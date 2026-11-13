"""Validate SQL written by the model before it reaches the database.

Rules (tests/test_sql_guard.py is the specification):
  1. It parses as PostgreSQL.
  2. It is exactly one statement.
  3. That statement is a query (SELECT, WITH ... SELECT, UNION ...).
  4. It contains no write or DDL anywhere, not even inside a CTE, and no SELECT ... INTO.
  5. It has a top-level LIMIT with a number between 1 and MAX_ROWS.

The checks run on the syntax tree built by sqlglot, not on the text. Searching the text for
"UPDATE" fails both ways: it rejects WHERE name ILIKE '%update%' and it misses a DELETE
hidden inside a WITH.

The error messages are read by the model, not by a person: each one says what is wrong and
how to fix it, because the agent uses that text to correct its query and retry.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_ROWS = 200

# Node types that write data or change the schema. exp.Command is what sqlglot returns for
# statements it does not model (EXPLAIN ANALYZE, VACUUM...): unknown means not allowed.
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Into,
    exp.Command,
)


class SqlValidationError(ValueError):
    """The query broke a rule. The message is sent back to the model."""


def validate_sql(query: str) -> str:
    """Return the query (without trailing semicolon) if it is safe, else raise."""
    # 1. It parses. A trailing ";" yields an empty statement (None), which we drop.
    try:
        statements = [s for s in sqlglot.parse(query, read="postgres") if s is not None]
    except SqlglotError as error:
        raise SqlValidationError(f"The SQL does not parse as PostgreSQL: {error}") from error

    # 2. Exactly one statement: blocks "SELECT 1; DROP TABLE prices".
    if len(statements) != 1:
        raise SqlValidationError(
            f"Send exactly one SQL statement; this contains {len(statements)}."
        )
    statement = statements[0]

    # 3. The statement is a query. exp.Query covers SELECT and UNION/INTERSECT/EXCEPT.
    if not isinstance(statement, exp.Query):
        raise SqlValidationError("Only SELECT queries are allowed: the database is read-only.")

    # 4. No write anywhere in the tree. find() walks every node, CTEs and subqueries included.
    forbidden = statement.find(*FORBIDDEN_NODES)
    if forbidden is not None:
        raise SqlValidationError(
            f"{type(forbidden).__name__.upper()} is not allowed: the database is read-only. "
            "Write a plain SELECT."
        )

    # 5. A LIMIT on the outer query, so a careless query cannot return 185,000 rows.
    limit = statement.args.get("limit")
    if limit is None:
        raise SqlValidationError(
            f"The query has no LIMIT. Add LIMIT n (at most {MAX_ROWS}) to the outer query."
        )
    value = limit.expression
    is_number = isinstance(value, exp.Literal) and value.is_int
    if not is_number or not 1 <= int(value.this) <= MAX_ROWS:
        raise SqlValidationError(f"LIMIT must be a number between 1 and {MAX_ROWS}.")

    return query.strip().rstrip(";").strip()
