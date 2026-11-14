"""MCP server: the agent's two tools, for any MCP client (Claude Desktop, Claude Code...).

Run it from the repo root, with the database up:

    uv run python -m mcp_server.server

It speaks MCP over stdin and stdout, which is how a desktop client starts a local server.

There is no model in this file. The client brings its own; the server only describes the
database and runs read-only queries, through the very functions the agent uses
(agent/tools.py). So the rules are the same for both: one SELECT over the project's tables,
a LIMIT of at most 200, a read-only role, 5 seconds, and a cap on the size of the result.

The server is built with MCPServer, the class the official Python SDK called FastMCP until
its version 2.
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from agent import tools
from agent.models import SchemaDescription, SqlResult
from agent.sql_guard import MAX_ROWS, SqlValidationError
from agent.tools import DatabaseUnavailableError, SqlExecutionError

INSTRUCTIONS = f"""\
Supermarket prices observed in French stores (Open Prices, by Open Food Facts, ODbL).
All stores are in France and all prices are in EUR. Product names are mostly in French and
category tags are in English (for example 'en:whole-milks').

Call describe_schema first: it lists the tables and the view with a comment on every
column. Then use run_sql with one PostgreSQL SELECT that has a LIMIT of at most {MAX_ROWS}.
To compare products of different sizes use the view v_price_per_unit (EUR per kg or l).
The prices are crowdsourced and coverage is uneven: say so when a result rests on few rows.
Never state a figure that did not come from a query.
"""

# Both tools only read, change nothing outside, and return the same for the same call.
READ_ONLY = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)

server = MCPServer("retail-prices", instructions=INSTRUCTIONS)


@server.tool(title="Describe the database", annotations=READ_ONLY)
def describe_schema() -> SchemaDescription:
    """List the tables and views with their columns, types and comments. Call this first."""
    try:
        return tools.describe_schema()
    except DatabaseUnavailableError as error:
        raise ToolError("The database cannot be reached. Try again later.") from error


@server.tool(title="Run a read-only SQL query", annotations=READ_ONLY)
def run_sql(query: str) -> SqlResult:
    """Run one read-only PostgreSQL SELECT and return its rows.

    The query must be a single SELECT over the tables that describe_schema lists, with a
    LIMIT of at most 200. On error the message says what to fix.
    """
    try:
        return tools.run_sql(query)
    except (SqlValidationError, SqlExecutionError) as error:
        # The message is written for a model: it says what was wrong with the query.
        raise ToolError(str(error)) from error
    except DatabaseUnavailableError as error:
        # The connection error names the host and the role: not for the client to see.
        raise ToolError("The database cannot be reached. Try again later.") from error


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
