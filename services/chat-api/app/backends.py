"""PostgreSQL filesystem backend for DeepAgents via deepagents-backends.

Provides a singleton PostgresBackend that stores file content in PostgreSQL,
integrated with the application's lifecycle via initialise() / close().
"""

from __future__ import annotations

import logging
from typing import Optional

from deepagents_backends import PostgresBackend, PostgresConfig

from app.config import settings

logger = logging.getLogger(__name__)

# Singleton state
_backend: Optional[PostgresBackend] = None
_initialized: bool = False


# ---------- helpers ----------


def _build_config() -> PostgresConfig:
    """Build a PostgresConfig from application settings."""
    return PostgresConfig(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        table="agent_files",
    )


# ---------- public API ----------


async def initialize_pg_backend() -> PostgresBackend:
    """Initialise the PostgreSQL filesystem backend for DeepAgents.

    Idempotent — returns the existing backend if already initialised.
    """
    global _backend, _initialized

    if _backend is not None and _initialized:
        return _backend

    config = _build_config()
    _backend = PostgresBackend(config=config)
    await _backend.initialize()
    _initialized = True

    logger.info("PostgreSQL filesystem backend initialised (table=%s)", config.table)
    return _backend


async def close_pg_backend() -> None:
    """Close the PostgreSQL filesystem backend.

    Idempotent — no-op if not initialised.
    """
    global _backend, _initialized

    if _backend is None:
        return

    await _backend.close()
    _backend = None
    _initialized = False


async def get_pg_backend() -> Optional[PostgresBackend]:
    """Lazy accessor for the singleton PostgresBackend.

    Lazily initialises the backend on first call if it has not been
    initialised explicitly via ``initialize_pg_backend()`` yet.

    Returns *None* only if initialisation fails.
    """
    global _backend, _initialized

    if _backend is not None and _initialized:
        return _backend

    if _initialized:
        return _backend

    try:
        await initialize_pg_backend()
    except Exception:
        _backend = None
        _initialized = False
        logger.exception("Failed to lazy-initialise PostgreSQL filesystem backend")
        return None

    return _backend
