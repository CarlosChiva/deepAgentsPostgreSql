"""Health check helpers for the DeepAgents Chat API.

Provides individual health probes (postgres, checkpointer, agent) and a
composite ``check_health()`` utility that combines them into a single status
dictionary suitable for the ``/health`` endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from psycopg import connect
from psycopg.pq import ConnStatus

from app.config import settings

logger = logging.getLogger(__name__)


def check_postgres() -> str:
    """Ping PostgreSQL with a short-lived synchronous connection.

    Creates a fresh connection, runs ``SELECT 1``, then closes it
    immediately — no pool resources are consumed.
    """
    conn = None
    try:
        conn = connect(settings.postgres_url)
        status = conn.info.status
        return "ok" if status == ConnStatus.IDLE else "error"
    except Exception as exc:
        logger.warning("PostgreSQL probe failed: %s", exc)
        return "error"
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def check_checkpointer() -> str:
    """Verify the checkpointer can be obtained and its connection is idle."""
    try:
        # Lazy import to avoid circular dependencies at module load time.
        from app.database import _checkpointer, _checkpointer_initialized

        if not _checkpointer_initialized:
            return "error"

        # _checkpointer is the already-initialized global (set during startup).
        if _checkpointer is None:
            return "error"

        # PostgresSaver exposes ``.conn`` (not ``get_conn()``).
        conn = _checkpointer.conn
        return "ok" if conn.info.status == ConnStatus.IDLE else "error"
    except Exception as exc:
        logger.warning("Checkpointer probe failed: %s", exc)
        return "error"


def check_agent() -> str:
    """Verify the DeepAgent singleton can be retrieved.

    Uses the module-level ``_agent`` global directly to avoid the
    ``RuntimeError: asyncio.run() cannot be called from a running event loop``
    that would occur when calling ``get_agent()`` (a coroutine) during a
    synchronous health check inside FastAPI.
    """
    try:
        # Lazy import to avoid circular dependencies at module load time.
        from app.agent import _agent  # noqa: E402

        if _agent is None:
            return "error"
        return "ok"
    except Exception as exc:
        logger.warning("Agent probe failed: %s", exc)
        return "error"


def check_health() -> dict:
    """Run all probes and return a composite health dictionary.

    *All ok*       → ``"ok"``
    *Some failed*  → ``"degraded"``
    """
    results = {
        "status": "ok",
        "postgres": check_postgres(),
        "checkpointer": check_checkpointer(),
        "agent": check_agent(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if any(v == "error" for v in results.values() if v != "ok" and v is not None):
        results["status"] = "degraded"
    return results
