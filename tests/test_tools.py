"""Specification of describe_schema and run_sql. Needs a loaded database.

These tests are skipped when PostgreSQL is not reachable (docker compose up -d). They do not
depend on how much data there is, so they also run on the sample rows of data/sample.py,
which is what continuous integration loads.
"""

import os

import psycopg
import pytest

from agent.db import connect
from agent.sql_guard import SqlValidationError
from agent.tools import SqlExecutionError, describe_schema, run_sql


def _database_is_up() -> bool:
    try:
        connect().close()
    except (psycopg.Error, KeyError):
        # In continuous integration the database is part of the job: not reaching it is a
        # failure to show, not a reason to skip these tests and report success.
        if os.environ.get("CI"):
            raise
        return False
    return True


pytestmark = pytest.mark.skipif(not _database_is_up(), reason="database not reachable")


# --- describe_schema ---------------------------------------------------------------


def test_schema_lists_the_three_tables_and_the_view():
    schema = describe_schema()
    kinds = {t.name: t.kind for t in schema.tables}
    assert kinds == {
        "products": "table",
        "locations": "table",
        "prices": "table",
        "v_price_per_unit": "view",
    }


def test_schema_includes_types_and_comments_in_column_order():
    prices = next(t for t in describe_schema().tables if t.name == "prices")
    assert [c.name for c in prices.columns][:3] == ["id", "product_code", "location_id"]
    price = next(c for c in prices.columns if c.name == "price")
    assert price.type == "numeric"
    assert "EUR" in price.comment
    assert "observed" in prices.comment


# --- run_sql -----------------------------------------------------------------------


def test_run_sql_returns_columns_and_rows():
    result = run_sql("SELECT code, name FROM products ORDER BY code LIMIT 3")
    assert result.columns == ["code", "name"]
    assert result.row_count == 3
    assert len(result.rows) == 3
    assert all(len(row) == 2 for row in result.rows)


def test_run_sql_result_can_be_serialised_to_json():
    # Decimals and dates must survive the trip to the model.
    result = run_sql("SELECT price, date FROM prices ORDER BY id LIMIT 1")
    assert '"rows"' in result.model_dump_json()


def test_run_sql_validates_before_touching_the_database():
    with pytest.raises(SqlValidationError):
        run_sql("UPDATE prices SET price = 0")
    with pytest.raises(SqlValidationError):
        run_sql("SELECT * FROM prices")


def test_run_sql_reports_postgres_errors_with_their_message():
    with pytest.raises(SqlExecutionError, match="no_such_column"):
        run_sql("SELECT no_such_column FROM prices LIMIT 1")


def test_run_sql_cancels_slow_queries():
    with pytest.raises(SqlExecutionError, match="timeout"):
        # A hundred million numbers to count: far more than 200 ms of work, whatever
        # the size of the tables.
        run_sql("SELECT count(*) FROM generate_series(1, 100000000) LIMIT 1", timeout_ms=200)


def test_run_sql_cannot_write_even_if_validation_is_bypassed(monkeypatch):
    # Second barrier: with the guard switched off, the database role still refuses.
    monkeypatch.setattr("agent.tools.validate_sql", lambda query: query)
    with pytest.raises(SqlExecutionError):
        run_sql("DELETE FROM prices")


# --- limits on what comes back -----------------------------------------------------


def test_run_sql_refuses_a_result_too_large_to_send_to_a_model():
    # One row with 660,000 characters in it: the row limit does not catch this.
    with pytest.raises(SqlExecutionError, match="too large"):
        run_sql("SELECT string_agg(md5(n::text), ',') FROM generate_series(1, 20000) n LIMIT 1")


def test_run_sql_caps_the_rows_itself_if_the_validator_lets_too_many_through(monkeypatch):
    monkeypatch.setattr("agent.tools.validate_sql", lambda query: query)
    with pytest.raises(SqlExecutionError, match="more than 200 rows"):
        run_sql("SELECT n FROM generate_series(1, 500) n")


def test_run_sql_turns_unusual_types_into_text_instead_of_failing_later():
    result = run_sql("SELECT int4range(1, 5), interval '2 days' LIMIT 1")
    assert result.rows == [["[1, 5)", "2 days, 0:00:00"]]
    assert result.model_dump_json()
    # Inside an array too: found in review, it broke the JSON of the result.
    inside = run_sql("SELECT ARRAY[int4range(1, 5)], ARRAY[decode('ff', 'hex')] LIMIT 1")
    assert inside.rows == [[["[1, 5)"], ["ff"]]]
    assert inside.model_dump_json()


@pytest.mark.skipif(
    bool(os.environ.get("AGENT_DATABASE_URL")),
    reason="a hosted database does not let its owner revoke built-in functions",
)
def test_the_role_cannot_call_the_functions_revoked_from_it():
    # The second barrier for functions, checked on the server itself and not on our code.
    with connect() as conn:
        for call in ("pg_sleep(0)", "pg_terminate_backend(0)", "query_to_xml('', true, true, '')"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(f"SELECT {call}")
            conn.rollback()
