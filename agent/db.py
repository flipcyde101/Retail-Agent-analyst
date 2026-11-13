"""Database connection for the agent: always the read-only role, never the admin."""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent


def connect() -> psycopg.Connection:
    """Open a connection as the read-only role created by data/load.py."""
    load_dotenv(ROOT / ".env")
    return psycopg.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["AGENT_DB_USER"],
        password=os.environ["AGENT_DB_PASSWORD"],
        connect_timeout=5,
    )
