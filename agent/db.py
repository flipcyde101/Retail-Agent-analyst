"""Database connection for the agent: always the read-only role, never the admin.

Two ways to say where the database is:

  AGENT_DATABASE_URL   one connection string, as hosted PostgreSQL services give it
                       (postgresql://user:password@host/dbname?sslmode=require)
  POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, AGENT_DB_USER, AGENT_DB_PASSWORD
                       the separate values of the local Docker database

The connection string wins when both are set.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
# A hosted database that was asleep takes a few seconds to wake up; a local one answers at
# once or not at all.
CONNECT_TIMEOUT_S = 10


def connect() -> psycopg.Connection:
    """Open a connection as the read-only role created by data/load.py."""
    load_dotenv(ROOT / ".env")
    url = os.environ.get("AGENT_DATABASE_URL")
    if url:
        return psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_S)
    return psycopg.connect(
        # 127.0.0.1 and not "localhost": that name tries IPv6 first, where the container
        # does not listen, and every connection would wait for that attempt to time out.
        host=os.environ.get("POSTGRES_HOST", "127.0.0.1"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["AGENT_DB_USER"],
        password=os.environ["AGENT_DB_PASSWORD"],
        connect_timeout=CONNECT_TIMEOUT_S,
    )
