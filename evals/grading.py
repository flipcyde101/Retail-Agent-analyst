"""Decide whether the agent's answer to one question is right.

Pure functions: no model, no database. The rules, by answer_type:

  number   the expected figure (within 1 %; exact if it is a year) is in the agent's answer
           text, or in the first row of the result of its query
  text     the expected value is in the answer text, or in the first row of the result
  rows     every expected row is found in the result of the agent's query; if the question
           is ordered, in the same order; if a single row is expected ("which chain is the
           cheapest?"), it must be the first row of the agent's result. Failing that, an
           unordered answer is right if the text states every name next to its own figure
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

# A space (plain, no-break or narrow) separates thousands only when exactly three digits
# follow it: "44 736" is one number, "In 2025, 30,805 prices" is two.
_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0\u202f\u2009]\d{3})+(?![\d])(?:[.,]\d+)?|\d+(?:[.,]\d+)*")
_THOUSANDS = re.compile(r"\d{1,3}([.,]\d{3})+")


class Grade(BaseModel):
    correct: bool | None  # None: not graded automatically
    reason: str


def close(a: float, b: float) -> bool:
    return math.isclose(a, b, rel_tol=RELATIVE_TOLERANCE, abs_tol=ABSOLUTE_TOLERANCE)


def number_matches(expected: int | float, value: float) -> bool:
    """Within 1 %, except for a year: 1 % of 2024 is twenty years, so a year must be exact."""
    if isinstance(expected, int) and 1900 <= expected <= 2100:
        return value == expected
    return close(float(expected), value)


_SPACES = re.compile(r"[ \u00a0\u202f\u2009]")


def _readings(token: str) -> list[float]:
    """The numbers one written token can mean: "1.234" is 1234 or 1.234."""
    joined = _SPACES.sub("", token)
    found: list[float] = []
    if joined != token:
        # "5 100 g packs" may be 5,100 or a 5 followed by 100: keep the parts as well.
        found.extend(n for part in _SPACES.split(token) for n in _readings(part))
    readings = set()
    if "," in joined and "." in joined:
        decimal_mark = max(",", ".", key=joined.rfind)
        thousands_mark = "," if decimal_mark == "." else "."
        readings.add(joined.replace(thousands_mark, "").replace(decimal_mark, "."))
    elif "," in joined or "." in joined:
        mark = "," if "," in joined else "."
        if _THOUSANDS.fullmatch(joined):
            readings.add(joined.replace(mark, ""))
        if joined.count(mark) == 1:
            readings.add(joined.replace(mark, "."))
    else:
        readings.add(joined)
    for reading in sorted(readings):
        try:
            found.append(float(reading))
        except ValueError:
            continue
    return found


def numbers_with_positions(text: str) -> list[tuple[int, list[float]]]:
    """Where each written figure starts, with the numbers it can mean."""
    return [(match.start(), _readings(match.group())) for match in _NUMBER.finditer(text)]


def numbers_in_text(text: str) -> list[float]:
    """Every figure written in a sentence, as a non-negative number.

    "30,805", "30 805" and "30.805" are all thirty thousand; "2,76" is 2.76. When a token
    is ambiguous ("1.234" can be one thousand or one point two), both readings are returned.
    """
    return [number for _, readings in numbers_with_positions(text) for number in readings]


def cell_matches(expected: Any, cell: Any) -> bool:
    """One expected value against one cell of the agent's result."""
    if isinstance(expected, bool) or isinstance(cell, bool):
        return expected == cell
    if isinstance(expected, int | float):
        if isinstance(cell, date):  # a year asked for, a date returned
            return expected == cell.year
        if isinstance(cell, int | float | Decimal):
            return number_matches(expected, float(cell))
        try:
            return number_matches(expected, float(str(cell)))
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
        if any(number_matches(abs(expected), n) for n in numbers_in_text(run.answer.answer)):
            return Grade(correct=True, reason="expected value is in the answer text")
    elif str(expected).casefold() in run.answer.answer.casefold():
        return Grade(correct=True, reason="expected value is in the answer text")
    return Grade(correct=False, reason=f"expected {expected!r} not found in answer or result")


def _name_of(row: list) -> str:
    return next(str(v) for v in row if isinstance(v, str)).casefold()


def _position_of_figures(row: list, piece: str) -> int | None:
    """Where the row's first figure is in the piece, if all its figures are there."""
    numbers = numbers_with_positions(piece)
    positions = []
    for value in row:
        if isinstance(value, str):
            continue
        where = [
            start
            for start, readings in numbers
            if any(number_matches(abs(value), n) for n in readings)
        ]
        if not where:
            return None
        positions.append(where[0])
    return min(positions) if positions else None


def rows_stated_in_text(expected: list[list], text: str) -> bool:
    """Every row is in the text, each figure next to its own name.

    The text is cut at the first mention of each expected name. The figures of a row must
    sit in the piece right after its name ("Carrefour 24.94 and E.Leclerc 19.17") or in the
    piece right before it ("19.17 at E.Leclerc"). When two names share the piece between
    them ("Monoprix 4.88 versus 2.51 for Lidl"), the figure of the first name must come
    first. Both names and both figures somewhere in the text is not enough: they could be
    swapped.
    """
    folded = text.casefold()
    by_position = sorted(expected, key=lambda row: folded.find(_name_of(row)))
    starts = [folded.find(_name_of(row)) for row in by_position]
    if any(start < 0 for start in starts):
        return False
    ends = [start + len(_name_of(row)) for start, row in zip(starts, by_position, strict=True)]
    # pieces[i] is the text before name i; pieces[i + 1] is the text after it.
    cuts = [0, *ends]
    pieces = [text[cut:start] for cut, start in zip(cuts, [*starts, len(text)], strict=True)]

    previous_after: int | None = None  # where the previous name's figure is, if it follows it
    for index, row in enumerate(by_position):
        before = _position_of_figures(row, pieces[index])
        after = _position_of_figures(row, pieces[index + 1])
        # The piece before this name is the piece after the previous one: no crossing.
        if before is not None and previous_after is not None and before <= previous_after:
            before = None
        if before is not None:
            previous_after = None
        elif after is not None:
            previous_after = after
        else:
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
    stated = has_figures and rows_stated_in_text(question.expected, run.answer.answer)
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
