"""The grading rules. A wrong grader makes every number of the evaluation wrong."""

from datetime import date
from decimal import Decimal

import pytest

from agent.agent import AgentAnswer, AgentRun
from agent.models import SqlResult
from evals.grading import cell_matches, grade, numbers_in_text
from evals.questions import Question


def question(answer_type="number", expected=30805, **fields) -> Question:
    category = "refusal" if answer_type == "refusal" else "lookup"
    reason = "no data" if answer_type == "refusal" else None
    sql = None if answer_type == "refusal" else "SELECT 1 LIMIT 1"
    return Question(
        id="X01",
        category=category,
        question="?",
        answer_type=answer_type,
        reference_sql=sql,
        reason=reason,
        expected=expected,
        **fields,
    )


def run(text="", rows=None, answerable=True, error=None) -> AgentRun:
    data = None
    if rows is not None:
        data = SqlResult(columns=["c"] * len(rows[0]), rows=rows, row_count=len(rows))
    return AgentRun(
        question="?",
        answer=AgentAnswer(
            answerable=answerable,
            answer=text,
            sql="SELECT 1 LIMIT 1" if data else None,
            chart="none",
            confidence="high",
        ),
        data=data,
        attempts=[],
        error=error,
        model="test",
    )


# --- reading numbers out of a sentence ----------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("There are 30,805 prices.", 30805),
        ("Hay 30.805 precios.", 30805),
        ("Il y a 30 805 prix.", 30805),
        ("The median is 2.76 EUR.", 2.76),
        ("La mediana es 2,76 EUR.", 2.76),
        ("It costs 1,234.50 EUR in total.", 1234.5),
        ("Cuesta 1.234,50 EUR en total.", 1234.5),
        ("It fell by 2.89%.", 2.89),
        ("The year was 2024.", 2024),
    ],
)
def test_numbers_are_read_in_english_and_spanish_formats(text, expected):
    assert expected in numbers_in_text(text)


def test_an_ambiguous_token_gives_both_readings():
    assert set(numbers_in_text("1.234")) == {1234.0, 1.234}


# --- one cell ------------------------------------------------------------------------


def test_cells_match_across_types_the_database_returns():
    assert cell_matches(1.83, Decimal("1.828"))
    assert cell_matches("Auchan", "auchan")
    assert cell_matches("2026-02", date(2026, 2, 1))
    assert cell_matches(2024, date(2024, 1, 1))
    assert cell_matches(2024, Decimal("2024"))
    assert not cell_matches(1.83, Decimal("1.96"))
    assert not cell_matches(160, "Lidl")
    assert not cell_matches("Auchan", "Carrefour")


# --- number and text -----------------------------------------------------------------


def test_a_number_is_right_when_the_query_returns_it():
    assert grade(question(), run("Many.", rows=[[30805]])).correct


def test_a_number_is_right_when_the_answer_states_it_within_one_percent():
    assert grade(question(), run("About 30,900 prices.", rows=[[1], [2]])).correct
    assert not grade(question(), run("About 32,000 prices.", rows=[[1], [2]])).correct


def test_a_number_buried_in_a_long_result_does_not_count():
    # The right figure is one of many rows, and the answer states another one.
    result = grade(question(), run("Auchan has 23,373.", rows=[[23373], [30805]]))
    assert not result.correct


def test_a_negative_change_matches_a_sentence_without_the_sign():
    assert grade(question(expected=-2.89), run("It fell by 2.89 %.", rows=[[1], [2]])).correct


def test_a_date_is_right_from_the_result_or_from_the_text():
    q = question("text", "2026-11-12")
    assert grade(q, run("Last week.", rows=[[date(2026, 11, 12)]])).correct
    assert grade(q, run("The latest is 2026-11-12.")).correct
    assert not grade(q, run("The latest is 2026-11-10.", rows=[[date(2026, 11, 10)]])).correct


def test_a_date_written_in_words_is_right_when_it_tops_a_sorted_result():
    # Found in the first measurement: "6 February 2021" over 200 rows sorted by date.
    q = question("text", "2021-02-06")
    rows = [[date(2021, 2, 6), 0.98], [date(2021, 3, 1), 0.99]]
    assert grade(q, run("First seen on 6 February 2021.", rows=rows)).correct
    assert not grade(q, run("First seen in spring.", rows=rows[::-1])).correct


# --- rows ----------------------------------------------------------------------------


def test_rows_match_with_extra_columns_and_extra_rows():
    q = question("rows", [["Carrefour", 24.94], ["E.Leclerc", 19.17]])
    rows = [["Auchan", 9, 20.0], ["E.Leclerc", 545, 19.171], ["Carrefour", 432, 24.938]]
    assert grade(q, run(rows=rows)).correct


def test_a_missing_row_is_wrong():
    q = question("rows", [["Carrefour", 24.94], ["E.Leclerc", 19.17]])
    assert not grade(q, run(rows=[["Carrefour", 24.94]])).correct


def test_ordered_rows_must_keep_their_order():
    q = question("rows", [[2024, 3.57], [2025, 3.74]], ordered=True)
    assert grade(q, run(rows=[[2024, 3.57], [2025, 3.74]])).correct
    assert not grade(q, run(rows=[[2025, 3.74], [2024, 3.57]])).correct


def test_a_single_expected_row_must_come_first():
    q = question("rows", [["Auchan", 1.83]])
    assert grade(q, run(rows=[["Auchan", 1.83], ["U", 2.2]])).correct
    assert not grade(q, run(rows=[["U", 2.2], ["Auchan", 1.83]])).correct


def test_no_result_at_all_is_wrong():
    assert not grade(question("rows", [["Auchan", 1.83]]), run("Auchan, 1.83.")).correct


# --- refusals and failures -----------------------------------------------------------


def test_a_refusal_is_right_only_when_the_agent_refuses_by_itself():
    q = question("refusal", None)
    assert grade(q, run("No data for Spain.", answerable=False)).correct
    assert not grade(q, run("It costs 1.20.", rows=[[1.2]])).correct
    assert not grade(q, run("Gave up.", answerable=False, error="UsageLimitExceeded")).correct


def test_refusing_or_failing_a_question_that_has_an_answer_is_wrong():
    assert not grade(question(), run("I cannot tell.", answerable=False)).correct
    assert not grade(question(), run("30805", answerable=False, error="boom")).correct
