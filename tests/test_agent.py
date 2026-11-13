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
    run = ask(
        "Which chain is cheapest?", scripted_model(("describe",), ("sql", GOOD_SQL), answer())
    )

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
    run = ask("Cheapest chain in Spain?", scripted_model(("describe",), refusal))

    assert not run.answer.answerable
    assert run.answer.sql is None
    assert run.data is None
    assert run.attempts == []
    assert run.error is None
