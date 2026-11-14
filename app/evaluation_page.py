"""The evaluation page: how often the agent is right, what it costs and how long it takes.

Everything shown is read from evals/results/, the files the evaluation writes. Nothing is
typed here, so the page cannot disagree with the measurements.
"""

from pathlib import Path

import pandas as pd
import streamlit as st

from agent.agent import PROMPT_VERSION

RESULTS_DIR = Path(__file__).parent.parent / "evals" / "results"
REPORT_URL = "https://github.com/pitelet222/Retail-Agent-analyst/blob/main/evals/REPORT.md"

RUN_COLUMNS = {
    "date": "Date",
    "question_set": "Set",
    "model": "Model",
    "prompt_version": "Prompt",
    "accuracy_pct": "Accuracy %",
    "refusals": "Correct refusals",
    "wrong_language": "Wrong language",
    "sql_valid_first_try_pct": "SQL valid 1st try %",
    "cost_per_question_eur": "EUR per question",
    "latency_median_s": "Median s",
    "latency_p90_s": "p90 s",
}
QUESTION_COLUMNS = ["id", "category", "result", "seconds", "queries", "answer", "sql"]


def load_summary(results_dir: Path = RESULTS_DIR) -> pd.DataFrame:
    """One row per run, oldest first, as the evaluation appended them."""
    summary = pd.read_csv(results_dir / "summary.csv")
    summary["model"] = summary["model"].str.split(":").str[-1]
    summary["refusals"] = (
        summary["correct_refusals"].astype(str) + " of " + summary["refusal_questions"].astype(str)
    )
    return summary


def latest_run(summary: pd.DataFrame, question_set: str, prompt_version: str) -> pd.Series | None:
    """The most recent run of a set with a prompt version, or None if there is none."""
    runs = summary[
        (summary["question_set"] == question_set) & (summary["prompt_version"] == prompt_version)
    ]
    return None if runs.empty else runs.iloc[-1]


def load_run(run_id: str, results_dir: Path = RESULTS_DIR) -> pd.DataFrame:
    """The questions of one run, with the verdict as a word."""
    questions = pd.read_csv(results_dir / f"{run_id}.csv")
    # The column holds True, False or nothing (left for a person to read).
    verdict = questions["correct"].map(lambda v: {"True": "right", "False": "wrong"}.get(str(v)))
    questions["result"] = verdict.fillna("read by hand")
    return questions


def render() -> None:
    st.title("Evaluation")
    st.write(
        "The agent is measured on 50 questions with a known answer, 10 of them questions "
        "it must refuse, and on 22 more worded the way a user would ask. Each run asks them "
        "one at a time and records what came back."
    )
    try:
        summary = load_summary()
    except (FileNotFoundError, pd.errors.EmptyDataError):
        st.info("No evaluation results are available.")
        return
    current = latest_run(summary, "core", PROMPT_VERSION)
    if current is not None:
        st.subheader(f"Latest run of the current version ({current['model']}, {PROMPT_VERSION})")
        accuracy, refusals, latency, cost = st.columns(4)
        accuracy.metric("Accuracy", f"{current['accuracy_pct']:.0f} %")
        refusals.metric("Correct refusals", current["refusals"])
        latency.metric("Median answer", f"{current['latency_median_s']:.1f} s")
        # Per 100 questions: one question costs a fraction of a cent, which reads badly.
        # A model with no known price has no cost recorded, and none is made up here.
        per_question = current["cost_per_question_eur"]
        priced = "not priced" if pd.isna(per_question) else f"{100 * per_question:.2f} EUR"
        cost.metric("Cost of 100 questions", priced)
        st.caption(
            "50 questions make each one worth 2 points, and two runs of the same agent can "
            "differ by more than that. The report says what the numbers do and do not show."
        )

    st.subheader("Every run")
    newest_first = summary.iloc[::-1]
    st.dataframe(
        newest_first[list(RUN_COLUMNS)].rename(columns=RUN_COLUMNS),
        hide_index=True,
    )
    st.markdown(
        f"Failure types, what fixed them and the comparisons between prompt versions and "
        f"models are in the [evaluation report]({REPORT_URL})."
    )

    st.subheader("One run, question by question")
    run_ids = newest_first["run_id"].tolist()
    shown_first = run_ids.index(current["run_id"]) if current is not None else 0
    run_id = st.selectbox("Run", run_ids, index=shown_first)
    try:
        questions = load_run(run_id)
    except (FileNotFoundError, pd.errors.EmptyDataError):
        st.info("The questions of this run are not available.")
        return
    only_problems = st.checkbox("Only the ones not marked right")
    if only_problems:
        questions = questions[questions["result"] != "right"]
    st.dataframe(questions[QUESTION_COLUMNS], hide_index=True)
