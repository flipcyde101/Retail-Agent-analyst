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
from agent.language import NAMES, detect_language
from agent.models import SchemaDescription, SqlResult
from agent.prompts import PROMPTS
from agent.sql_guard import SqlValidationError
from agent.tools import SqlExecutionError

PROMPT_VERSION = "v3"
# Versions that also get the checks added on day 7 (SQL clean-up, no refusal before looking
# at the schema, no empty answer). v1 runs exactly as it was first measured.
STRICT_VERSIONS = frozenset(PROMPTS) - {"v1"}
# Versions where the code, not only the prompt, takes care of the answer's language.
LANGUAGE_VERSIONS = frozenset(PROMPTS) - {"v1", "v2"}
# The stricter checks send more answers back, so they need more room than the default of 1.
STRICT_OUTPUT_RETRIES = 3

# A failed query can be corrected twice: 3 attempts in a row, then the run stops.
MAX_SQL_ATTEMPTS = 3
# Hard ceiling on model calls per question, so a confused model cannot loop (and bill) forever.
MAX_MODEL_REQUESTS = 12


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
    # True when stray characters after the SQL were removed before running it.
    repaired: bool = False


@dataclass
class RunState:
    """Per-question memory shared by the tools: what was run and what came back."""

    prompt_version: str = PROMPT_VERSION
    # en, es, fr or unknown: guessed from the question, used by the versions that enforce it.
    question_language: str = "unknown"
    schema_seen: bool = False
    attempts: list[SqlAttempt] = field(default_factory=list)
    results: dict[str, SqlResult] = field(default_factory=dict)

    @property
    def strict(self) -> bool:
        return self.prompt_version in STRICT_VERSIONS

    @property
    def required_language(self) -> str | None:
        """The language the answer must be in, when this version enforces one."""
        if self.prompt_version in LANGUAGE_VERSIONS and self.question_language in NAMES:
            return self.question_language
        return None


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


def strip_json_debris(sql: str) -> str:
    """Remove a stray quote or brace the model sometimes leaves after the SQL.

    Seen in 4 of the 7 failed queries of the first measurement: the tool argument arrives as
    `SELECT ... LIMIT 200"}`. A valid query here always ends in the LIMIT number, so trailing
    quotes and braces are never part of it. The cleaned query is still validated.
    """
    return sql.rstrip().rstrip('"}').rstrip()


# defer_model_check: importing this file must not require an API key (tests, CI).
agent = Agent(output_type=AgentAnswer, deps_type=RunState, defer_model_check=True)


@agent.instructions
def instructions(ctx: RunContext[RunState]) -> str:
    text = PROMPTS[ctx.deps.prompt_version]
    language = ctx.deps.required_language
    if language:
        # Telling the model beats asking it to work it out: with the prompt rule alone (v2)
        # it kept answering English questions in French, the language of the data.
        text += f"\nThis question is written in {NAMES[language]}: answer in {NAMES[language]}.\n"
    return text


@agent.tool
def describe_schema(ctx: RunContext[RunState]) -> SchemaDescription:
    """List the tables and views with their columns, types and comments. Call this first."""
    ctx.deps.schema_seen = True
    return tools.describe_schema()


@agent.tool(retries=MAX_SQL_ATTEMPTS - 1)
def run_sql(ctx: RunContext[RunState], query: str) -> SqlResult:
    """Run one read-only PostgreSQL SELECT and return its rows.

    The query must be a single SELECT with a LIMIT of at most 200. On error the message
    explains what to fix.
    """
    cleaned = strip_json_debris(query) if ctx.deps.strict else query
    repaired = cleaned != query.rstrip()
    try:
        result = tools.run_sql(cleaned)
    except (SqlValidationError, SqlExecutionError) as error:
        ctx.deps.attempts.append(
            SqlAttempt(sql=cleaned, ok=False, error=str(error), repaired=repaired)
        )
        # ModelRetry sends the message back to the model as "try again", and counts
        # against the tool's retries. This is the self-correction loop.
        raise ModelRetry(str(error)) from error
    ctx.deps.attempts.append(
        SqlAttempt(sql=cleaned, ok=True, row_count=result.row_count, repaired=repaired)
    )
    ctx.deps.results[_normalise(cleaned)] = result
    return result


@agent.output_validator
def answer_is_backed_by_a_query(ctx: RunContext[RunState], output: AgentAnswer) -> AgentAnswer:
    """Reject an answer whose SQL was never executed: that is how invented numbers get in."""
    state = ctx.deps
    if state.strict and not output.answer.strip():
        raise ModelRetry("The answer field is empty. Write the answer, or explain the refusal.")
    required = state.required_language
    written = detect_language(output.answer)
    if required and written in NAMES and written != required:
        raise ModelRetry(
            f"The answer is written in {NAMES[written]} but the question is in "
            f"{NAMES[required]}. Return the same answer written in {NAMES[required]}."
        )
    if not output.answerable:
        if state.strict and not state.schema_seen:
            # Seen in the first measurement: the model "answered" before doing anything,
            # with answerable = false, and the run ended there.
            raise ModelRetry(
                "You have not looked at the database yet. Call describe_schema, and run a "
                "query if the data might exist, before deciding the question has no answer."
            )
        return output.model_copy(update={"sql": None})
    sql = strip_json_debris(output.sql) if state.strict and output.sql else output.sql
    if not sql or _normalise(sql) not in state.results:
        raise ModelRetry(
            "The sql field must be exactly a query you ran successfully with run_sql. "
            "Run the query first, or set answerable to false if the data does not exist."
        )
    return output.model_copy(update={"sql": sql})


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


def ask(
    question: str, model: Model | str | None = None, prompt_version: str = PROMPT_VERSION
) -> AgentRun:
    """Answer one question. Never raises for a model that gives up: it reports it instead."""
    if prompt_version not in PROMPTS:
        raise ValueError(f"Unknown prompt version {prompt_version!r}; have {sorted(PROMPTS)}")
    # The API key lives in .env too, and it is needed whichever way the model was chosen.
    load_dotenv(ROOT / ".env")
    model = model or default_model()
    model_name = model if isinstance(model, str) else model.model_name
    state = RunState(prompt_version=prompt_version, question_language=detect_language(question))
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
            retries={"output": STRICT_OUTPUT_RETRIES} if state.strict else None,
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
        prompt_version=prompt_version,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        requests=usage.requests,
        seconds=round(time.perf_counter() - started, 2),
    )
