"""The same agent written as a LangGraph graph, to compare the two ways of running the loop.

What stays the same as agent/agent.py: the instructions, the tools, the checks on the final
answer, the limits, and the requests the model receives. They were captured from PydanticAI
and are pinned by tests/test_graph_agent.py, which sends the same scripted conversation
through both versions and compares the requests: one system block, the tools with
`tool_choice` auto, the answer's JSON schema as Anthropic's native structured output, tool
results and retry messages worded as PydanticAI words them, and the same retry rules.

What changes: the loop is an explicit graph, and the model is called with the Anthropic SDK
directly instead of through PydanticAI.

    START -> model --(tool calls)--> tools -> model -> ...
               |
               +--(text, or nothing)--> check --(rejected: feedback)--> model
                                          |
                                          +--(accepted)--> END

Every node can also end the run by setting `error`, which routes straight to END.
"""

import inspect
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal, TypedDict

import anthropic
from dotenv import load_dotenv
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import ValidationError
from pydantic_ai import ModelRetry
from pydantic_ai import _framing as pydantic_ai_framing
from pydantic_ai import messages as pydantic_ai_messages

from agent import agent as base
from agent.agent import (
    FALLBACK_ANSWERS,
    MAX_MODEL_REQUESTS,
    MAX_SQL_ATTEMPTS,
    PROMPT_VERSION,
    STRICT_OUTPUT_RETRIES,
    AgentAnswer,
    AgentRun,
    RunState,
    check_answer,
    default_model,
    instruction_text,
    read_schema,
    run_query,
    schema_text,
)
from agent.db import ROOT
from agent.language import detect_language
from agent.prompts import PROMPTS
from agent.tools import DatabaseUnavailableError

# What PydanticAI sends for this model: the largest output, and the timeout of its HTTP
# client (the SDK refuses a large max_tokens without streaming unless a timeout is given).
MAX_TOKENS = 128_000
# The SDK's own Timeout: it is built on httpx2 and refuses an httpx one.
TIMEOUT = anthropic.Timeout(600, connect=5)
# PydanticAI's defaults when an agent sets no retries: the output of v1, describe_schema.
DEFAULT_OUTPUT_RETRIES = 1
DEFAULT_TOOL_RETRIES = 1
TOOL_RETRIES = {"run_sql": MAX_SQL_ATTEMPTS - 1}
# Each turn is two or three steps of the graph; the counters above stop the run well
# before this LangGraph limit is reached, so hitting it would be a bug in the graph.
RECURSION_LIMIT = 100

# AgentAnswer as PydanticAI sends it in output_config, copied from a captured request.
ANSWER_FORMAT = {
    "format": {
        "type": "json_schema",
        "schema": {
            "type": "object",
            "properties": {
                "answerable": {
                    "type": "boolean",
                    "description": AgentAnswer.model_fields["answerable"].description,
                },
                "answer": {
                    "type": "string",
                    "description": AgentAnswer.model_fields["answer"].description,
                },
                "sql": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "description": f"{AgentAnswer.model_fields['sql'].description}"
                    "\n\n{default: None}",
                },
                "chart": {
                    "enum": ["none", "table", "bar", "line"],
                    "description": AgentAnswer.model_fields["chart"].description,
                },
                "confidence": {"enum": ["high", "medium", "low"]},
            },
            "additionalProperties": False,
            "required": ["answerable", "answer", "chart", "confidence"],
        },
    }
}

# The tools as PydanticAI describes them: the docstring of each tool function.
RUN_SQL_TOOL = {
    "name": "run_sql",
    "description": inspect.cleandoc(base.run_sql.__doc__ or ""),
    "input_schema": {
        "additionalProperties": False,
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
        "type": "object",
    },
}
DESCRIBE_SCHEMA_TOOL = {
    "name": "describe_schema",
    "description": inspect.cleandoc(base.describe_schema.__doc__ or ""),
    "input_schema": {"additionalProperties": False, "properties": {}, "type": "object"},
}

