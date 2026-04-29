"""Health check helpers for the DeepAgents Chat API.

Provides a single ``check_health()`` probe that checks PostgreSQL connectivity
and returns a composite status dictionary for the ``/health`` endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from psycopg import connect
from psycopg.pq import ConnStatus

from app.config import settings

logger = logging.getLogger(__name__)


def check_postgres() -> str:
    """Ping PostgreSQL with a short-lived synchronous connection.

    Creates a fresh connection, runs ``SELECT 1``, then closes immediately
    (no pool resources are consumed).
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


def check_health() -> dict[str, Any]:
    """Run all probes and return a composite health dictionary.

    *All ok*       \u2192 ``"ok"``
    *Some failed*  \u2192 ``"degraded"``
    """
    pg_status = check_postgres()
    results = {
        "status": "ok",
        "postgres": pg_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if any(v == "error" for v in results.values() if v != "ok" and v is not None):
        results["status"] = "degraded"
    return results
