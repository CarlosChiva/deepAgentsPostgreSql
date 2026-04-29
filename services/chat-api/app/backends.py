"""Tenant-aware PostgreSQL backend helpers for DeepAgents via deepagents-backends.

Each tenant (user_id) gets its own PostgresConfig pointing to the tenant's
own database. No singleton state — every function is pure and stateless.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


# ─── Configuration ───


def build_tenant_backend_config(user_id: str, tenant_settings=None) -> PostgresConfig:
    """Build a PostgresConfig pointing to a tenant's database.

    Args:
        user_id: The tenant's user ID
        tenant_settings: Optional settings override (defaults to module-level settings)

    Returns:
        PostgresConfig with the tenant database name
    """
    if tenant_settings is None:
        from app.config import settings
        tenant_settings = settings

    from deepagents_backends import PostgresConfig  # noqa: PLC0415

    return PostgresConfig(
        host=tenant_settings.POSTGRES_HOST,
        port=tenant_settings.POSTGRES_PORT,
        database=f"{tenant_settings.TENANT_PREFIX}{user_id}",
        user=tenant_settings.POSTGRES_USER,
        password=tenant_settings.POSTGRES_PASSWORD,
        table="agent_files",
    )


# ─── Public API ───


async def build_backend_for_tenant(user_id: str, tenant_settings=None) -> PostgresBackend:
    """Instantiate and initialize a PostgresBackend for a specific tenant.

    This is a pure function — no caching, no singleton state.
    The caller (TenantManager) is responsible for caching and lifecycle.

    Args:
        user_id: The tenant's user ID
        tenant_settings: Optional settings override

    Returns:
        Initialized PostgresBackend instance
    """
    from deepagents_backends import PostgresBackend  # noqa: PLC0415

    config = build_tenant_backend_config(user_id, tenant_settings)
    backend = PostgresBackend(config=config)
    await backend.initialize()

    logger.info("PostgreSQL backend initialized for tenant=%s (table=%s)", user_id, config.table)
    return backend


async def close_tenant_backend(backend: PostgresBackend) -> None:
    """Close a tenant's backend.

    Args:
        backend: The backend instance to close
    """
    if backend is None:
        return

    await backend.close()
    logger.info("PostgreSQL backend closed for tenant")
