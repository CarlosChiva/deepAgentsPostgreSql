"""Shared database helpers for checkpointer and store.

Provides a single shared PostgreSQL connection string and functions
to initialize the checkpointer and store tables once on startup.

Fixed: context manager bugs — the original code exited the context
manager before the tables were set up, causing the saver/store objects
to be closed prematurely.  The new code keeps the connection alive
through ``.setup()`` and only closes it after the operation completes.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── module-level shared state ─────────────────────────────

_checkpointer: AsyncPostgresSaver | None = None
_checkpointer_initialized: bool = False
_store: AsyncPostgresStore | None = None
_store_initialized: bool = False


def get_db_uri() -> str:
    """Return the shared database URI."""
    return settings.postgres_uri


async def init_db_tables() -> None:
    """Initialize checkpointer and store tables in the shared PostgreSQL database.

    Idempotent — running multiple times is safe (duplicate-create errors are ignored).
    Fixed context manager: uses ``async with`` to keep the connection alive
    through ``.setup()``.
    """
    global _checkpointer, _checkpointer_initialized
    global _store, _store_initialized

    # ── checkpointer ──────────────────────────────────────
    try:
        saver_cm = AsyncPostgresSaver.from_conn_string(get_db_uri())
        async with saver_cm as saver:
            await saver.setup()
        _checkpointer = saver
        _checkpointer_initialized = True
        logger.info("Checkpointer tables initialised")
    except Exception as exc:
        if "already exists" in str(exc) or "already initialized" in str(exc):
            _checkpointer_initialized = True
            logger.info("Checkpointer tables already initialised")
        else:
            raise

    # ── store ─────────────────────────────────────────────
    try:
        store_cm = AsyncPostgresStore.from_conn_string(get_db_uri())
        async with store_cm as store:
            await store.setup()
        _store = store
        _store_initialized = True
        logger.info("Store tables initialised")
    except Exception as exc:
        if "already exists" in str(exc) or "already initialized" in str(exc):
            _store_initialized = True
            logger.info("Store tables already initialised")
        else:
            raise


def get_checkpointer() -> AsyncPostgresSaver | None:
    """Return the shared checkpointer (may be None if not yet initialised)."""
    return _checkpointer  # type: ignore[return-value]


def get_store() -> AsyncPostgresStore | None:
    """Return the shared store (may be None if not yet initialised)."""
    return _store  # type: ignore[return-value]


# ── Tenant DB helpers ─────────────────────────────────────


async def create_tenant_database(tenant_db_name: str, superuser_uri: str) -> bool:
    """Create a tenant database if it does not already exist.

    Parameters
    ----------
    tenant_db_name:
        The name of the database to create (e.g. ``deepagent_user-abc``).
    superuser_uri:
        A connection URI to the superuser database (e.g. ``postgres``).

    Returns
    -------
    ``True`` if the database was created, ``False`` if it already existed.
    """
    import psycopg

    conn = await psycopg.AsyncConnection.connect(superuser_uri, autocommit=True)
    async with conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (tenant_db_name,)
        )
        if not await cur.fetchone():
            await cur.execute(f'CREATE DATABASE "{tenant_db_name}"')
            logger.info("Created database %s", tenant_db_name)
            return True
    return False


async def drop_tenant_database(tenant_db_name: str, superuser_uri: str) -> bool:
    """Drop a tenant database.

    Returns
    -------
    ``True`` if the database was dropped, ``False`` if it did not exist.
    """
    import psycopg

    conn = await psycopg.AsyncConnection.connect(superuser_uri, autocommit=True)
    async with conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (tenant_db_name,)
        )
        if await cur.fetchone():
            await cur.execute(f'DROP DATABASE IF EXISTS "{tenant_db_name}"')
            logger.info("Dropped database %s", tenant_db_name)
            return True
    return False


async def ensure_tenant_schema(db_uri: str) -> None:
    """Ensure checkpointer and store tables exist in *db_uri*."""
    async with AsyncPostgresSaver.from_conn_string(db_uri) as saver:
        await saver.setup()
    async with AsyncPostgresStore.from_conn_string(db_uri) as store:
        await store.setup()
