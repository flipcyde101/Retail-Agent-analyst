"""The MCP server, called the way a client calls it. No model and no database.

mcp.Client connected straight to the server object speaks the real protocol in memory:
the list of tools, their schemas and the shape of results and errors are what Claude
Desktop would receive. One last test starts the server as a process, as a client does.
"""

import sys

import pytest
from mcp import Client, StdioServerParameters

from agent import tools
from agent.models import ColumnInfo, SchemaDescription, SqlResult, TableInfo
from agent.sql_guard import validate_sql
from agent.tools import DatabaseUnavailableError, SqlExecutionError
from mcp_server.server import server

pytestmark = pytest.mark.anyio

ROWS = SqlResult(columns=["chain", "avg_price"], rows=[["Auchan", 2.5]], row_count=1)
SCHEMA = SchemaDescription(
    tables=[
        TableInfo(
            name="prices",
            kind="table",
            comment="One observed shelf price.",
            columns=[ColumnInfo(name="price", type="numeric", comment="In EUR.")],
        )
    ]
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def fake_database(monkeypatch):
    """Keep the real validation, replace only the trip to PostgreSQL."""

    def fake_run_sql(query: str) -> SqlResult:
        validate_sql(query)
        if "no_such_column" in query:
            raise SqlExecutionError('column "no_such_column" does not exist')
        return ROWS

    monkeypatch.setattr(tools, "run_sql", fake_run_sql)
    monkeypatch.setattr(tools, "describe_schema", lambda: SCHEMA)


async def test_the_server_offers_the_two_tools_as_read_only_with_typed_results():
    async with Client(server) as client:
        listed = {tool.name: tool for tool in (await client.list_tools()).tools}

    assert set(listed) == {"describe_schema", "run_sql"}
    assert listed["run_sql"].input_schema["required"] == ["query"]
    for tool in listed.values():
        assert tool.annotations.read_only_hint is True
        assert tool.output_schema["type"] == "object"  # clients get typed data, not only text


async def test_the_server_tells_the_client_how_to_use_it():
    async with Client(server) as client:
        assert "describe_schema first" in client.instructions
        assert "Open Prices" in client.instructions  # the licence asks for the source to be named


async def test_run_sql_returns_the_rows_as_data_and_as_text():
    async with Client(server) as client:
        result = await client.call_tool(
            "run_sql", {"query": "SELECT chain, avg(price) AS avg_price FROM prices LIMIT 5"}
        )

    assert not result.is_error
    assert result.structured_content == {
        "columns": ["chain", "avg_price"],
        "rows": [["Auchan", 2.5]],
        "row_count": 1,
    }
    assert "Auchan" in result.content[0].text


async def test_describe_schema_returns_the_tables_with_their_comments():
    async with Client(server) as client:
        result = await client.call_tool("describe_schema", {})

    table = result.structured_content["tables"][0]
    assert (table["name"], table["comment"]) == ("prices", "One observed shelf price.")
    assert table["columns"][0] == {"name": "price", "type": "numeric", "comment": "In EUR."}


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("DELETE FROM prices", "read-only"),
        ("SELECT * FROM prices", "LIMIT"),
        ("SELECT usename FROM pg_stat_activity LIMIT 5", "not available"),
        ("SELECT pg_sleep(60) LIMIT 1", "not allowed"),
        ("SELECT no_such_column FROM prices LIMIT 1", "no_such_column"),
    ],
)
async def test_a_bad_query_comes_back_as_an_error_the_model_can_act_on(query, message):
    # The same rules as for the agent: the server adds no way around the SQL check.
    async with Client(server) as client:
        result = await client.call_tool("run_sql", {"query": query})

    assert result.is_error
    assert message in result.content[0].text


async def test_a_database_that_is_down_does_not_leak_where_it_is(monkeypatch):
    def unreachable(query: str) -> SqlResult:
        raise DatabaseUnavailableError("Cannot connect: host db.internal user agent_ro")

    monkeypatch.setattr(tools, "run_sql", unreachable)
    async with Client(server) as client:
        result = await client.call_tool("run_sql", {"query": "SELECT 1 LIMIT 1"})

    assert result.is_error
    assert "cannot be reached" in result.content[0].text
    assert "db.internal" not in result.content[0].text


async def test_the_server_starts_as_a_process_and_speaks_over_stdio():
    # What a desktop client does. Anything printed to stdout that is not protocol (a
    # banner, a stray print) breaks this, and listing the tools needs no database.
    process = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"])
    async with Client(process) as client:
        names = {tool.name for tool in (await client.list_tools()).tools}

    assert names == {"describe_schema", "run_sql"}
