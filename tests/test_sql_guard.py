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


# --- found by trying to break it: a read-only SELECT can still do harm ---------------


def test_rejects_a_nul_byte_that_hides_the_limit_from_the_server():
    # sqlglot reads past the NUL and sees the LIMIT; PostgreSQL stops at it and runs
    # "SELECT id FROM prices --", which returned the whole table.
    with pytest.raises(SqlValidationError, match="NUL"):
        validate_sql("SELECT id FROM prices --\x00\nLIMIT 5")


@pytest.mark.parametrize(
    "query",
    [
        "SELECT usename, query FROM pg_stat_activity LIMIT 20",
        "SELECT name, setting FROM pg_settings LIMIT 20",
        "SELECT table_name FROM information_schema.tables LIMIT 20",
        "SELECT * FROM other_schema.prices LIMIT 5",
        "SELECT * FROM (SELECT rolname FROM pg_roles) r LIMIT 5",
    ],
)
def test_rejects_tables_that_are_not_part_of_the_project(query):
    with pytest.raises(SqlValidationError, match="not available"):
        validate_sql(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT pg_terminate_backend(123) LIMIT 1",
        "SELECT pg_catalog.pg_sleep(60) LIMIT 1",
        'SELECT "pg_sleep"(60) LIMIT 1',
        "SELECT query_to_xml('SELECT * FROM prices', true, false, '') LIMIT 1",
        "SELECT set_config('statement_timeout', '0', false) LIMIT 1",
        "SELECT repeat('x', 500000000) LIMIT 1",
        "SELECT lpad('x', 500000000) LIMIT 1",
        "SELECT lo_import('/etc/passwd') LIMIT 1",
    ],
)
def test_rejects_functions_that_act_on_the_server(query):
    with pytest.raises(SqlValidationError, match="not allowed"):
        validate_sql(query)


@pytest.mark.parametrize(
    "query",
    [
        "SELECT name FROM products LIMIT 10; -- ten of them",
        "SELECT name FROM products LIMIT 10; /* done */",
        "WITH t AS (SELECT chain FROM locations) SELECT * FROM t LIMIT 5",
        "SELECT * FROM public.prices LIMIT 5",
        "SELECT tag, count(*) FROM products, unnest(categories) AS tag GROUP BY tag LIMIT 5",
        "SELECT d FROM generate_series(1, 12) AS d LIMIT 12",
        "SELECT date_trunc('month', date), round(avg(price)::numeric, 2) FROM prices "
        "GROUP BY 1 LIMIT 5",
    ],
)
def test_accepts_ordinary_analysis_queries(query):
    assert validate_sql(query)


def test_fetch_first_gets_a_message_that_says_what_to_write():
    with pytest.raises(SqlValidationError, match="instead of FETCH FIRST"):
        validate_sql("SELECT name FROM products FETCH FIRST 10 ROWS ONLY")
