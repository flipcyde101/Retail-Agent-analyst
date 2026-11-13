"""The two tools the agent can call. Plain functions: no PydanticAI or MCP in here.

Keeping them framework-free is what lets day 4 (PydanticAI agent) and day 9 (MCP server)
wrap the same code instead of duplicating it.
"""

import psycopg

from agent.db import connect
from agent.models import ColumnInfo, SchemaDescription, SqlResult, TableInfo
from agent.sql_guard import validate_sql

DEFAULT_TIMEOUT_MS = 5000

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


def describe_schema() -> SchemaDescription:
    """Tables and views of the public schema, with column types and comments."""
    tables: dict[str, TableInfo] = {}
    with connect() as conn:
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


def run_sql(query: str, timeout_ms: int = DEFAULT_TIMEOUT_MS) -> SqlResult:
    """Validate the query, run it as the read-only role and return the rows.

    Raises SqlValidationError if the query breaks a rule, SqlExecutionError if PostgreSQL
    fails (unknown column, timeout...).
    """
    query = validate_sql(query)
    try:
        with connect() as conn, conn.cursor() as cur:
            # int() guarantees a number: SET does not accept query parameters.
            cur.execute(f"SET statement_timeout = {int(timeout_ms)}")
            cur.execute(query)
            columns = [column.name for column in cur.description]
            rows = [list(row) for row in cur.fetchall()]
    except psycopg.Error as error:
        # PostgreSQL's message ("column x does not exist") is what lets the model fix it.
        raise SqlExecutionError(str(error).strip()) from error
    return SqlResult(columns=columns, rows=rows, row_count=len(rows))
