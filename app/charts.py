"""Turn the rows of a query into a chart. No Streamlit in here, so it can be tested alone.

The agent says which kind of chart fits its answer (bar, line, table or none). It does not
say which columns to plot, so this file works that out from the data:

  x      for a line, the column that looks like time (a date, a year, '2026-02');
         for bars, the first column of text (the names being compared)
  y      one numeric column: the one whose name sounds like the answer (a price, an
         average, a percentage) rather than a count of observations; never an id
  series for a line, a text column with few distinct values (one line per chain)

When the data cannot carry the chart (one row, no numeric column, two rows for the same
point) there is no chart, and the page shows the table instead: a wrong chart is worse than
none.
"""

import re
from datetime import date, datetime
from decimal import Decimal

import pandas as pd
import plotly.express as px
from plotly.graph_objects import Figure

from agent.models import SqlResult

MAX_BARS = 30
MAX_SERIES = 8
UNKNOWN = "(unknown)"  # shown for NULL names: locations.chain is NULL for independent shops

# Words that say "this is the figure the question is about", most specific first. They are
# looked for anywhere in the name: median_eur_per_l, avg_price, pct_discounted.
_VALUE_HINTS = ("per_unit", "per_l", "per_kg", "median", "mean", "avg", "price", "eur", "pct")
_VALUE_HINTS += ("percent", "change", "diff", "ratio", "share", "discount")
# Names that only count rows. Whole words of the name, so that "pct_discounted" and
# "last_observed_price" are not taken for counts because they contain "count" and "obs".
_COUNT_WORDS = frozenset({"n", "nb", "num", "count", "obs", "observations", "rows", "samples"})
# A bare plural is a count too: "count(*) AS prices" is how these queries are usually written.
_COUNT_NAMES = frozenset({"prices", "products", "stores", "locations", "chains", "number"})
_ID_NAME = re.compile(r"(^|_)(id|code|barcode|ean)$")
_TIME_WORDS = frozenset({"date", "day", "week", "month", "quarter", "year", "period"})
_TIME_TEXT = re.compile(r"\d{4}(-\d{2}){0,2}")


def to_frame(result: SqlResult) -> pd.DataFrame:
    """The rows as a DataFrame, with PostgreSQL's exact decimals turned into numbers.

    A whole Decimal becomes an int: extract(year FROM date) returns 2024 as a decimal, and
    as a float it would be labelled "2024.0".
    """
    rows = [[_number(value) for value in row] for row in result.rows]
    return pd.DataFrame(rows, columns=_unique(result.columns))


def _number(value: object) -> object:
    if not isinstance(value, Decimal) or not value.is_finite():
        return None if isinstance(value, Decimal) else value
    return int(value) if value == value.to_integral_value() else float(value)


def _unique(names: list[str]) -> list[str]:
    """Two columns may share a name in SQL (avg, avg); a DataFrame needs them apart."""
    used: set[str] = set()
    unique = []
    for name in names:
        candidate, number = name, 1
        # A generated "avg_2" must not collide with a real column called avg_2 either.
        while candidate in used or (number > 1 and candidate in names):
            number += 1
            candidate = f"{name}_{number}"
        used.add(candidate)
        unique.append(candidate)
    return unique


def _values(frame: pd.DataFrame, column: str) -> list:
    return [value for value in frame[column].tolist() if value is not None and value == value]


def _is_number(value: object) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _has_value_hint(name: str) -> bool:
    return any(hint in name for hint in _VALUE_HINTS)


def _is_count(name: str) -> bool:
    return name in _COUNT_NAMES or bool(_COUNT_WORDS & set(name.split("_")))


def numeric_columns(frame: pd.DataFrame) -> list[str]:
    """Columns where every value is a number (booleans and all-empty columns are not)."""
    return [
        column
        for column in frame.columns
        if (values := _values(frame, column)) and all(_is_number(v) for v in values)
    ]


