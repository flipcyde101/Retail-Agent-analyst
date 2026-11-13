"""The agent loop, tested without a real model and without the database.

FunctionModel replaces the LLM with a Python function that we script: "first call
describe_schema, then run this SQL, then answer". That makes the loop deterministic, free
and fast, so what is tested is our code: retries, state and the output validator.
"""

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agent import tools
from agent.agent import MAX_SQL_ATTEMPTS, ask
from agent.models import ColumnInfo, SchemaDescription, SqlResult, TableInfo
from agent.sql_guard import validate_sql

GOOD_SQL = "SELECT chain, avg(price) AS avg_price FROM prices GROUP BY chain LIMIT 5"
FAKE_ROWS = SqlResult(columns=["chain", "avg_price"], rows=[["Auchan", 2.5]], row_count=1)


@pytest.fixture(autouse=True)
def fake_database(monkeypatch):
    """Keep the real validation, replace only the trip to PostgreSQL."""

    def fake_run_sql(query: str) -> SqlResult:
        validate_sql(query)
        return FAKE_ROWS

    schema = SchemaDescription(
        tables=[
            TableInfo(
                name="prices",
                kind="table",
                columns=[ColumnInfo(name="price", type="numeric")],
            )
        ]
    )
    monkeypatch.setattr(tools, "run_sql", fake_run_sql)
    monkeypatch.setattr(tools, "describe_schema", lambda: schema)


def scripted_model(*steps) -> FunctionModel:
    """A fake model that plays the given steps in order, one per model request.

    Each step is ("describe",), ("sql", query) or ("answer", {...fields of AgentAnswer}).
    """
    remaining = list(steps)

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        kind, *args = remaining.pop(0)
        if kind == "describe":
            return ModelResponse(parts=[ToolCallPart("describe_schema", {})])
        if kind == "sql":
            return ModelResponse(parts=[ToolCallPart("run_sql", {"query": args[0]})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args[0])])

    return FunctionModel(respond)


def answer(**overrides) -> tuple:
    fields = {
        "answerable": True,
        "answer": "Auchan averages 2.50 EUR.",
        "sql": GOOD_SQL,
        "chart": "bar",
        "confidence": "high",
    }
    return ("answer", fields | overrides)


def test_happy_path_returns_the_answer_with_the_rows_of_its_query():
    steps = scripted_model(("describe",), ("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", steps, prompt_version="v3")

    assert run.answer.answerable
    assert run.answer.sql == GOOD_SQL
    assert run.data == FAKE_ROWS
    assert [a.ok for a in run.attempts] == [True]
    assert run.requests == 3
    assert run.error is None


def test_a_rejected_query_is_sent_back_and_the_model_corrects_it():
    without_limit = "SELECT chain, avg(price) AS avg_price FROM prices GROUP BY chain"
    seen_errors = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if len(messages) == 1:
            return ModelResponse(parts=[ToolCallPart("run_sql", {"query": without_limit})])
        # What the model sees after its tool call: the rows, or the error to fix.
        tool_reply = str(messages[-1].parts[0].content)
        if "LIMIT" in tool_reply:
            seen_errors.append(tool_reply)
            return ModelResponse(parts=[ToolCallPart("run_sql", {"query": GOOD_SQL})])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer()[1])])

    run = ask("Which chain is cheapest?", FunctionModel(respond))

    assert "LIMIT" in seen_errors[0]
    assert [a.ok for a in run.attempts] == [False, True]
    assert "LIMIT" in run.attempts[0].error
    assert run.answer.answerable
    assert run.data == FAKE_ROWS


def test_three_failed_queries_in_a_row_stop_the_run_without_an_invented_answer():
    bad = ("sql", "DELETE FROM prices")
    run = ask("Delete everything", scripted_model(*[bad] * MAX_SQL_ATTEMPTS, answer()))

    assert len(run.attempts) == MAX_SQL_ATTEMPTS
    assert not any(a.ok for a in run.attempts)
    assert not run.answer.answerable
    assert run.answer.sql is None
    assert run.data is None
    assert run.error is not None
    # The failed run still reports what it cost.
    assert run.requests == MAX_SQL_ATTEMPTS
    assert run.input_tokens > 0


def test_an_answer_whose_sql_was_never_run_is_rejected():
    invented = answer(sql="SELECT 42 LIMIT 1")
    run = ask("Which chain is cheapest?", scripted_model(invented, ("sql", GOOD_SQL), answer()))

    # First answer refused by the output validator; the model then runs a query for real.
    assert run.requests == 3
    assert run.answer.sql == GOOD_SQL
    assert run.data == FAKE_ROWS


def test_the_sql_of_the_answer_matches_ignoring_whitespace_and_semicolon():
    reformatted = answer(sql=GOOD_SQL.replace(" FROM", "\nFROM") + ";")
    run = ask("Which chain is cheapest?", scripted_model(("sql", GOOD_SQL), reformatted))

    assert run.data == FAKE_ROWS


def test_an_unanswerable_question_has_no_sql_and_no_data():
    refusal = answer(answerable=False, answer="There is no data for Spain.", sql=None, chart="none")
    run = ask("Cheapest chain in Spain?", scripted_model(("describe",), refusal), "v3")

    assert not run.answer.answerable
    assert run.answer.sql is None
    assert run.data is None
    assert run.attempts == []
    assert run.error is None


# --- day 7: what prompt v2 adds, each from a failure seen in the first measurement ---

DIRTY_SQL = GOOD_SQL + '"}'
REFUSAL = answer(answerable=False, answer="There is no data for Spain.", sql=None, chart="none")


