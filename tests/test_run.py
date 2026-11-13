"""The runner, with a fake agent: no model and no database."""

import csv

import pytest

from agent.agent import AgentAnswer, AgentRun, SqlAttempt
from agent.models import SqlResult
from evals.questions import Question
from evals.run import cost_eur, evaluate_one, run_all, summarise, write_csv

MODEL = "anthropic:claude-haiku-5-5"

QUESTIONS = [
    Question(
        id="L01",
        category="lookup",
        question="How many stores?",
        answer_type="number",
        reference_sql="SELECT count(*) FROM locations LIMIT 1",
        expected=2285,
    ),
    Question(
        id="L02",
        category="lookup",
        question="How many products?",
        answer_type="number",
        reference_sql="SELECT count(*) FROM products LIMIT 1",
        expected=65826,
    ),
    Question(
        id="R01",
        category="refusal",
        question="Prices in Spain?",
        answer_type="refusal",
        reason="France only",
    ),
]


def fake_ask(question: str, model: str) -> AgentRun:
    """Right on stores (after one failed query), wrong on products, refuses Spain."""
    if "Spain" in question:
        answer = AgentAnswer(answerable=False, answer="No data.", chart="none", confidence="high")
        return AgentRun(question=question, answer=answer, attempts=[], model=model, seconds=2.0)
    if "products" in question:
        raise ConnectionError("network down")
    sql = "SELECT count(*) FROM locations LIMIT 1"
    return AgentRun(
        question=question,
        answer=AgentAnswer(
            answerable=True, answer="2,285 stores.", sql=sql, chart="none", confidence="high"
        ),
        data=SqlResult(columns=["count"], rows=[[2285]], row_count=1),
        attempts=[
            SqlAttempt(sql="SELECT count(*) FROM locations", ok=False, error="no LIMIT"),
            SqlAttempt(sql=sql, ok=True, row_count=1),
        ],
        model=model,
        input_tokens=1_000_000,
        output_tokens=100_000,
        requests=4,
        seconds=10.0,
    )


def test_cost_uses_the_price_table_and_is_unknown_for_other_models():
    # 1 MTok in at 0.10 USD + 0.1 MTok out at 0.50 USD = 0.15 USD.
    assert cost_eur(MODEL, 1_000_000, 100_000) == pytest.approx(0.15 / 1.1081, abs=1e-6)
    assert cost_eur("openai:some-model", 1_000_000, 100_000) is None


def test_one_question_records_the_grade_and_what_the_run_cost():
    result = evaluate_one(QUESTIONS[0], MODEL, "run-1", "2026-11-13", fake_ask)

    assert result.correct
    assert (result.queries, result.failed_queries, result.first_query_ok) == (2, 1, False)
    assert result.requests == 4
    assert result.cost_eur == pytest.approx(0.15 / 1.1081, abs=1e-6)


def test_an_exception_in_the_agent_is_a_wrong_answer_not_a_crash():
    result = evaluate_one(QUESTIONS[1], MODEL, "run-1", "2026-11-13", fake_ask)

    assert not result.correct
    assert "ConnectionError" in result.error
    assert result.first_query_ok is None


def test_the_summary_adds_up():
    results = run_all(QUESTIONS, MODEL, workers=2, ask_fn=fake_ask)
    summary = summarise(results)

    assert [r.id for r in results] == ["L01", "L02", "R01"]
    assert summary.questions == 3
    assert summary.accuracy_pct == 66.7
    assert summary.accuracy_answerable_pct == 50.0
    assert (summary.correct_refusals, summary.refusal_questions) == (1, 1)
    assert summary.wrongly_refused == 0
    assert summary.sql_valid_first_try_pct == 0.0  # the only run with queries failed its first
    assert summary.failed_queries == 1
    assert summary.runs_failed == 1
    assert summary.cost_eur is None  # one question has no cost, so the total is not known
    assert summary.latency_max_s == 10.0


def test_results_are_written_and_the_summary_file_is_appended(tmp_path):
    results = run_all(QUESTIONS, MODEL, workers=1, ask_fn=fake_ask)
    path = tmp_path / "summary.csv"

    write_csv(path, [summarise(results)], append=True)
    write_csv(path, [summarise(results)], append=True)

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 2
    assert rows[0]["accuracy_pct"] == "66.7"
    assert rows[0]["model"] == MODEL
