"""The main page: ask a question, see the answer, the SQL behind it, the rows and a chart."""

from datetime import date

import streamlit as st

from agent import agent as agent_module
from agent.agent import AgentRun
from app.charts import MAX_BARS, build_chart, time_column, to_frame, value_columns
from app.limits import (
    MAX_QUESTION_CHARS,
    QUESTIONS_PER_DAY,
    QUESTIONS_PER_SESSION,
    DailyBudget,
    problem_with,
)
from evals.run import cost_eur

EXAMPLES = [
    "Which chain has the cheapest whole milk per litre?",
    "How has the number of prices recorded changed year by year since 2022?",
    "¿Cuál es el precio medio por kilo de la mantequilla en Carrefour y en E.Leclerc?",
    "How many jars of Nutella did Carrefour sell in 2025?",
]
CONFIDENCE = {"high": "High confidence", "medium": "Medium confidence", "low": "Low confidence"}
DAILY_LIMIT = QUESTIONS_PER_DAY


@st.cache_resource(show_spinner="Starting up: loading the model client (once per server)...")
def start_up() -> bool:
    """Load the model client and the schema once, so no visitor's question waits for it."""
    agent_module.warm_up()
    return True


@st.cache_resource
def daily_budget() -> DailyBudget:
    """One budget for the whole server: st.cache_resource shares it between sessions."""
    return DailyBudget(limit=DAILY_LIMIT)


def _ask(question: str) -> None:
    """Check the limits, run the agent and keep the result in the session."""
    history: list[AgentRun] = st.session_state.setdefault("runs", [])
    problem = problem_with(question)
    if problem:
        st.warning(problem)
        return
    # Attempts, not answers: a question that fails still counts for the session.
    if st.session_state.get("asked", 0) >= QUESTIONS_PER_SESSION:
        st.warning(
            f"This demo answers {QUESTIONS_PER_SESSION} questions per session. "
            "The code is public: you can run it with your own key."
        )
        return
    budget = daily_budget()
    today = date.today()
    if not budget.take(today):
        st.warning("The demo has reached its limit of questions for today. Come back tomorrow.")
        return
    st.session_state["asked"] = st.session_state.get("asked", 0) + 1
    try:
        with st.spinner("Writing and running the query..."):
            run = agent_module.ask(question.strip())
            # Stored before the spinner closes: if the visitor clicked something meanwhile,
            # Streamlit stops this script at its next call, and an answer already paid for
            # would be lost.
            history.insert(0, run)
    except Exception as error:  # noqa: BLE001 - a visitor gets a sentence, not a traceback
        # The model API is down, the key is missing... Nothing the visitor can fix, and
        # nothing was answered, so today's budget gets the question back.
        budget.give_back(today)
        print(f"ask() failed: {type(error).__name__}: {error}")
        st.error("The agent could not be reached. Try again in a moment.")


def _show(run: AgentRun, key: str) -> None:
    # The question comes from a visitor and the answer from a model that has read it.
    # st.text shows both exactly as written; every other element would read them as
    # Markdown and turn a pasted address into a link or an image.
    st.caption("Question")
    st.text(run.question)
    if run.error:
        st.error("The agent could not finish this question.")
    elif not run.answer.answerable:
        st.warning("The database cannot answer this question.")
    st.caption("Answer")
    st.text(run.answer.answer)

    if run.data is not None and run.data.rows:
        frame = to_frame(run.data)
        kind = run.answer.chart
        # Only offer a choice of value when there is a chart for it to change.
        if build_chart(kind, run.data) is not None:
            # The time column (a year is a number too) is the x axis, not a value to plot.
            options = value_columns(frame, time_column(frame))
            value = options[0] if options else None
            if len(options) > 1:
                value = st.selectbox("Value shown in the chart", options, key=f"value-{key}")
            figure = build_chart(kind, run.data, value)
            st.plotly_chart(figure, key=f"chart-{key}")
            if figure.data[0].type == "bar" and run.data.row_count > MAX_BARS:
                st.caption(f"The chart shows the first {MAX_BARS} of {run.data.row_count} rows.")
        st.dataframe(frame, hide_index=True)
        if run.data.row_count == 200:
            st.caption("Showing the first 200 rows: the agent's queries are limited to 200.")

    if run.answer.sql:
        with st.expander("SQL that produced this answer"):
            st.code(run.answer.sql, language="sql")

    cost = cost_eur(run.model, run.input_tokens, run.output_tokens)
    failed = sum(not attempt.ok for attempt in run.attempts)
    queries = len(run.attempts)
    parts = [
        CONFIDENCE[run.answer.confidence] if run.answer.answerable else "No answer",
        f"{run.seconds:.1f} s",
        f"{run.requests} model call{'s' if run.requests != 1 else ''}",
        f"{queries} quer{'ies' if queries != 1 else 'y'}"
        + (f" ({failed} corrected)" if failed else ""),
        f"{run.input_tokens + run.output_tokens:,} tokens",
    ]
    if cost is not None:
        parts.append(f"{cost:.4f} EUR")
    st.caption(" · ".join(parts))


def render() -> None:
    st.title("Retail analyst agent")
    st.write(
        "Ask a question about supermarket prices in France. An LLM agent writes the SQL, "
        "runs it on a read-only PostgreSQL database and answers from the rows, and only "
        "from them. It answers in English, Spanish or French."
    )
    try:
        start_up()
    except Exception as error:  # noqa: BLE001 - missing key or database: a setup problem
        print(f"start-up failed: {type(error).__name__}: {error}")
        st.error("The demo is not available right now: its database or model is not reachable.")
        return

    with st.form("ask", clear_on_submit=True):
        typed = st.text_input(
            "Your question",
            max_chars=MAX_QUESTION_CHARS,
            placeholder="Which chain has the cheapest whole milk per litre?",
        )
        submitted = st.form_submit_button("Ask", type="primary")
    st.caption("Or try one of these:")
    chosen = None
    # One per line: side by side, the text was cut off on a laptop screen.
    for example in EXAMPLES:
        if st.button(example, key=f"example-{example}"):
            chosen = example

    if submitted:
        _ask(typed)
    elif chosen:
        _ask(chosen)

    runs: list[AgentRun] = st.session_state.get("runs", [])
    for position, run in enumerate(runs):
        st.divider()
        _show(run, key=str(len(runs) - position))

    st.divider()
    st.caption(
        "185,337 prices observed in 2,285 French stores, from "
        "[Open Prices](https://prices.openfoodfacts.org) by Open Food Facts, under the "
        "[ODbL](https://opendatacommons.org/licenses/odbl/1-0/). The prices are "
        "crowdsourced and coverage is uneven: read small samples with care. "
        "Code and evaluation: "
        "[github.com/pitelet222/Retail-Agent-analyst](https://github.com/pitelet222/Retail-Agent-analyst)."
    )