def test_v2_removes_the_stray_quote_and_brace_after_the_sql():
    run = ask("Which chain is cheapest?", scripted_model(("sql", DIRTY_SQL), answer()))

    assert [(a.ok, a.repaired) for a in run.attempts] == [(True, True)]
    assert run.attempts[0].sql == GOOD_SQL
    assert run.data == FAKE_ROWS


def test_v1_still_fails_on_the_stray_characters_as_first_measured():
    steps = scripted_model(("sql", DIRTY_SQL), ("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", steps, prompt_version="v1")

    assert [(a.ok, a.repaired) for a in run.attempts] == [(False, False), (True, False)]
    assert run.prompt_version == "v1"


def test_v2_and_v3_reject_a_refusal_made_before_looking_at_the_database():
    steps = scripted_model(REFUSAL, ("describe",), REFUSAL)
    run = ask("Cheapest chain in Spain?", steps, prompt_version="v3")

    assert run.requests == 3  # the first refusal was sent back
    assert not run.answer.answerable
    assert run.error is None


def test_v1_accepts_that_premature_refusal():
    run = ask("Cheapest chain in Spain?", scripted_model(REFUSAL), prompt_version="v1")

    assert run.requests == 1


def test_v2_rejects_an_empty_answer():
    empty = answer(answer="  ")
    run = ask("Which chain is cheapest?", scripted_model(("sql", GOOD_SQL), empty, answer()))

    assert run.requests == 3
    assert run.answer.answer.strip()


def test_v2_gives_a_rejected_answer_more_than_one_chance():
    # Found measuring v2: two answers sent back in a row ended the run with the default of 1.
    empty = answer(answer="")
    steps = scripted_model(("sql", GOOD_SQL), empty, empty, answer())
    run = ask("Which chain is cheapest?", steps, prompt_version="v2")

    assert run.error is None
    assert run.requests == 4


FRENCH = answer(answer="Auchan est le moins cher, avec une moyenne de 2,50 EUR dans les magasins.")


def test_v3_tells_the_model_the_language_of_the_question():
    seen = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions)
        return ModelResponse(parts=[ToolCallPart("describe_schema", {})])

    ask("¿Qué cadena tiene los precios más baratos?", FunctionModel(respond))

    assert "answer in Spanish" in seen[0]


def test_v3_sends_back_an_answer_in_another_language_than_the_question():
    steps = scripted_model(("sql", GOOD_SQL), FRENCH, answer())
    run = ask("Which chain has the cheapest prices of them all?", steps)

    assert run.requests == 3
    assert run.answer.answer == "Auchan averages 2.50 EUR."


def test_v2_leaves_the_language_to_the_prompt():
    steps = scripted_model(("sql", GOOD_SQL), FRENCH)
    run = ask("Which chain has the cheapest prices of them all?", steps, prompt_version="v2")

    assert run.requests == 2
    assert "moins cher" in run.answer.answer


def test_a_question_too_short_to_tell_its_language_is_not_enforced():
    run = ask("Auchan?", scripted_model(("sql", GOOD_SQL), FRENCH))

    assert run.requests == 2


def test_each_version_sends_its_own_instructions():
    seen = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append(info.instructions)
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, REFUSAL[1])])

    ask("Anything?", FunctionModel(respond), prompt_version="v1")

    assert "Language:" not in seen[0]
    assert "retail price analyst" in seen[0]


# --- v4: the schema is in the instructions, so no model call is spent fetching it ---


def test_v4_puts_the_schema_in_the_instructions_and_hides_the_tool():
    seen = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append((info.instructions, [tool.name for tool in info.function_tools]))
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, REFUSAL[1])])

    run = ask("Cheapest chain in Spain?", FunctionModel(respond), prompt_version="v4")

    instructions, tool_names = seen[0]
    assert "table prices" in instructions
    assert "price numeric" in instructions
    assert tool_names == ["run_sql"]
    # The schema was in front of the model, so a refusal with no tool call is accepted.
    assert run.requests == 1
    assert run.error is None


def test_v3_still_offers_describe_schema_and_keeps_the_schema_out_of_the_instructions():
    seen = []

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.append((info.instructions, [tool.name for tool in info.function_tools]))
        return ModelResponse(parts=[ToolCallPart("describe_schema", {})])

    ask("Anything at all?", FunctionModel(respond), prompt_version="v3")

    instructions, tool_names = seen[0]
    assert "Database schema:" not in instructions
    assert sorted(tool_names) == ["describe_schema", "run_sql"]


# --- a database that is down, and the language of the fallback answer ---


def test_a_database_that_is_down_ends_the_run_at_once_instead_of_being_retried(monkeypatch):
    def unreachable(query: str) -> SqlResult:
        raise tools.DatabaseUnavailableError("Cannot connect to the database: refused")

    monkeypatch.setattr(tools, "run_sql", unreachable)
    run = ask("Which chain is cheapest?", scripted_model(("sql", GOOD_SQL), answer()))

    # One model call: the error was not sent back to the model as a query to fix.
    assert run.requests == 1
    assert run.attempts == []
    assert "DatabaseUnavailableError" in run.error
    assert not run.answer.answerable


def test_the_fallback_answer_is_in_the_language_of_the_question():
    bad = ("sql", "DELETE FROM prices")
    run = ask("¿Qué cadena tiene los precios más baratos?", scripted_model(*[bad] * 3))

    assert run.error is not None
    assert run.answer.answer.startswith("El agente no ha podido")


def test_an_unknown_prompt_version_is_an_error():
    with pytest.raises(ValueError, match="v9"):
        ask("Anything?", scripted_model(REFUSAL), prompt_version="v9")
