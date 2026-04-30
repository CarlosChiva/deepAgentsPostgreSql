"""Backend-related patterns unified.

Provides helpers for creating tenant-specific backend configurations
and the standard agent build pipeline.
"""

from __future__ import annotations

from typing import Any

from app.core.config import settings


def build_tenant_backend_config(user_id: str) -> dict[str, Any]:
    """Build a backend config dict for a tenant.

    Returns a dict that can be passed to ``PostgresBackend(config=…)``
    or used to construct a ``PostgresConfig``.
    """
    db_name = f"{settings.TENANT_PREFIX}{user_id}"
    return {
        "host": settings.POSTGRES_HOST,
        "port": settings.POSTGRES_PORT,
        "database": db_name,
        "user": settings.POSTGRES_USER,
        "password": settings.POSTGRES_PASSWORD,
        "table": "agent_files",
    }


def build_backend_for_tenant(
    user_id: str,
    backend_class: Any = None,
) -> Any:
    """Build a backend instance for a tenant database.

    Parameters
    ----------
    user_id:
        The tenant/user identifier.
    backend_class:
        Backend class to instantiate (defaults to ``PostgresBackend`` from
        ``deepagents_backends``).

    Returns
    -------
    An initialised backend instance.
    """
    if backend_class is None:
        from deepagents_backends import PostgresBackend
        backend_class = PostgresBackend

    config_dict = build_tenant_backend_config(user_id)

    # Use keyword args to build the config object
    from deepagents_backends import PostgresConfig
    config = PostgresConfig(**config_dict)

    backend = backend_class(config=config)
    return backend
