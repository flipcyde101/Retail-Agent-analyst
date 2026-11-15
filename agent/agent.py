"""The agent: a PydanticAI loop around the two tools, with a validated final answer.

What PydanticAI does for us: it sends the question, the instructions and the JSON schema of
each tool to the model; when the model answers "call run_sql with this query" it runs our
function and sends the result back; and it repeats until the model returns the final answer,
which it validates against AgentAnswer. We write the tools, the instructions and the rules.
"""

import datetime
import os
import time
from dataclasses import dataclass, field
from functools import cache, lru_cache
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models import Model, infer_model
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage, UsageLimits

from agent import tools
from agent.db import ROOT
from agent.language import NAMES, detect_language
from agent.models import SchemaDescription, SqlResult
from agent.prompts import PROMPTS
from agent.sql_guard import SqlValidationError
from agent.tools import DatabaseUnavailableError, SqlExecutionError

PROMPT_VERSION = "v5"
# Versions that also get the checks added on day 7 (SQL clean-up, no refusal before looking
# at the schema, no empty answer). v1 runs exactly as it was first measured.
STRICT_VERSIONS = frozenset(PROMPTS) - {"v1"}
# Versions where the code, not only the prompt, takes care of the answer's language.
LANGUAGE_VERSIONS = frozenset(PROMPTS) - {"v1", "v2"}
# Versions that get the schema inside the instructions and have no describe_schema tool.
INLINE_SCHEMA_VERSIONS = frozenset(PROMPTS) - {"v1", "v2", "v3"}
# Versions that are told today's date.
DATE_VERSIONS = frozenset(PROMPTS) - {"v1", "v2", "v3", "v4"}
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

    def __post_init__(self) -> None:
        if self.inline_schema:
            self.schema_seen = True

    @property
    def strict(self) -> bool:
        return self.prompt_version in STRICT_VERSIONS

    @property
    def inline_schema(self) -> bool:
        return self.prompt_version in INLINE_SCHEMA_VERSIONS

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


@lru_cache(maxsize=1)
def schema_text() -> str:
    """The schema as plain text for the instructions, read from the database once.

    Text and not JSON: the same tables, columns and comments in fewer tokens. Cached for
    the life of the process, because the schema only changes when the data is reloaded.
    """
    lines = []
    for table in tools.describe_schema().tables:
        comment = f": {table.comment}" if table.comment else ""
        lines.append(f"\n{table.kind} {table.name}{comment}")
        for column in table.columns:
            comment = f"  -- {column.comment}" if column.comment else ""
            lines.append(f"  {column.name} {column.type}{comment}")
    return "\n".join(lines).strip()


def today() -> datetime.date:
    """Today's date. A function of its own so that tests can fix the day."""
    return datetime.date.today()


FALLBACK_ANSWERS = {
    "en": "The agent could not produce a reliable answer to this question.",
    "es": "El agente no ha podido dar una respuesta fiable a esta pregunta.",
    "fr": "L'agent n'a pas pu donner une réponse fiable à cette question.",
}

# defer_model_check: importing this file must not require an API key (tests, CI).
agent = Agent(output_type=AgentAnswer, deps_type=RunState, defer_model_check=True)


@agent.instructions
def instructions(ctx: RunContext[RunState]) -> str:
    text = PROMPTS[ctx.deps.prompt_version]
    if ctx.deps.inline_schema:
        text += f"\nDatabase schema:\n{schema_text()}\n"
    if ctx.deps.prompt_version in DATE_VERSIONS:
        text += (
            f"\nToday's date is {today().isoformat()}. Use it for \"this year\", \"last "
            "month\" and the like. The data may end before today: max(date) tells where.\n"
        )
    language = ctx.deps.required_language
    if language:
        # Telling the model beats asking it to work it out: with the prompt rule alone (v2)
        # it kept answering English questions in French, the language of the data.
        text += f"\nThis question is written in {NAMES[language]}: answer in {NAMES[language]}.\n"
    return text


async def _only_without_inline_schema(
    ctx: RunContext[RunState], definition: ToolDefinition
) -> ToolDefinition | None:
    # Returning None hides the tool from the model for this run.
    return None if ctx.deps.inline_schema else definition


@agent.tool(prepare=_only_without_inline_schema)
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


@cache
def _client(model_name: str) -> Model:
    """The model object for a 'provider:model' name, built once per process.

    Building it imports the provider's SDK, which took about 17 s on the first call when
    measured: it was the one question per run that went over 15 s. Keeping the object also
    keeps its HTTP connection open between questions.
    """
    return infer_model(model_name)


def warm_up(model: str | None = None, prompt_version: str = PROMPT_VERSION) -> None:
    """Pay the start-up costs now, so the first question is as fast as the others.

    Call it when the process starts (the demo, the evaluation): it loads the model client
    and, for the versions that carry the schema in the instructions, reads the schema.
    """
    load_dotenv(ROOT / ".env")
    _client(model or default_model())
    if prompt_version in INLINE_SCHEMA_VERSIONS:
        schema_text()


def ask(
    question: str, model: Model | str | None = None, prompt_version: str = PROMPT_VERSION
) -> AgentRun:
    """Answer one question.

    Never raises for a model that gives up or a database that is down: the run comes back
    with `error` set and an answer that says so. An error of the model API still raises.
    """
    if prompt_version not in PROMPTS:
        raise ValueError(f"Unknown prompt version {prompt_version!r}; have {sorted(PROMPTS)}")
    # The API key lives in .env too, and it is needed whichever way the model was chosen.
    load_dotenv(ROOT / ".env")
    model = model or default_model()
    model_name = model if isinstance(model, str) else model.model_name
    if isinstance(model, str):
        model = _client(model)
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
    except (UnexpectedModelBehavior, UsageLimitExceeded, DatabaseUnavailableError) as failure:
        # Three failed queries in a row, too many steps, or no database: stop and say so
        # honestly, in the language of the question when it is known.
        error = f"{type(failure).__name__}: {failure}"
        answer = AgentAnswer(
            answerable=False,
            answer=FALLBACK_ANSWERS[state.required_language or "en"],
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