# The messages from the application itself are PydanticAI's, built with its own functions
# (private ones: the wording is not public API, and copying it would drift). Found by the
# comparison test, not documented anywhere: from the first request that carries such a
# message, PydanticAI also puts in front of the instructions a block that tells the model
# how to read them, and keeps it there for the rest of the run.
NO_OUTPUT_FEEDBACK = pydantic_ai_framing.system_frame(
    pydantic_ai_messages._no_output_feedback_text(
        [
            pydantic_ai_messages._NO_OUTPUT_RETURN_TEXT,
            pydantic_ai_messages._NO_OUTPUT_USE_OUTPUT_TOOL,
        ]
    ),
    sealed=True,
)
FRAMING_ANNOUNCEMENT = pydantic_ai_framing.lookalike_note_announcement(
    system_role=False, sealed=True
)


@dataclass
class Context:
    """What every node needs and the graph does not change: the run's state and settings."""

    run: RunState
    model_id: str  # the model as the Anthropic API names it, without the provider prefix
    output_retries: int


class GraphState(TypedDict):
    """What moves through the graph. Each node returns the keys it changes."""

    messages: list[dict[str, Any]]
    stop_reason: str | None  # of the last response
    answer: AgentAnswer | None
    error: str | None
    requests: int
    input_tokens: int
    output_tokens: int
    # Failures per tool name, counted once per turn and reset by a turn where the tool only
    # worked: PydanticAI's rule, which is what "3 failed queries in a row" means there.
    tool_retries: dict[str, int]
    output_retries: int  # final answers sent back so far
    announced: bool  # whether the application has spoken, so FRAMING_ANNOUNCEMENT is sent


@cache
def _client() -> anthropic.Anthropic:
    """The SDK client, built once per process like the PydanticAI model object."""
    load_dotenv(ROOT / ".env")
    return anthropic.Anthropic()


def _blocks(message: dict[str, Any], kind: str) -> list[Any]:
    content = message["content"]
    return [block for block in content if getattr(block, "type", None) == kind]


def _text(content: str) -> list[dict[str, str]]:
    return [{"text": content, "type": "text"}]


def _with_user_turn(messages: list[dict[str, Any]], content: list[dict]) -> list[dict]:
    # A turn of its own even after another user turn: PydanticAI does not merge them either.
    return [*messages, {"role": "user", "content": content}]


def _feedback(problem: ValidationError | ModelRetry) -> str:
    """A rejected answer explained the way PydanticAI explains it to the model."""
    if isinstance(problem, ModelRetry):
        content, tag = problem.message, pydantic_ai_messages.RETRY_FEEDBACK_TAG
    else:
        errors = problem.errors(include_url=False, include_context=False)
        details = pydantic_ai_messages._dump_error_details(errors)
        content = pydantic_ai_messages.tool_return_ta.dump_json(details, by_alias=True).decode()
        tag = pydantic_ai_messages.VALIDATION_ERRORS_TAG
    return pydantic_ai_messages._fenced_retry_feedback(content, tag=tag)


# --- nodes ----------------------------------------------------------------------------------


def call_model(state: GraphState, runtime: Runtime[Context]) -> dict[str, Any]:
    """One request to the model with the whole conversation so far."""
    context = runtime.context
    if state["requests"] >= MAX_MODEL_REQUESTS:
        return {
            "error": f"UsageLimitExceeded: The next request would exceed the request_limit of "
            f"{MAX_MODEL_REQUESTS}"
        }
    tools = [RUN_SQL_TOOL] if context.run.inline_schema else [DESCRIBE_SCHEMA_TOOL, RUN_SQL_TOOL]
    system = [{"type": "text", "text": instruction_text(context.run)}]
    if state["announced"]:
        system.insert(0, {"type": "text", "text": FRAMING_ANNOUNCEMENT})
    response = _client().messages.create(
        model=context.model_id,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=state["messages"],
        tools=tools,
        tool_choice={"type": "auto"},
        output_config=ANSWER_FORMAT,
        timeout=TIMEOUT,
    )
    usage = response.usage
    update: dict[str, Any] = {
        "stop_reason": response.stop_reason,
        "requests": state["requests"] + 1,
        "input_tokens": state["input_tokens"]
        + usage.input_tokens
        + (usage.cache_creation_input_tokens or 0)
        + (usage.cache_read_input_tokens or 0),
        "output_tokens": state["output_tokens"] + usage.output_tokens,
    }
    has_output = any(
        (block.type == "text" and block.text) or block.type == "tool_use"
        for block in response.content
    )
    if has_output:
        # The content goes back as it came, thinking blocks included: the API wants them.
        message = {"role": "assistant", "content": response.content}
        update["messages"] = [*state["messages"], message]
    # A response with nothing in it is not kept: check() asks again, as PydanticAI does.
    return update


