"""Decide whether the agent's answer to one question is right.

Pure functions: no model, no database. The rules, by answer_type:

  number   the expected figure (within 1 %) is in the agent's answer text, or in the first
           row of the result of its query
  text     the expected value is in the answer text, or in the first row of the result
  rows     every expected row is found in the result of the agent's query; if the question
           is ordered, in the same order; if a single row is expected ("which chain is the
           cheapest?"), it must be the first row of the agent's result. Failing that, an
           unordered answer is right if the text states every name with its figure
  refusal  the agent said answerable = false, by itself (a run that crashed is not a refusal)
  manual   no verdict (correct = None): a person reads the answer

The rules err on the strict side: a doubtful answer counts as wrong, and the wrong ones are
reviewed by hand. Known leniency: in text, the sign is ignored ("fell by 2.89 %" matches
-2.89), so "rose by 2.89 %" would pass too.
"""

import math
import re
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from agent.agent import AgentRun
from evals.questions import Question

RELATIVE_TOLERANCE = 0.01
# Lets 0.46 match 0.4586: the reference answers are rounded to two decimals.
ABSOLUTE_TOLERANCE = 0.005

_NUMBER = re.compile(r"\d[\d.,   ]*\d|\d")
_THOUSANDS = re.compile(r"\d{1,3}([.,]\d{3})+")


class Grade(BaseModel):
    correct: bool | None  # None: not graded automatically
    reason: str


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)


def numbers_in_text(text: str) -> list[float]:
    """Every figure written in a sentence, as a non-negative number.

    "30,805", "30 805" and "30.805" are all thirty thousand; "2,76" is 2.76. When a token
    is ambiguous ("1.234" can be one thousand or one point two), both readings are returned.
    """
    found: list[float] = []
    for match in _NUMBER.finditer(text):
        token = re.sub(r"[   ]", "", match.group()).rstrip(".,")
        readings = set()
        if "," in token and "." in token:
            decimal_mark = max(",", ".", key=token.rfind)
            thousands_mark = "," if decimal_mark == "." else "."
            readings.add(token.replace(thousands_mark, "").replace(decimal_mark, "."))
        elif "," in token or "." in token:
            mark = "," if "," in token else "."
            if _THOUSANDS.fullmatch(token):
                readings.add(token.replace(mark, ""))
            if token.count(mark) == 1:
                readings.add(token.replace(mark, "."))
        else:
            readings.add(token)
        for reading in readings:
            try:
                found.append(float(reading))
            except ValueError:
                continue
    return found


def cell_matches(expected: Any, cell: Any) -> bool:
    """One expected value against one cell of the agent's result."""
    if isinstance(expected, bool) or isinstance(cell, bool):
        return expected == cell
    if isinstance(expected, int | float):
        if isinstance(cell, date):  # a year asked for, a date returned
            return expected == cell.year
        if isinstance(cell, int | float | Decimal):
            return close(float(expected), float(cell))
        try:
            return close(float(expected), float(str(cell)))
        except ValueError:
            return False
    # Text: 'Auchan' matches 'auchan'; '2026-02' matches the date 2026-02-01.
    return str(cell).strip().casefold().startswith(str(expected).strip().casefold())


def row_matches(expected_row: list, row: list) -> bool:
    """Every expected value is somewhere in the row; extra columns do not matter."""
    return all(any(cell_matches(value, cell) for cell in row) for value in expected_row)


def _grade_scalar(question: Question, run: AgentRun) -> Grade:
    expected = question.expected
    rows = run.data.rows if run.data else []
    # First row only: the query has to put the answer on top, not somewhere in 200 rows.
    if rows and row_matches([expected], rows[0]):
        return Grade(correct=True, reason="expected value is in the first row of the result")
    if question.answer_type == "number":
        if any(close(abs(float(expected)), n) for n in numbers_in_text(run.answer.answer)):
            return Grade(correct=True, reason="expected value is in the answer text")
    elif str(expected).casefold() in run.answer.answer.casefold():
        return Grade(correct=True, reason="expected value is in the answer text")
    return Grade(correct=False, reason=f"expected {expected!r} not found in answer or result")


def stated_in_text(expected_row: list, text: str) -> bool:
    """The row is spelled out in the answer: every name and every figure of it."""
    numbers = numbers_in_text(text)
    for value in expected_row:
        if isinstance(value, int | float) and not isinstance(value, bool):
            if not any(close(abs(float(value)), n) for n in numbers):
                return False
        elif str(value).casefold() not in text.casefold():
            return False
    return True


def _grade_rows(question: Question, run: AgentRun) -> Grade:
    in_result = _grade_rows_in_result(question, run)
    if in_result.correct or question.ordered:
        return in_result
    # The agent may answer from an earlier query and attach a different one. A name alone
    # proves nothing ("Carrefour or Leclerc?" has both in any answer), so a figure is needed.
    has_figures = all(
        any(isinstance(v, int | float) for v in row) and any(isinstance(v, str) for v in row)
        for row in question.expected
    )
    stated = all(stated_in_text(row, run.answer.answer) for row in question.expected)
    # run.data: figures with no query behind them are not accepted.
    if run.data and has_figures and stated:
        return Grade(correct=True, reason="all expected rows are stated in the answer text")
    return in_result


def _grade_rows_in_result(question: Question, run: AgentRun) -> Grade:
    expected: list[list] = question.expected
    rows = run.data.rows if run.data else []
    positions = []
    for expected_row in expected:
        position = next((i for i, row in enumerate(rows) if row_matches(expected_row, row)), None)
        if position is None:
            return Grade(correct=False, reason=f"row {expected_row!r} not in the result")
        positions.append(position)
    if len(expected) == 1 and positions[0] != 0:
        return Grade(
            correct=False,
            reason=f"{expected[0]!r} is in the result but not as its first row",
        )
    if question.ordered and positions != sorted(positions):
        return Grade(correct=False, reason="all rows found, in the wrong order")
    return Grade(correct=True, reason="all expected rows are in the result")


def grade(question: Question, run: AgentRun) -> Grade:
    if question.answer_type == "refusal":
        if run.error:
            return Grade(correct=False, reason="the run failed; that is not a refusal")
        if run.answer.answerable:
            return Grade(correct=False, reason="answered a question it should have refused")
        return Grade(correct=True, reason="refused")
    if question.answer_type == "manual":
        return Grade(correct=None, reason=f"manual review: {question.reason}")
    if run.error:
        return Grade(correct=False, reason="the run failed")
    if not run.answer.answerable:
        return Grade(correct=False, reason="refused a question that has an answer")
    if question.answer_type == "rows":
        return _grade_rows(question, run)
    return _grade_scalar(question, run)
