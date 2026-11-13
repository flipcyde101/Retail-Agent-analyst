"""Pydantic models for what the tools return.

Why models and not dicts: the tool result is sent to the LLM as JSON and, on day 9, the same
tools are exposed over MCP. A model fixes the shape once, validates it, and generates the
JSON schema that both PydanticAI and MCP publish to the client.
"""

from typing import Any

from pydantic import BaseModel, Field


class ColumnInfo(BaseModel):
    name: str
    type: str
    comment: str | None = None


class TableInfo(BaseModel):
    name: str
    kind: str = Field(description="'table' or 'view'")
    comment: str | None = None
    columns: list[ColumnInfo]


class SchemaDescription(BaseModel):
    tables: list[TableInfo]


class SqlResult(BaseModel):
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