def time_column(frame: pd.DataFrame) -> str | None:
    """The column to put on the x axis of a line: dates, years or 'YYYY-MM' texts.

    A column of whole numbers counts only if its name is a time word (year, month, quarter)
    and its values look like one. "days_observed" and "price_last_month" are figures.
    """
    for column in frame.columns:
        values = _values(frame, column)
        if not values:
            continue
        if all(isinstance(v, date | datetime) for v in values):
            return column
        if all(isinstance(v, str) and _TIME_TEXT.fullmatch(v) for v in values):
            return column
        name = column.lower()
        named_as_time = bool(_TIME_WORDS & set(name.split("_"))) and not _has_value_hint(name)
        if named_as_time and not _is_count(name) and all(_is_time_number(v) for v in values):
            return column
    return None


def _is_time_number(value: object) -> bool:
    if not _is_number(value) or not float(value).is_integer():
        return False
    return 1 <= value <= 53 or 1900 <= value <= 2100


def value_columns(frame: pd.DataFrame, x: str | None = None) -> list[str]:
    """Numeric columns that can go on the y axis, the most likely answer first.

    Ids are left out: a store id drawn as the height of a bar means nothing.
    """
    candidates = [
        column
        for column in numeric_columns(frame)
        if column != x and not _ID_NAME.search(column.lower())
    ]

    def rank(column: str) -> tuple[int, int]:
        name = column.lower()
        if _is_count(name):
            return (2, 0)
        for position, hint in enumerate(_VALUE_HINTS):
            if hint in name:
                return (0, position)
        return (1, 0)

    return sorted(candidates, key=rank)


def label_column(frame: pd.DataFrame, exclude: tuple[str | None, ...] = ()) -> str | None:
    """The first column of text: the names of the things being compared."""
    for column in frame.columns:
        values = _values(frame, column)
        if column not in exclude and values and all(isinstance(v, str) for v in values):
            return column
    return None


def build_chart(kind: str, result: SqlResult | None, value: str | None = None) -> Figure | None:
    """The chart the agent asked for, or None when the data cannot carry it.

    `value` is the numeric column to plot; by default the first of value_columns().
    """
    if kind not in ("bar", "line") or result is None or result.row_count < 2:
        return None
    frame = to_frame(result)
    if kind == "line":
        return _line(frame, value)
    return _bar(frame, value)


def _line(frame: pd.DataFrame, value: str | None) -> Figure | None:
    x = time_column(frame)
    values = value_columns(frame, x)
    if x is None or not values:
        return _bar(frame, value)  # "evolution" answered with two named periods, say
    y = value if value in values else values[0]
    shown = frame.copy()
    series = label_column(shown, exclude=(x,))
    if series is not None:
        shown[series] = shown[series].fillna(UNKNOWN)
        if not 1 < shown[series].nunique() <= MAX_SERIES:
            series = None
    # One point per date and line. Two (a year split in quarters, two products per chain)
    # would be joined into a zigzag that means nothing.
    if shown.duplicated([x] if series is None else [x, series]).any():
        return None
    figure = px.line(shown.sort_values(x), x=x, y=y, color=series, markers=True)
    if not all(isinstance(v, date | datetime) for v in _values(shown, x)):
        figure.update_xaxes(type="category")  # years are labels, not 2,024.5
    return _tidy(figure)


def _bar(frame: pd.DataFrame, value: str | None) -> Figure | None:
    x = label_column(frame) or time_column(frame)
    values = value_columns(frame, x)
    if x is None or not values:
        return None
    y = value if value in values else values[0]
    labels = frame[x].map(lambda v: UNKNOWN if v is None or v != v else str(v))
    if labels.duplicated().any():
        return None  # one bar per name: the same name twice needs a table
    shown = frame.assign(**{x: labels}).head(MAX_BARS)
    if shown[x].str.len().max() > 14:
        # Long names (products) read better on horizontal bars, first row on top.
        figure = px.bar(shown.iloc[::-1], x=y, y=x, orientation="h")
        figure.update_yaxes(type="category")
    else:
        figure = px.bar(shown, x=x, y=y)
        figure.update_xaxes(type="category")
    return _tidy(figure)


def _tidy(figure: Figure) -> Figure:
    figure.update_layout(margin={"l": 10, "r": 10, "t": 30, "b": 10}, height=380)
    return figure
