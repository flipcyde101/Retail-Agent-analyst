"""Validate SQL written by the model before it reaches the database.

Rules (tests/test_sql_guard.py is the specification):
  1. It has no NUL byte and it parses as PostgreSQL.
  2. It is exactly one statement.
  3. That statement is a query (SELECT, WITH ... SELECT, UNION ...).
  4. It contains no write or DDL anywhere, not even inside a CTE, and no SELECT ... INTO.
  5. It reads only the tables and the view of this project (or its own CTEs).
  6. It calls no function that acts on the server instead of computing a value.
  7. It has a top-level LIMIT with a number between 1 and MAX_ROWS.

The checks run on the syntax tree built by sqlglot, not on the text. Searching the text for
"UPDATE" fails both ways: it rejects WHERE name ILIKE '%update%' and it misses a DELETE
hidden inside a WITH.

This is the first barrier, not the only one. The database role can only read and has the
dangerous functions revoked (data/load.py), and run_sql caps the rows and the size of what
comes back (agent/tools.py). Rules 5 and 6 exist because a read-only SELECT can still do
harm on a shared demo: list or kill other sessions, or build a result that exhausts memory.

The error messages are read by the model, not by a person: each one says what is wrong and
how to fix it, because the agent uses that text to correct its query and retry.
"""

import sqlglot
from sqlglot import exp
from sqlglot.errors import SqlglotError

MAX_ROWS = 200

# The objects of sql/schema.sql and sql/views.sql. Everything else (pg_stat_activity,
# pg_settings, information_schema...) is none of the agent's business.
ALLOWED_TABLES = frozenset({"prices", "products", "locations", "v_price_per_unit"})

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

# Functions that do something to the server. Found by trying them: pg_terminate_backend kills
# another user's query, query_to_xml runs SQL this file never sees and ignored the timeout,
# repeat and lpad build values of any size.
FORBIDDEN_FUNCTION_PREFIXES = ("pg_", "lo_", "dblink")
FORBIDDEN_FUNCTIONS = frozenset(
    {"set_config", "nextval", "setval", "currval", "repeat", "pad", "lpad", "rpad"}
)


class SqlValidationError(ValueError):
    """The query broke a rule. The message is sent back to the model."""


def _function_name(node: exp.Func) -> str:
    # Functions sqlglot does not model keep the name as written; the others have a class.
    name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
    return name.lower()


def _is_forbidden_function(name: str) -> bool:
    return (
        name in FORBIDDEN_FUNCTIONS or name.startswith(FORBIDDEN_FUNCTION_PREFIXES) or "xml" in name
    )


def _check_tables(statement: exp.Expression) -> None:
    own_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    for table in statement.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            continue  # a function in FROM (unnest, generate_series): checked as a function
        name = table.name.lower()
        qualified = bool(table.db or table.catalog)
        in_public = not table.catalog and table.db.lower() in ("", "public")
        if (name in ALLOWED_TABLES and in_public) or (name in own_names and not qualified):
            continue
        raise SqlValidationError(
            f"Table {table.sql(dialect='postgres')} is not available. "
            f"Query only: {', '.join(sorted(ALLOWED_TABLES))}."
        )


def validate_sql(query: str) -> str:
    """Return the query (without trailing semicolon) if it is safe, else raise."""
    # 1. No NUL byte: sqlglot reads past it and PostgreSQL stops at it, so the two would
    # check and run different queries ("SELECT ... --\x00\nLIMIT 5" ran with no LIMIT).
    if "\x00" in query:
        raise SqlValidationError("The SQL contains a NUL character. Send plain SQL text.")
    # A trailing ";" yields an empty statement (None) and a comment after it a Semicolon
    # node: neither is a second statement.
    try:
        statements = [
            s
            for s in sqlglot.parse(query, read="postgres")
            if s is not None and not isinstance(s, exp.Semicolon)
        ]
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

    # 5. Only the tables of this project.
    _check_tables(statement)

    # 6. No function that acts on the server.
    for function in statement.find_all(exp.Func):
        name = _function_name(function)
        if _is_forbidden_function(name):
            raise SqlValidationError(
                f"The function {name} is not allowed. Use only functions that compute a "
                "value from the data."
            )

    # 7. A LIMIT on the outer query, so a careless query cannot return 185,000 rows.
    limit = statement.args.get("limit")
    if limit is None:
        raise SqlValidationError(
            f"The query has no LIMIT. Add LIMIT n (at most {MAX_ROWS}) at the end of the "
            "outer query, outside any parentheses."
        )
    if isinstance(limit, exp.Fetch):
        raise SqlValidationError(f"Use LIMIT n (at most {MAX_ROWS}) instead of FETCH FIRST.")
    value = limit.expression
    is_number = isinstance(value, exp.Literal) and value.is_int
    if not is_number or not 1 <= int(value.this) <= MAX_ROWS:
        raise SqlValidationError(f"LIMIT must be a number between 1 and {MAX_ROWS}.")

    return query.strip().rstrip(";").strip()
