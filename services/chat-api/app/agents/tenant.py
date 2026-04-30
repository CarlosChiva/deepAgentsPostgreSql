"""Tenant management for per-user DeepAgent lifecycles.

Manages creation, caching, and cleanup of per-user tenant agents backed
by isolated PostgreSQL databases.  Supports LRU-style eviction, TTL-based
expiry, and asyncio lock protection against race conditions on first access.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── data classes ───────────────────────────────────────────────────


@dataclass
class TenantStore:
    """Holds per-user tenant state and resources.

    Attributes
    ----------
    user_id:
        Unique user identifier.
    tenant_db_name:
        Name of the tenant's PostgreSQL database (e.g. *deepagent_user-abc*).
    database_url:
        Full ``postgresql://`` URI for the tenant database.
    agent:
        The built :class:`deepagents.DeepAgent` instance (or ``None``).
    checkpointer:
        The live :class:`AsyncPostgresSaver` (or ``None``).
    store:
        The live :class:`AsyncPostgresStore` (or ``None``).
    backend:
        The Deep Agents backend instance (or ``None``).
    created_at:
        Time the tenant entry was first created.
    last_used_at:
        Time the tenant entry was last accessed.
    checkpointer_cm:
        The async context manager for the checkpointer, used for cleanup.
    store_cm:
        The async context manager for the store, used for cleanup.
    """

    user_id: str
    tenant_db_name: str
    database_url: str
    agent: Any = None
    checkpointer: Any = None
    store: Any = None
    backend: Any = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    checkpointer_cm: Any = None
    store_cm: Any = None


# ── URI helpers ────────────────────────────────────────────────────


def _db_uri(user_id: str) -> str:
    """Build the database URI for a given *user_id*."""
    db_name = f"{settings.TENANT_PREFIX}{user_id}"
    return f"{settings.postgres_uri}/{db_name}"


def _superuser_uri() -> str:
    """Build the connection URI for the PostgreSQL superuser database."""
    return (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/"
        f"{settings.TENANT_SUPERUSER_DB}"
    )


async def _create_tenant_db(user_id: str) -> str:
    """Create the user database if it doesn't already exist.

    Parameters
    ----------
    user_id:
        The user whose database should be created.

    Returns
    -------
    The ``postgresql://`` URI that points at the newly created (or existing)
    tenant database.
    """
    import psycopg

    db_name = f"{settings.TENANT_PREFIX}{user_id}"
    conn = await psycopg.AsyncConnection.connect(
        _superuser_uri(), autocommit=True
    )
    async with conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        )
        if not await cur.fetchone():
            await cur.execute(f'CREATE DATABASE "{db_name}"')
            logger.info("Created database %s", db_name)
    return _db_uri(user_id)


# ── Tenant manager ─────────────────────────────────────────────────


class TenantManager:
    """Registry and LRU cache for per-user tenant agents.

    Each user gets an isolated PostgreSQL database, a :class:`AsyncPostgresSaver`
    checkpointer, an :class:`AsyncPostgresStore`, a DeepAgents backend, and
    a fully configured :class:`deepagents.DeepAgent`.

    Eviction strategy
    -----------------
    When the cache exceeds *max_cache_size*, the tenants whose ``last_used_at``
    is older than *ttl_seconds* are closed and removed.

    Parameters
    ----------
    ttl_seconds:
        Seconds of inactivity before a tenant becomes eligible for eviction.
    max_cache_size:
        Upper bound on the number of cached tenants.
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        max_cache_size: int = 1000,
    ) -> None:
        self._cache: dict[str, TenantStore] = {}
        self._lock = asyncio.Lock()
        self.ttl_seconds = ttl_seconds
        self.max_cache_size = max_cache_size

    @property
    def cache_size(self) -> int:
        """Number of currently cached tenant entries."""
        return len(self._cache)

    async def get_or_create_agent(self, user_id: str) -> Any:
        """Return the DeepAgent for *user_id*, creating it on first access.

        On the fast path (cache hit) the agent is returned without acquiring
        the lock.  On the slow path the lock prevents two concurrent requests
        from racing to create the same tenant twice.

        Parameters
        ----------
        user_id:
            The user whose agent should be returned.

        Returns
        -------
        The fully configured :class:`deepagents.DeepAgent` instance.
        """
        # Fast path — cache hit
        if user_id in self._cache:
            tenant = self._cache[user_id]
            tenant.last_used_at = datetime.now(timezone.utc)
            if tenant.agent is not None:
                return tenant.agent

        # Evict expired entries if over the max cache size
        if self.max_cache_size and len(self._cache) >= self.max_cache_size:
            await self._evict_expired()

        # Slow path — create tenant DB and agent under lock
        async with self._lock:
            # Double-check inside lock
            if user_id in self._cache:
                tenant = self._cache[user_id]
                tenant.last_used_at = datetime.now(timezone.utc)
                if tenant.agent is not None:
                    return tenant.agent

            db_uri = await _create_tenant_db(user_id)
            db_name = f"{settings.TENANT_PREFIX}{user_id}"

            from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
            from langgraph.store.postgres import AsyncPostgresStore

            checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_uri)
            checkpointer = await checkpointer_cm.__aenter__()
            await checkpointer.setup()

            store_cm = AsyncPostgresStore.from_conn_string(db_uri)
            store = await store_cm.__aenter__()
            await store.setup()

            from app.core.agent import build_model, create_deep_agent
            from deepagents_backends import PostgresBackend, PostgresConfig

            model = build_model()

            backend = PostgresBackend(config=PostgresConfig(
                host=settings.POSTGRES_HOST,
                port=settings.POSTGRES_PORT,
                database=db_name,
                user=settings.POSTGRES_USER,
                password=settings.POSTGRES_PASSWORD,
                table="agent_files",
            ))
            await backend.initialize()

            agent = create_deep_agent(
                model=model,
                checkpointer=checkpointer,
                store=store,
                backend=backend,
            )

            tenant = TenantStore(
                user_id=user_id,
                tenant_db_name=db_name,
                database_url=db_uri,
                agent=agent,
                checkpointer=checkpointer,
                store=store,
                checkpointer_cm=checkpointer_cm,
                store_cm=store_cm,
                backend=backend,
            )
            self._cache[user_id] = tenant
            logger.info(
                "Agent loaded for user_id=%s (db=%s)", user_id, db_name
            )
            return agent

    async def remove_tenant(self, user_id: str) -> None:
        """Close and remove all resources for *user_id*.

        If the user is not in the cache this is a no-op.

        Parameters
        ----------
        user_id:
            The user whose resources should be cleaned up.
        """
        if user_id not in self._cache:
            return

        tenant = self._cache.pop(user_id)
        try:
            if hasattr(tenant, "checkpointer_cm") and tenant.checkpointer_cm is not None:
                await tenant.checkpointer_cm.__aexit__(None, None, None)
            if hasattr(tenant, "store_cm") and tenant.store_cm is not None:
                await tenant.store_cm.__aexit__(None, None, None)
        except Exception as e:
            logger.warning("Error closing agent for user_id=%s: %s", user_id, e)

        logger.info("Agent closed for user_id=%s", user_id)

    async def close_all(self) -> None:
        """Close every cached tenant.  Call on application shutdown."""
        for user_id in list(self._cache.keys()):
            await self.remove_tenant(user_id)

    async def _evict_expired(self) -> None:
        """Evict tenants whose ``last_used_at`` is older than *ttl_seconds*."""
        now = datetime.now(timezone.utc)
        expired = [
            uid for uid, t in self._cache.items()
            if (now - t.last_used_at).total_seconds() > self.ttl_seconds
        ]
        for uid in expired:
            await self.remove_tenant(uid)
        if expired:
            logger.info("Evicted %d expired tenants", len(expired))


# ── Module-level singleton ─────────────────────────────────────────

_tenant_manager: TenantManager | None = None


def get_tenant_manager() -> TenantManager:
    """Return the singleton :class:`TenantManager`, creating it lazily.

    Uses ``TENANT_DEFAULT_TTL_SECONDS`` and ``TENANT_MAX_CACHE_SIZE`` from
    :data:`app.core.config.settings`.

    Returns
    -------
    The global :class:`TenantManager` instance.
    """
    global _tenant_manager
    if _tenant_manager is None:
        _tenant_manager = TenantManager(
            ttl_seconds=settings.TENANT_DEFAULT_TTL_SECONDS,
            max_cache_size=settings.TENANT_MAX_CACHE_SIZE,
        )
    return _tenant_manager


async def reset_tenant_manager() -> None:
    """Close all tenants and reset the singleton.  Mainly for tests."""
    global _tenant_manager
    if _tenant_manager is not None:
        await _tenant_manager.close_all()
    _tenant_manager = None


__all__ = [
    "TenantStore",
    "TenantManager",
    "get_tenant_manager",
    "reset_tenant_manager",
]
