"""Specification of validate_sql. No database needed."""

import pytest

from agent.sql_guard import MAX_ROWS, SqlValidationError, validate_sql


@pytest.mark.parametrize(
    "query",
    [
        "SELECT name FROM products LIMIT 10",
        "select name from products limit 10;",
        f"SELECT * FROM prices LIMIT {MAX_ROWS}",
        "SELECT chain, avg(price) FROM prices p JOIN locations l ON l.id = p.location_id "
        "GROUP BY chain ORDER BY 2 LIMIT 5",
        "WITH recent AS (SELECT * FROM prices WHERE date >= '2026-01-01') "
        "SELECT count(*) FROM recent LIMIT 1",
        "SELECT name FROM products WHERE code = '1' UNION SELECT name FROM products LIMIT 5",
        "SELECT * FROM prices ORDER BY date DESC LIMIT 20 OFFSET 40",
        # Words like "update" inside a string or a column name are not writes.
        "SELECT name FROM products WHERE name ILIKE '%drop%' LIMIT 5",
    ],
)
def test_accepts_read_only_queries_with_a_limit(query):
    assert validate_sql(query)


def test_returns_the_query_without_trailing_semicolon():
    assert validate_sql("SELECT 1 LIMIT 1;").rstrip().endswith("1")


@pytest.mark.parametrize(
    "query",
    [
        "UPDATE prices SET price = 0",
        "DROP TABLE prices",
        "DELETE FROM prices",
        "INSERT INTO products (code, name) VALUES ('1', 'x')",
        "TRUNCATE prices",
        "ALTER TABLE prices ADD COLUMN x int",
        "CREATE TABLE t AS SELECT * FROM prices LIMIT 5",
        "GRANT ALL ON prices TO agent_ro",
        "EXPLAIN ANALYZE SELECT 1",
    ],
)
def test_rejects_anything_that_is_not_a_query(query):
    with pytest.raises(SqlValidationError):
        validate_sql(query)


@pytest.mark.parametrize(
    "query",
    [
        # A write hidden inside a CTE: the outer statement is a SELECT.
        "WITH gone AS (DELETE FROM prices RETURNING *) SELECT * FROM gone LIMIT 5",
        "WITH up AS (UPDATE prices SET price = 0 RETURNING *) SELECT * FROM up LIMIT 5",
        # SELECT ... INTO creates a table.
        "SELECT * INTO copy_of_prices FROM prices LIMIT 5",
    ],
)
def test_rejects_writes_hidden_inside_a_select(query):
    with pytest.raises(SqlValidationError):
        validate_sql(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT 1 LIMIT 1; DROP TABLE prices",
        "SELECT 1 LIMIT 1; SELECT 2 LIMIT 1",
    ],
)
def test_rejects_more_than_one_statement(query):
    with pytest.raises(SqlValidationError):
        validate_sql(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM prices",
        "SELECT * FROM prices LIMIT ALL",
        f"SELECT * FROM prices LIMIT {MAX_ROWS + 1}",
        "SELECT * FROM prices LIMIT 0",
        # The LIMIT must be on the outer query, not only in a subquery.
        "SELECT * FROM (SELECT * FROM prices LIMIT 5) s",
    ],
)
def test_rejects_queries_without_a_valid_limit(query):
    with pytest.raises(SqlValidationError):
        validate_sql(query)


@pytest.mark.parametrize("query", ["", "   ", "SELEC name FROM products LIMIT 5", "hello"])
def test_rejects_text_that_is_not_sql(query):
    with pytest.raises(SqlValidationError):
        validate_sql(query)


def test_the_error_tells_the_model_how_to_fix_a_missing_limit():
    with pytest.raises(SqlValidationError, match=r"(?i)limit"):
        validate_sql("SELECT * FROM prices")