def _run_one(run: RunState, call: Any) -> tuple[str, bool]:
    """Run one tool call: (what goes back to the model, whether it is an error)."""
    try:
        if call.name == "run_sql":
            result = run_query(run, str(call.input.get("query", "")))
        elif call.name == "describe_schema" and not run.inline_schema:
            result = read_schema(run)
        else:
            raise ModelRetry(f"Unknown tool name: {call.name!r}.")
    except ModelRetry as retry:
        return retry.message, True
    return result.model_dump_json(), False


def run_tools(state: GraphState, runtime: Runtime[Context]) -> dict[str, Any]:
    """Run every tool the model called and send back the results, or the errors to fix."""
    run = runtime.context.run
    calls = _blocks(state["messages"][-1], "tool_use")
    # Several calls in one response run at the same time, as PydanticAI runs them.
    with ThreadPoolExecutor(max_workers=len(calls)) as pool:
        outcomes = list(pool.map(lambda call: _run_one(run, call), calls))

    retries = state["tool_retries"]
    failed = {call.name for call, (_, error) in zip(calls, outcomes, strict=True) if error}
    worked = {call.name for call, (_, error) in zip(calls, outcomes, strict=True) if not error}
    for name in sorted(failed):
        limit = TOOL_RETRIES.get(name, DEFAULT_TOOL_RETRIES)
        if retries.get(name, 0) >= limit:
            return {
                "error": f"UnexpectedModelBehavior: Tool {name!r} exceeded max retries "
                f"count of {limit}"
            }
    new_retries = {name: count for name, count in retries.items() if name not in worked}
    new_retries.update({name: retries.get(name, 0) + 1 for name in failed})

    results = [
        {
            "tool_use_id": call.id,
            "type": "tool_result",
            "content": _text(content),
            "is_error": error,
        }
        for call, (content, error) in zip(calls, outcomes, strict=True)
    ]
    return {
        "messages": _with_user_turn(state["messages"], results),
        "tool_retries": new_retries,
    }


def check(state: GraphState, runtime: Runtime[Context]) -> dict[str, Any]:
    """Validate the final answer; a rejected one goes back to the model with the reason."""
    context = runtime.context
    last = state["messages"][-1]
    text = "".join(b.text for b in _blocks(last, "text")) if last["role"] == "assistant" else ""
    stop_reason = state["stop_reason"]
    if not text:
        # Nothing came back. Asking again would only repeat a refusal or another cut-off.
        if stop_reason == "max_tokens":
            return {"error": f"OutputTokenLimitExceeded: model token limit ({MAX_TOKENS}) reached"}
        if stop_reason == "refusal":
            return {"error": "ContentFilterError: the model refused to answer"}
        feedback = NO_OUTPUT_FEEDBACK
        problem = "the response had no text and no tool call"
    else:
        try:
            answer = check_answer(context.run, AgentAnswer.model_validate_json(text))
        except (ValidationError, ModelRetry) as rejected:
            if stop_reason == "max_tokens" and isinstance(rejected, ValidationError):
                return {"error": "OutputTokenLimitExceeded: the answer was cut off"}
            feedback = _feedback(rejected)
            problem = rejected.message if isinstance(rejected, ModelRetry) else str(rejected)
        else:
            return {"answer": answer}

    retries = state["output_retries"] + 1
    if retries > context.output_retries:
        return {
            "error": f"UnexpectedModelBehavior: Exceeded maximum output retries "
            f"({context.output_retries}), the last time because: {problem}"
        }
    return {
        "messages": _with_user_turn(state["messages"], _text(feedback)),
        "output_retries": retries,
        "announced": True,
    }


