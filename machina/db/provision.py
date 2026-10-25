"""Small, explicit database provisioning helpers.

The installers and ``machina init`` use this only when PostgreSQL is already
reachable but the target database (or pgvector) is missing. Container
creation remains in ``machina.db.lifecycle``.
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import asyncpg


def database_name(dsn: str) -> str:
    """Return the database name from a PostgreSQL DSN."""
    return (urlsplit(dsn).path or "/machina").lstrip("/") or "machina"


async def ensure_database(dsn: str, *, timeout: float = 15.0) -> None:
    """Create the target database and enable the required extensions.

    Raises the original asyncpg error when the server is unreachable, the
    credentials cannot create a database, or pgvector is not installed.
    """
    parts = urlsplit(dsn)
    name = database_name(dsn)
    admin_url = urlunsplit(parts._replace(path="/postgres"))

    admin = await asyncpg.connect(admin_url, timeout=timeout)
    try:
        exists = await admin.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if not exists:
            safe = name.replace('"', '""')
            await admin.execute(f'CREATE DATABASE "{safe}"')
    finally:
        await admin.close()

    conn = await asyncpg.connect(dsn, timeout=timeout)
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    finally:
        await conn.close()
