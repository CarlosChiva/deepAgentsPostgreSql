"""Shared database helpers for checkpointer and store.

Provides a single shared PostgreSQL connection string and a function
to initialize the checkpointer and store tables once on startup.
"""

from __future__ import annotations

import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from app.config import settings

logger = logging.getLogger(__name__)

# ── module-level shared state ──────────────────────────────────────────────

_checkpointer: AsyncPostgresSaver | None = None
_checkpointer_initialized: bool = False
_store: AsyncPostgresStore | None = None
_store_initialized: bool = False


# ── public API ──────────────────────────────────────────────────────────────

def get_db_uri() -> str:
    """Return the shared database URI."""
    return settings.postgres_uri


async def init_db_tables() -> None:
    """Initialize checkpointer and store tables in the shared PostgreSQL database.

    Idempotent — running multiple times is safe (duplicate‑create errors are ignored).
    """
    global _checkpointer, _checkpointer_initialized
    global _store, _store_initialized

    # ── checkpointer ───────────────────────────────────────────────────────
    try:
        async with AsyncPostgresSaver.from_conn_string(get_db_uri()) as saver:
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

    # ── store ────────────────────────────────────────────────────────────────
    try:
        async with AsyncPostgresStore.from_conn_string(get_db_uri()) as store:
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
