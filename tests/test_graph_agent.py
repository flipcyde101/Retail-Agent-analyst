"""The LangGraph version of the agent, tested like the PydanticAI one: no model, no database.

A fake Anthropic client plays scripted responses, built as the SDK's own objects, one per
model call, and records what it was sent. Most checks repeat test_agent.py. The last ones
send the same scripted conversation through both versions and compare the requests the
model would receive, since the comparison between the two loops means something only if
the model sees the same thing in both.
"""

import json
from typing import Any

import httpx2
import pytest
from anthropic import Anthropic, AsyncAnthropic
from anthropic.types import Message
from anthropic.types.beta import BetaMessage
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from agent import agent as agent_module
from agent import graph_agent, tools
from agent.agent import MAX_MODEL_REQUESTS, MAX_SQL_ATTEMPTS, RunState, instruction_text
from agent.graph_agent import ANSWER_FORMAT, NO_OUTPUT_FEEDBACK, ask
from agent.models import ColumnInfo, SchemaDescription, SqlResult, TableInfo
from agent.sql_guard import validate_sql

GOOD_SQL = "SELECT chain, avg(price) AS avg_price FROM prices GROUP BY chain LIMIT 5"
BAD_SQL = "DELETE FROM prices"
FAKE_ROWS = SqlResult(columns=["chain", "avg_price"], rows=[["Auchan", 2.5]], row_count=1)
MODEL = "anthropic:claude-haiku-5-5"


@pytest.fixture(autouse=True)
def fake_database(monkeypatch):
    """Keep the real validation, replace only the trip to PostgreSQL."""

    def fake_run_sql(query: str) -> SqlResult:
        validate_sql(query)
        return FAKE_ROWS

    schema = SchemaDescription(
        tables=[
            TableInfo(
                name="prices", kind="table", columns=[ColumnInfo(name="price", type="numeric")]
            )
        ]
    )
    monkeypatch.setattr(tools, "run_sql", fake_run_sql)
    monkeypatch.setattr(tools, "describe_schema", lambda: schema)


# --- scripted responses ----------------------------------------------------------------------


def response(step: tuple, number: int, beta: bool = False) -> Message | BetaMessage:
    """The SDK object for one scripted step.

    A step is ("describe",), ("sql", query), ("sqls", [query, ...]) for several calls in one
    response, ("answer", {...fields}), ("text", raw), ("empty",), ("refusal",), or
    ("cut", raw) for an answer stopped by max_tokens.
    """
    kind, *args = step

    def call(index: int, name: str, arguments: dict) -> dict:
        return {
            "type": "tool_use",
            "id": f"toolu_{number}_{index}",
            "name": name,
            "input": arguments,
        }

    stop = "tool_use"
    if kind == "describe":
        content = [call(0, "describe_schema", {})]
    elif kind == "sql":
        content = [call(0, "run_sql", {"query": args[0]})]
    elif kind == "sqls":
        content = [call(i, "run_sql", {"query": q}) for i, q in enumerate(args[0])]
    elif kind == "answer":
        content, stop = [{"type": "text", "text": json.dumps(args[0])}], "end_turn"
    elif kind in ("text", "cut"):
        content = [{"type": "text", "text": args[0]}]
        stop = "max_tokens" if kind == "cut" else "end_turn"
    else:
        content, stop = [], "refusal" if kind == "refusal" else "end_turn"
    data = {
        "id": f"msg_{number}",
        "type": "message",
        "role": "assistant",
        "model": "claude-haiku-5-5",
        "content": content,
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 10},
    }
    return (BetaMessage if beta else Message).model_validate(data)


