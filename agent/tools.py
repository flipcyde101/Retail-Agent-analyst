"""The two tools the agent can call. Plain functions: no PydanticAI or MCP in here.

Keeping them framework-free is what lets day 4 (PydanticAI agent) and day 9 (MCP server)
wrap the same code instead of duplicating it.
"""

from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import psycopg

from agent.db import connect
from agent.models import ColumnInfo, SchemaDescription, SqlResult, TableInfo
from agent.sql_guard import MAX_ROWS, validate_sql

DEFAULT_TIMEOUT_MS = 5000
# Characters of the result as JSON. 200 ordinary rows are far below this; what goes over is
# a query that folds a whole table into one cell (json_agg, string_agg), and that would be
# sent to the model, and billed, in full.
MAX_RESULT_CHARS = 200_000

# Types that reach the model as they are. Anything else PostgreSQL can return (ranges,
# intervals, network addresses) becomes its text form instead of breaking the JSON.
_JSON_READY = (str, int, float, bool, Decimal, date, datetime, time, list, dict, type(None))

# PostgreSQL describes itself: information_schema has the tables and columns, and
# obj_description / col_description return the COMMENTs written in sql/schema.sql.
SCHEMA_QUERY = """
SELECT
    t.table_name,
    t.table_type,
    obj_description(format('public.%I', t.table_name)::regclass)  AS table_comment,
    c.column_name,
    c.data_type,
    col_description(format('public.%I', t.table_name)::regclass, c.ordinal_position)
        AS column_comment
FROM information_schema.tables t
JOIN information_schema.columns c USING (table_schema, table_name)
WHERE t.table_schema = 'public'
ORDER BY t.table_name, c.ordinal_position
"""


class SqlExecutionError(RuntimeError):
    """PostgreSQL rejected or cancelled the query. The message is sent back to the model."""


class DatabaseUnavailableError(RuntimeError):
    """PostgreSQL cannot be reached. Not sent to the model: no rewrite of the query fixes it."""


def _connect() -> psycopg.Connection:
    try:
        return connect()
    except psycopg.OperationalError as error:
        raise DatabaseUnavailableError(f"Cannot connect to the database: {error}") from error
    except (psycopg.Error, KeyError):
        # A malformed connection string or a missing variable. psycopg quotes the string,
        # password included, in its message: it must not travel any further than here.
        raise DatabaseUnavailableError(
            "The database connection is not configured correctly."
        ) from None


def describe_schema() -> SchemaDescription:
    """Tables and views of the public schema, with column types and comments."""
    tables: dict[str, TableInfo] = {}
    with _connect() as conn:
        # One row per column; rows of the same table are grouped into one TableInfo.
        for name, table_type, comment, column, data_type, column_comment in conn.execute(
            SCHEMA_QUERY
        ):
            if name not in tables:
                kind = "view" if table_type == "VIEW" else "table"
                tables[name] = TableInfo(name=name, kind=kind, comment=comment, columns=[])
            tables[name].columns.append(
                ColumnInfo(name=column, type=data_type, comment=column_comment)
            )
    return SchemaDescription(tables=list(tables.values()))


def _plain_cell(value: Any) -> Any:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, list):  # an array column: its items may be unusual types too
        return [_plain_cell(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain_cell(item) for key, item in value.items()}
    return value if isinstance(value, _JSON_READY) else str(value)


def run_sql(query: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> SqlResult:
    """Validate the query, run it as the read-only role and return the rows.

    Raises SqlValidationError if the query breaks a rule, SqlExecutionError if PostgreSQL
    fails (unknown column, timeout...) or the result is too large to send to a model,
    DatabaseUnavailableError if the database cannot be reached.
    """
    query = validate_sql(query)
    # Connecting is outside the try: a refused connection must not look like a bad query.
    with _connect() as conn:
        try:
            with conn.cursor() as cur:
                # int() guarantees a number: SET does not accept query parameters.
                # LOCAL: for this transaction only. Through a connection pooler (hosted
                # PostgreSQL) a plain SET may land on another client's connection.
                cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
                cur.execute(query)
                columns = [column.name for column in cur.description]
                # One row more than allowed is enough to know the limit was not applied.
                # The validator already demands a LIMIT; this does not depend on it.
                fetched = cur.fetchmany(MAX_ROWS + 1)
        except psycopg.Error as error:
            # PostgreSQL's message ("column x does not exist") is what lets the model fix it.
            raise SqlExecutionError(str(error).strip()) from error
    if len(fetched) > MAX_ROWS:
        raise SqlExecutionError(f"The query returns more than {MAX_ROWS} rows. Add a LIMIT.")
    rows = [[_plain_cell(value) for value in row] for row in fetched]
    result = SqlResult(columns=columns, rows=rows, row_count=len(rows))
    size = len(result.model_dump_json())
    if size > MAX_RESULT_CHARS:
        raise SqlExecutionError(
            f"The result is too large ({size:,} characters; the limit is {MAX_RESULT_CHARS:,}). "
            "Select fewer columns or rows, and do not aggregate a whole table into one value."
        )
    return result