# --- edges ----------------------------------------------------------------------------------


def after_model(state: GraphState) -> Literal["tools", "check", "__end__"]:
    if state["error"]:
        return END
    last = state["messages"][-1]
    if last["role"] == "assistant" and _blocks(last, "tool_use"):
        return "tools"
    return "check"


def after_tools(state: GraphState) -> Literal["model", "__end__"]:
    return END if state["error"] else "model"


def after_check(state: GraphState) -> Literal["model", "__end__"]:
    return END if state["error"] or state["answer"] else "model"


def build_graph():
    graph = StateGraph(GraphState, context_schema=Context)
    graph.add_node("model", call_model)
    graph.add_node("tools", run_tools)
    graph.add_node("check", check)
    graph.add_edge(START, "model")
    graph.add_conditional_edges("model", after_model)
    graph.add_conditional_edges("tools", after_tools)
    graph.add_conditional_edges("check", after_check)
    return graph.compile()


GRAPH = build_graph()


# --- entry points, with the signatures of agent.agent ---------------------------------------


def _model_id(model: str) -> str:
    provider, _, name = model.partition(":")
    if provider != "anthropic" or not name:
        raise ValueError(f"The LangGraph version calls the Anthropic API only; got {model!r}.")
    return name


def warm_up(model: str | None = None, prompt_version: str = PROMPT_VERSION) -> None:
    """Load the SDK client and the schema before the first question, as agent.warm_up does."""
    _model_id(model or default_model())
    _client()
    if RunState(prompt_version=prompt_version).inline_schema:
        schema_text()


def ask(question: str, model: str | None = None, prompt_version: str = PROMPT_VERSION) -> AgentRun:
    """Answer one question; same contract as agent.agent.ask.

    Never raises for a model that gives up or a database that is down: the run comes back
    with `error` set and an answer that says so. An error of the model API still raises.
    """
    if prompt_version not in PROMPTS:
        raise ValueError(f"Unknown prompt version {prompt_version!r}; have {sorted(PROMPTS)}")
    load_dotenv(ROOT / ".env")
    model = model or default_model()
    run = RunState(prompt_version=prompt_version, question_language=detect_language(question))
    context = Context(
        run=run,
        model_id=_model_id(model),
        output_retries=STRICT_OUTPUT_RETRIES if run.strict else DEFAULT_OUTPUT_RETRIES,
    )
    start: GraphState = {
        "messages": [{"role": "user", "content": _text(question)}],
        "stop_reason": None,
        "answer": None,
        "error": None,
        "requests": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "tool_retries": {},
        "output_retries": 0,
        "announced": False,
    }
    started = time.perf_counter()
    final = start
    try:
        # Streamed rather than invoked to keep the state after each step: if a step raises,
        # what the run had already cost is still known.
        for step in GRAPH.stream(
            start, {"recursion_limit": RECURSION_LIMIT}, context=context, stream_mode="values"
        ):
            final = step
    except DatabaseUnavailableError as failure:
        final = final | {"error": f"{type(failure).__name__}: {failure}"}

    answer = final["answer"]
    if answer is None:
        answer = AgentAnswer(
            answerable=False,
            answer=FALLBACK_ANSWERS[run.required_language or "en"],
            chart="none",
            confidence="low",
        )
    return AgentRun(
        question=question,
        answer=answer,
        data=run.results.get(base._normalise(answer.sql)) if answer.sql else None,
        attempts=run.attempts,
        error=final["error"],
        model=model,
        prompt_version=prompt_version,
        input_tokens=final["input_tokens"],
        output_tokens=final["output_tokens"],
        requests=final["requests"],
        seconds=round(time.perf_counter() - started, 2),
    )
