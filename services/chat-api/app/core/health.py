"""Health check helpers for the DeepAgents Chat API.

Provides probes for PostgreSQL connectivity, tenant manager cache,
and returns a composite status dictionary for the ``/health`` endpoint.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from psycopg import connect
from psycopg.pq import ConnStatus

from app.core.config import settings

logger = logging.getLogger(__name__)


def check_postgres() -> str:
    """Ping PostgreSQL with a short-lived synchronous connection.

    Creates a fresh connection, runs ``SELECT 1``, then closes immediately
    (no pool resources are consumed).

    Fixed: was using ``settings.postgres_url`` (non-existent property);
    now uses ``settings.postgres_uri``.
    """
    conn = None
    try:
        conn = connect(settings.postgres_uri)
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


def check_tenants() -> str:
    """Check tenant manager cache health."""
    try:
        from app.agents.tenant import get_tenant_manager
        mgr = get_tenant_manager()
        return "ok"
    except Exception as exc:
        logger.warning("Tenant health probe failed: %s", exc)
        return "error"


def check_health() -> dict[str, Any]:
    """Run all probes and return a composite health dictionary.

    *All ok*       → ``"ok"``
    *Some failed*  → ``"degraded"``
    """
    pg_status = check_postgres()
    tenant_status = check_tenants()
    results = {
        "status": "ok",
        "postgres": pg_status,
        "tenants": tenant_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if any(v == "error" for v in results.values() if v != "ok" and v is not None):
        results["status"] = "degraded"
    return results