def plain(value: Any) -> Any:
    """SDK objects and nested containers as plain JSON data, to compare and inspect."""
    if hasattr(value, "model_dump"):
        return plain(value.model_dump(mode="json", exclude_none=True))
    if isinstance(value, dict):
        return {key: plain(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [plain(item) for item in value]
    return value


class FakeClient:
    """The graph's client: plays one step per call of messages.create, keeps what was sent."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls: list[dict] = []

    @property
    def messages(self):
        return self

    def create(self, **request):
        self.calls.append(plain(request))
        return response(self.steps.pop(0), len(self.calls))


@pytest.fixture
def scripted(monkeypatch):
    """Install a FakeClient with the given steps and return it."""

    def install(*steps) -> FakeClient:
        client = FakeClient(*steps)
        monkeypatch.setattr(graph_agent, "_client", lambda: client)
        return client

    return install


def answer(**overrides) -> tuple:
    fields = {
        "answerable": True,
        "answer": "Auchan averages 2.50 EUR.",
        "sql": GOOD_SQL,
        "chart": "bar",
        "confidence": "high",
    }
    return ("answer", fields | overrides)


REFUSAL = answer(answerable=False, answer="The data has no sales.", sql=None, chart="none")
FRENCH = answer(answer="Auchan est le moins cher, avec une moyenne de 2,50 EUR dans les magasins.")


def last_user_content(request: dict) -> list[dict]:
    return request["messages"][-1]["content"]


def last_user_text(request: dict) -> str:
    return "".join(block.get("text", "") for block in last_user_content(request))


# --- the same behaviour as the PydanticAI version --------------------------------------------


def test_happy_path_returns_the_answer_with_the_rows_of_its_query(scripted):
    scripted(("describe",), ("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", MODEL, prompt_version="v3")

    assert run.answer.answerable
    assert run.answer.sql == GOOD_SQL
    assert run.data == FAKE_ROWS
    assert [a.ok for a in run.attempts] == [True]
    assert run.requests == 3
    assert (run.input_tokens, run.output_tokens) == (300, 30)
    assert run.error is None
    assert run.model == MODEL


def test_a_rejected_query_is_sent_back_and_the_model_corrects_it(scripted):
    without_limit = "SELECT chain, avg(price) AS avg_price FROM prices GROUP BY chain"
    client = scripted(("sql", without_limit), ("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", MODEL)

    error = last_user_content(client.calls[1])[0]
    assert error["is_error"] is True
    assert error["tool_use_id"] == "toolu_1_0"
    # The error alone, as PydanticAI sends it: no advice added after it.
    assert error["content"] == [{"text": run.attempts[0].error, "type": "text"}]
    rows = last_user_content(client.calls[2])[0]
    assert rows["is_error"] is False
    assert json.loads(rows["content"][0]["text"]) == FAKE_ROWS.model_dump()
    assert [a.ok for a in run.attempts] == [False, True]
    assert run.data == FAKE_ROWS


def test_three_failed_queries_in_a_row_stop_the_run_without_an_invented_answer(scripted):
    scripted(*[("sql", BAD_SQL)] * MAX_SQL_ATTEMPTS, answer())
    run = ask("Delete everything", MODEL)

    assert len(run.attempts) == MAX_SQL_ATTEMPTS
    assert not any(a.ok for a in run.attempts)
    assert not run.answer.answerable
    assert run.answer.sql is None
    assert run.data is None
    assert "exceeded max retries" in run.error
    # The failed run still reports what it cost.
    assert run.requests == MAX_SQL_ATTEMPTS
    assert run.input_tokens > 0


def test_a_turn_where_the_query_only_worked_resets_the_count_of_failures(scripted):
    bad, good = ("sql", BAD_SQL), ("sql", GOOD_SQL)
    scripted(bad, bad, good, bad, bad, good, answer())
    run = ask("Which chain is cheapest?", MODEL)

    assert run.error is None
    assert [a.ok for a in run.attempts] == [False, False, True, False, False, True]


def test_several_failed_queries_in_one_turn_count_as_one_failure(scripted):
    # PydanticAI counts a tool's failures per turn, not per call.
    scripted(("sqls", [BAD_SQL, BAD_SQL, BAD_SQL]), ("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", MODEL)

    assert run.error is None
    assert run.requests == 3
    assert [a.ok for a in run.attempts] == [False, False, False, True]


def test_a_turn_with_a_failure_and_a_success_still_counts_the_failure(scripted):
    scripted(("sql", BAD_SQL), ("sqls", [BAD_SQL, GOOD_SQL]), ("sql", BAD_SQL), answer())
    run = ask("Which chain is cheapest?", MODEL)

    assert run.requests == 3
    assert "exceeded max retries" in run.error


def test_every_call_of_a_turn_gets_its_result_in_order(scripted):
    client = scripted(("sqls", [BAD_SQL, GOOD_SQL]), answer())
    ask("Which chain is cheapest?", MODEL)

    results = last_user_content(client.calls[1])
    assert [(r["tool_use_id"], r["is_error"]) for r in results] == [
        ("toolu_1_0", True),
        ("toolu_1_1", False),
    ]


def test_an_answer_whose_sql_was_never_run_is_rejected(scripted):
    client = scripted(answer(sql="SELECT 42 LIMIT 1"), ("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", MODEL)

    feedback = last_user_text(client.calls[1])
    assert "<pyai:retry_feedback>" in feedback
    assert "must be exactly a query you ran" in feedback
    assert run.requests == 3
    assert run.answer.sql == GOOD_SQL


def test_text_that_is_not_the_answer_format_is_sent_back_with_the_errors(scripted):
    client = scripted(("text", "Auchan, I think."), ("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", MODEL)

    assert "<pyai:validation_errors>" in last_user_text(client.calls[1])
    assert run.error is None
    assert run.answer.sql == GOOD_SQL


def test_an_empty_response_is_dropped_and_the_model_asked_again(scripted):
    client = scripted(("sql", GOOD_SQL), ("empty",), answer())
    run = ask("Which chain is cheapest?", MODEL)

    assert run.error is None
    assert run.requests == 3
    retry = client.calls[2]["messages"]
    # No empty assistant turn: the feedback follows the tool results in a turn of its own.
    assert [m["role"] for m in retry] == ["user", "assistant", "user", "user"]
    assert last_user_content(client.calls[2]) == [{"text": NO_OUTPUT_FEEDBACK, "type": "text"}]


def test_a_refusal_ends_the_run_and_keeps_what_it_cost(scripted):
    scripted(("refusal",))
    run = ask("Which chain is cheapest?", MODEL)

    assert "ContentFilterError" in run.error
    assert (run.requests, run.input_tokens) == (1, 100)
    assert not run.answer.answerable


def test_an_answer_cut_off_by_the_token_limit_ends_the_run(scripted):
    scripted(("sql", GOOD_SQL), ("cut", '{"answerable": true, "answer": "Auch'))
    run = ask("Which chain is cheapest?", MODEL)

    assert "OutputTokenLimitExceeded" in run.error
    assert run.requests == 2


def test_too_many_rejected_answers_end_the_run(scripted):
    empty = answer(answer="")
    scripted(("sql", GOOD_SQL), *[empty] * 4)
    run = ask("Which chain is cheapest?", MODEL)

    # Three chances after the first rejection, as STRICT_OUTPUT_RETRIES in agent.py.
    assert run.requests == 5
    assert "Exceeded maximum output retries" in run.error
    assert not run.answer.answerable


def test_v1_gives_a_rejected_answer_only_one_more_chance(scripted):
    invented = answer(sql="SELECT 42 LIMIT 1")
    scripted(invented, invented, answer())
    run = ask("Which chain is cheapest?", MODEL, prompt_version="v1")

    assert run.requests == 2
    assert run.error is not None


def test_the_run_stops_at_the_limit_of_model_calls(scripted):
    scripted(*[("sql", GOOD_SQL)] * (MAX_MODEL_REQUESTS + 1))
    run = ask("Which chain is cheapest?", MODEL)

    assert run.requests == MAX_MODEL_REQUESTS
    assert "UsageLimitExceeded" in run.error
    assert not run.answer.answerable


def test_an_answer_in_another_language_than_the_question_is_sent_back(scripted):
    scripted(("sql", GOOD_SQL), FRENCH, answer())
    run = ask("Which chain has the cheapest prices of them all?", MODEL)

    assert run.requests == 3
    assert run.answer.answer == "Auchan averages 2.50 EUR."


def test_a_refusal_before_looking_at_the_schema_is_sent_back_in_v3(scripted):
    scripted(REFUSAL, ("describe",), REFUSAL)
    run = ask("How many jars were sold?", MODEL, prompt_version="v3")

    assert run.requests == 3
    assert run.error is None
    assert not run.answer.answerable
    assert run.answer.sql is None


def test_a_database_that_is_down_ends_the_run_and_keeps_what_it_cost(scripted, monkeypatch):
    def unreachable(query: str) -> SqlResult:
        raise tools.DatabaseUnavailableError("Cannot connect to the database: refused")

    monkeypatch.setattr(tools, "run_sql", unreachable)
    scripted(("sql", GOOD_SQL), answer())
    run = ask("Which chain is cheapest?", MODEL)

    assert (run.requests, run.input_tokens) == (1, 100)
    assert run.attempts == []
    assert "DatabaseUnavailableError" in run.error
    assert not run.answer.answerable


def test_the_fallback_answer_is_in_the_language_of_the_question(scripted):
    scripted(*[("sql", BAD_SQL)] * MAX_SQL_ATTEMPTS)
    run = ask("¿Qué cadena tiene los precios más baratos?", MODEL)

    assert run.answer.answer.startswith("El agente no ha podido")


def test_v5_sends_the_instructions_the_run_sql_tool_and_the_answer_format(scripted):
    client = scripted(REFUSAL)
    question = "Which chain has the cheapest prices of them all?"
    ask(question, MODEL)

    request = client.calls[0]
    expected = instruction_text(RunState(prompt_version="v5", question_language="en"))
    assert request["system"] == [{"type": "text", "text": expected}]
    assert request["model"] == "claude-haiku-5-5"
    assert [tool["name"] for tool in request["tools"]] == ["run_sql"]
    assert request["tool_choice"] == {"type": "auto"}
    assert request["output_config"] == ANSWER_FORMAT
    assert request["messages"] == [
        {"role": "user", "content": [{"text": question, "type": "text"}]}
    ]


def test_v3_also_offers_describe_schema(scripted):
    client = scripted(("describe",), REFUSAL)
    ask("Anything at all?", MODEL, prompt_version="v3")

    names = sorted(tool["name"] for tool in client.calls[0]["tools"])
    assert names == ["describe_schema", "run_sql"]


def test_only_anthropic_models_are_accepted(scripted):
    scripted(REFUSAL)
    with pytest.raises(ValueError, match="Anthropic"):
        ask("Anything?", "openai:gpt-whatever")


def test_the_real_sdk_accepts_the_requests_and_reads_the_responses(monkeypatch):
    # Found by the first real run: a fake client accepts anything, so it let through a
    # timeout the SDK refuses. Here the real SDK builds the request and parses the answer;
    # only the network is replaced.
    steps = [("sql", GOOD_SQL), answer()]
    sent = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content))
        return httpx2.Response(200, json=plain(response(steps[len(sent) - 1], len(sent))))

    client = Anthropic(
        api_key="not-used", http_client=httpx2.Client(transport=httpx2.MockTransport(handler))
    )
    monkeypatch.setattr(graph_agent, "_client", lambda: client)
    run = ask("Which chain is cheapest?", MODEL)

    assert run.error is None
    assert run.answer.sql == GOOD_SQL
    assert len(sent) == 2
    assert sent[1]["messages"][1]["content"][0]["type"] == "tool_use"


def test_the_graph_has_the_three_nodes_of_the_loop():
    nodes = set(graph_agent.GRAPH.get_graph().nodes)
    assert nodes == {"__start__", "model", "tools", "check", "__end__"}


# --- both versions, one scripted conversation: the model must see the same requests ----------

COMPARED = ("model", "max_tokens", "system", "tools", "tool_choice", "output_config", "messages")


def requests_of_pydantic_ai(steps: list[tuple], question: str, version: str):
    """Run the PydanticAI agent against the scripted responses; return its requests and run."""
    client = AsyncAnthropic(api_key="not-used")
    sent: list[dict] = []

    async def create(**request):
        sent.append(plain(request))
        return response(steps[len(sent) - 1], len(sent), beta=True)

    client.beta.messages.create = create
    model = AnthropicModel("claude-haiku-5-5", provider=AnthropicProvider(anthropic_client=client))
    run = agent_module.ask(question, model, prompt_version=version)
    return [{key: request.get(key) for key in COMPARED} for request in sent], run


def requests_of_langgraph(steps: list[tuple], question: str, version: str, monkeypatch):
    client = FakeClient(*steps)
    monkeypatch.setattr(graph_agent, "_client", lambda: client)
    run = ask(question, MODEL, prompt_version=version)
    return [{key: request.get(key) for key in COMPARED} for request in client.calls], run


CONVERSATIONS = {
    "a query fixed, an answer with an unrun query sent back": [
        ("sql", "SELECT chain, avg(price) AS avg_price FROM prices GROUP BY chain"),
        ("sql", GOOD_SQL),
        answer(sql="SELECT 42 LIMIT 1"),
        answer(),
    ],
    "text that is not the format, then the answer": [
        ("sql", GOOD_SQL),
        ("text", "Auchan, I think."),
        answer(),
    ],
    "two queries in one turn, one of them rejected": [
        ("sqls", [BAD_SQL, GOOD_SQL]),
        answer(),
    ],
    "an answer in the wrong language": [("sql", GOOD_SQL), FRENCH, answer()],
    "an empty response": [("sql", GOOD_SQL), ("empty",), answer()],
    "three failed turns": [("sql", BAD_SQL)] * MAX_SQL_ATTEMPTS,
}


@pytest.mark.parametrize("name", list(CONVERSATIONS))
def test_both_versions_send_the_model_the_same_requests(name, monkeypatch):
    steps = CONVERSATIONS[name]
    question = "Which chain has the cheapest prices of them all?"
    expected, pydantic_run = requests_of_pydantic_ai(steps, question, "v5")
    actual, graph_run = requests_of_langgraph(steps, question, "v5", monkeypatch)

    assert len(actual) == len(expected)
    for number, (ours, theirs) in enumerate(zip(actual, expected, strict=True), start=1):
        for key in COMPARED:
            assert ours[key] == theirs[key], f"request {number}, {key}"
    assert graph_run.requests == pydantic_run.requests
    assert graph_run.answer == pydantic_run.answer
    assert graph_run.attempts == pydantic_run.attempts
    assert (graph_run.error is None) == (pydantic_run.error is None)


def test_both_versions_send_v3_the_same_first_request(monkeypatch):
    steps = [("describe",), ("sql", GOOD_SQL), answer()]
    expected, _ = requests_of_pydantic_ai(steps, "Which chain is cheapest?", "v3")
    actual, _ = requests_of_langgraph(steps, "Which chain is cheapest?", "v3", monkeypatch)

    assert actual == expected
