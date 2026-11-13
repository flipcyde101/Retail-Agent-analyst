"""The agent: a PydanticAI loop around the two tools, with a validated final answer.

What PydanticAI does for us: it sends the question, the instructions and the JSON schema of
each tool to the model; when the model answers "call run_sql with this query" it runs our
function and sends the result back; and it repeats until the model returns the final answer,
which it validates against AgentAnswer. We write the tools, the instructions and the rules.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model
from pydantic_ai.usage import RunUsage, UsageLimits

from agent import tools
from agent.db import ROOT
from agent.models import SchemaDescription, SqlResult
from agent.sql_guard import SqlValidationError
from agent.tools import SqlExecutionError

PROMPT_VERSION = "v1"

# A failed query can be corrected twice: 3 attempts in a row, then the run stops.
MAX_SQL_ATTEMPTS = 3
# Hard ceiling on model calls per question, so a confused model cannot loop (and bill) forever.
MAX_MODEL_REQUESTS = 12

INSTRUCTIONS = """\
You are a retail price analyst. You answer questions about supermarket prices using a
PostgreSQL database, and you never state a number that did not come from a query you ran.

How to work:
1. Call describe_schema first to see the tables, columns and their comments.
2. Write one PostgreSQL SELECT and run it with run_sql. Every query needs a LIMIT (max 200).
3. If run_sql returns an error, read the message, fix the query and run it again.
4. If you are unsure which values exist (category tags, product names, chain names), run a
   small exploratory query before the final one instead of guessing.
5. Return the final answer.

About the data:
- All stores are in France and all prices are in EUR. Product names are mostly in French
  and category tags are in English (for example 'en:whole-milks'); translate the user's
  words accordingly.
- To compare products of different sizes, use the view v_price_per_unit (EUR per kg or l).
- locations.chain is NULL for independent shops: exclude them when comparing chains.
- Coverage is uneven. When a result rests on very few rows, say so and lower the confidence.

The final answer:
- answer: one to three sentences, in the language of the question, with the key numbers.
- sql: the exact query, as you ran it, whose result supports the answer.
- If the database cannot answer the question (data that does not exist, another country,
  a product or period with no rows), set answerable to false, explain why in answer and
  leave sql empty. Never invent a figure.
"""


class AgentAnswer(BaseModel):
    """What the model must return. PydanticAI validates it and asks again if it is wrong."""

    answerable: bool = Field(description="False if the database cannot answer the question.")
    answer: str = Field(description="Short answer in the language of the question.")
    sql: str | None = Field(
        default=None,
        description="The exact query run with run_sql that supports the answer. Null if not "
        "answerable.",
    )
    chart: Literal["none", "table", "bar", "line"] = Field(
        description="Best way to show the result: bar to compare categories, line for "
        "evolution over time, table for lists, none for a single number."
    )
    confidence: Literal["high", "medium", "low"]


class SqlAttempt(BaseModel):
    """One call to run_sql, kept for the evaluation (valid at first try, retries)."""

    sql: str
    ok: bool
    error: str | None = None
    row_count: int | None = None


@dataclass
class RunState:
    """Per-question memory shared by the tools: what was run and what came back."""

    attempts: list[SqlAttempt] = field(default_factory=list)
    results: dict[str, SqlResult] = field(default_factory=dict)


class AgentRun(BaseModel):
    """Everything about one question: the answer, its data and what it cost."""

    question: str
    answer: AgentAnswer
    data: SqlResult | None = None
    attempts: list[SqlAttempt]
    error: str | None = None
    model: str
    prompt_version: str = PROMPT_VERSION
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 0
    seconds: float = 0.0


def _normalise(sql: str) -> str:
    """Same query regardless of whitespace, case and trailing semicolon."""
    return " ".join(sql.strip().rstrip(";").split()).lower()


# defer_model_check: importing this file must not require an API key (tests, CI).
agent = Agent(
    output_type=AgentAnswer,
    instructions=INSTRUCTIONS,
    deps_type=RunState,
    defer_model_check=True,
)


@agent.tool_plain
def describe_schema() -> SchemaDescription:
    """List the tables and views with their columns, types and comments. Call this first."""
    return tools.describe_schema()


@agent.tool(retries=MAX_SQL_ATTEMPTS - 1)
def run_sql(ctx: RunContext[RunState], query: str) -> SqlResult:
    """Run one read-only PostgreSQL SELECT and return its rows.

    The query must be a single SELECT with a LIMIT of at most 200. On error the message
    explains what to fix.
    """
    try:
        result = tools.run_sql(query)
    except (SqlValidationError, SqlExecutionError) as error:
        ctx.deps.attempts.append(SqlAttempt(sql=query, ok=False, error=str(error)))
        # ModelRetry sends the message back to the model as "try again", and counts
        # against the tool's retries. This is the self-correction loop.
        raise ModelRetry(str(error)) from error
    ctx.deps.attempts.append(SqlAttempt(sql=query, ok=True, row_count=result.row_count))
    ctx.deps.results[_normalise(query)] = result
    return result


@agent.output_validator
def answer_is_backed_by_a_query(ctx: RunContext[RunState], output: AgentAnswer) -> AgentAnswer:
    """Reject an answer whose SQL was never executed: that is how invented numbers get in."""
    if not output.answerable:
        return output.model_copy(update={"sql": None})
    if not output.sql or _normalise(output.sql) not in ctx.deps.results:
        raise ModelRetry(
            "The sql field must be exactly a query you ran successfully with run_sql. "
            "Run the query first, or set answerable to false if the data does not exist."
        )
    return output


def default_model() -> str:
    """Model name from the environment, in PydanticAI's 'provider:model' format."""
    load_dotenv(ROOT / ".env")
    try:
        return os.environ["LLM_MODEL"]
    except KeyError:
        raise RuntimeError(
            "Set LLM_MODEL in .env (for example 'anthropic:claude-haiku-5-5') and the API "
            "key of that provider. See .env.example."
        ) from None


def ask(question: str, model: Model | str | None = None) -> AgentRun:
    """Answer one question. Never raises for a model that gives up: it reports it instead."""
    model = model or default_model()
    model_name = model if isinstance(model, str) else model.model_name
    state = RunState()
    started = time.perf_counter()
    # Our own counter, so tokens are still known when the run ends in an exception.
    usage = RunUsage()
    error = None

    try:
        result = agent.run_sync(
            question,
            model=model,
            deps=state,
            usage=usage,
            usage_limits=UsageLimits(request_limit=MAX_MODEL_REQUESTS),
        )
        answer = result.output
    except (UnexpectedModelBehavior, UsageLimitExceeded) as failure:
        # Three failed queries in a row, or too many steps: stop and say so honestly.
        error = f"{type(failure).__name__}: {failure}"
        answer = AgentAnswer(
            answerable=False,
            answer="The agent could not produce a reliable answer to this question.",
            chart="none",
            confidence="low",
        )

    return AgentRun(
        question=question,
        answer=answer,
        data=state.results.get(_normalise(answer.sql)) if answer.sql else None,
        attempts=state.attempts,
        error=error,
        model=model_name,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        requests=usage.requests,
        seconds=round(time.perf_counter() - started, 2),
    )
