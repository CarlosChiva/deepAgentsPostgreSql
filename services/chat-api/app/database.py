"""Database and persistence helpers.

Provides singleton factories for the ``PostgresSaver`` checkpointer
and ``AsyncPostgresStore``, a ``check_db_connection`` utility, and a
graceful-shutdown flag so other modules can gate on shutdown.
"""

from __future__ import annotations

import logging
import threading

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.pool import Pool

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.async_postgres import AsyncPostgresStore

from app.config import settings

logger = logging.getLogger(__name__)

# ------ singleton state ------------------------------------------------

_checkpointer: PostgresSaver | None = None
_checkpointer_initialized: bool = False
_store: AsyncPostgresStore | None = None
_store_initialized: bool = False


def get_checkpointer() -> PostgresSaver:
    """Return a singleton PostgresSaver checkpointer, lazily initializing it."""
    global _checkpointer, _checkpointer_initialized

    if _checkpointer is not None:
        return _checkpointer

    _checkpointer = PostgresSaver.from_conn_string(settings.postgres_url)

    if not _checkpointer_initialized:
        try:
            _checkpointer.setup()
            _checkpointer_initialized = True
        except (ProgrammingError, ValueError) as e:
            err_msg = str(e)
            if "already exists" not in err_msg and "already initialized" not in err_msg:
                raise

    return _checkpointer


def get_store() -> AsyncPostgresStore:
    """Return a singleton AsyncPostgresStore, lazily initializing it."""
    global _store, _store_initialized

    if _store is not None:
        return _store

    _store = AsyncPostgresStore.from_conn_string(settings.postgres_url)

    if not _store_initialized:
        try:
            _store.setup()
            _store_initialized = True
        except (ProgrammingError, ValueError) as e:
            err_msg = str(e)
            if "already exists" not in err_msg and "already initialized" not in err_msg:
                raise

    return _store


def setup_checkpointer() -> None:
    """Ensure the checkpointer is set up (idempotent, safe to call multiple times)."""
    checkpointer = get_checkpointer()
    try:
        checkpointer.setup()
    except (ProgrammingError, ValueError) as e:
        err_msg = str(e)
        if "already exists" not in err_msg and "already initialized" not in err_msg:
            raise


# ------ utility helpers ------------------------------------------------

def check_db_connection(postgres_url: str | None = None) -> bool:
    """Attempt a brief connection to PostgreSQL and ping it.

    Creates a short-lived synchronous connection, runs ``SELECT 1``,
    and closes it.  Does **not** use the pool.

    Args:
        postgres_url: Optional explicit URL.  Falls back to
            ``settings.postgres_url`` when *None*.

    Returns:
        ``True`` if the database is reachable, ``False`` otherwise.
    """
    url = postgres_url or settings.postgres_url
    from psycopg import connect  # noqa: E402
    from psycopg.pq import ConnStatus  # noqa: E402

    conn = None
    try:
        conn = connect(url, timeout=3)
        cursor = conn.cursor()
        cursor.execute(text("SELECT 1"))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        return row is not None and row[0] == 1
    except Exception:
        return False


def is_checkpointer_ready() -> bool:
    """Return ``True`` when the checkpointer has been successfully set up."""
    return _checkpointer_initialized


# ------ graceful shutdown flag -----------------------------------------

__is_shutting_down: bool = False
_shutdown_event = threading.Event()


def set_shutting_down(value: bool = True) -> None:
    """Set the graceful-shutdown flag (thread-safe)."""
    global __is_shutting_down
    __is_shutting_down = value
    if value:
        _shutdown_event.set()
    else:
        _shutdown_event.clear()


def is_shutting_down() -> bool:
    """Return the current shutdown flag value."""
    return __is_shutting_down


def close_checkpointer() -> None:
    """Close the checkpointer connection pool (if open)."""
    global _checkpointer

    if _checkpointer is None:
        return

    try:
        pool: Pool = _checkpointer._pool  # type: ignore[attr-defined]
        pool.close()
        pool.dispose()
        logger.info("Checkpointer connection pool disposed")
    except Exception as exc:
        logger.warning("Checkpointer pool cleanup failed: %s", exc)


async def close_store() -> None:
    """Gracefully close the AsyncPostgresStore connection pool."""
    global _store
    if _store is None:
        return
    try:
        await _store._pool.close()  # type: ignore[func-returns-value]
        await _store._pool.dispose()  # type: ignore[func-returns-value]
        logger.info("AsyncPostgresStore pool closed")
    except Exception as exc:
        logger.warning("Store pool cleanup failed: %s", exc)
