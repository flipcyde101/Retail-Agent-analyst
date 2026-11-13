"""The evaluation set is data, and data gets tests too.

The first block needs no database: it runs in CI and catches a malformed file. The last
test reruns every reference SQL and is skipped when PostgreSQL is not reachable.
"""

import psycopg
import pytest

from agent.db import connect
from agent.sql_guard import validate_sql
from evals.questions import (
    Question,
    database_problems,
    dump_questions,
    load_questions,
    same_answer,
    structure_problems,
)

QUESTIONS = load_questions()


def _database_is_up() -> bool:
    try:
        connect().close()
    except (psycopg.Error, KeyError):
        return False
    return True


def test_there_are_fifty_questions_ten_per_category_with_unique_ids():
    assert len(QUESTIONS) == 50
    assert structure_problems(QUESTIONS) == []


@pytest.mark.parametrize("question", [q for q in QUESTIONS if q.reference_sql], ids=lambda q: q.id)
def test_every_reference_sql_obeys_the_rules_the_agent_obeys(question):
    validate_sql(question.reference_sql)


def test_every_answerable_question_has_an_expected_answer():
    missing = [q.id for q in QUESTIONS if q.answer_type != "refusal" and q.expected is None]
    assert missing == []


def test_a_question_without_reference_sql_is_rejected_unless_it_is_a_refusal():
    with pytest.raises(ValueError, match="reference_sql"):
        Question(id="X01", category="lookup", question="How many?", answer_type="number")


def test_a_refusal_needs_a_reason():
    with pytest.raises(ValueError, match="reason"):
        Question(id="X02", category="refusal", question="Why?", answer_type="refusal")


def test_duplicated_ids_and_short_categories_are_reported():
    problems = structure_problems(QUESTIONS[:-1] + [QUESTIONS[0]])
    assert any("L01" in p for p in problems)
    assert any("refusal: 9" in p for p in problems)


def test_same_answer_tolerates_float_noise_but_not_a_real_difference():
    assert same_answer(2.76, 2.7600000000000002)
    assert same_answer([["Auchan", 1.83]], [["Auchan", 1.83]])
    assert not same_answer(2.76, 2.77)
    assert not same_answer([["Auchan", 1.83]], [["Carrefour", 1.83]])
    assert not same_answer([[1]], [[1], [2]])


def test_writing_the_file_and_reading_it_back_changes_nothing(tmp_path):
    copy = tmp_path / "questions.yaml"
    copy.write_text(dump_questions(QUESTIONS), encoding="utf-8")
    assert load_questions(copy) == QUESTIONS


@pytest.mark.skipif(not _database_is_up(), reason="database not reachable")
def test_stored_expected_answers_match_the_database():
    assert database_problems(QUESTIONS) == []
