"""Connection pool management for the multi-tenant PostgreSQL layer.

Provides a centralised pool manager that caches :class:`AsyncPostgresSaver`
and :class:`AsyncPostgresStore` instances per tenant, managing their full
lifecycle (creation, access, cleanup, and recycling).

Key differences from :mod:`app.db.connection`
----------------------------------------------
:mod:`app.db.connection` manages the *shared* (main) checkpointer and store.
This module manages *per-tenant* pools and gives the caller explicit control
over pool lifecycle.

Key differences from :mod:`app.agents.tenant.TenantManager`
----------------------------------------------------------------
``TenantManager`` orchestrates agent creation and caches *agents*.
This module focuses solely on **connection pool** and **saver/store**
instance lifecycle, so it can be used independently of agents.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── data classes ────────────────────────────────────────────────


@dataclass
class _TenantPoolEntry:
    """Internal entry tracking a tenant's checkpointer and store pools.

    Attributes
    ----------
    user_id:
        Tenant / user identifier.
    tenant_db_name:
        Name of the tenant's PostgreSQL database.
    database_url:
        Full ``postgresql://`` URI for the tenant database.
    checkpointer:
        The live :class:`AsyncPostgresSaver` instance (or ``None``).
    store:
        The live :class:`AsyncPostgresStore` instance (or ``None``).
    checkpointer_cm:
        The async context manager for the checkpointer, kept alive so it
        can be closed cleanly.
    store_cm:
        The async context manager for the store, kept alive so it can be
        closed cleanly.
    created_at:
        When this entry was first created.
    last_used_at:
        When the checkpointer or store was last accessed.
    """

    user_id: str
    tenant_db_name: str
    database_url: str
    checkpointer: AsyncPostgresSaver | None = None
    store: AsyncPostgresStore | None = None
    checkpointer_cm: Any | None = None
    store_cm: Any | None = None
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_used_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


@dataclass
class PoolStats:
    """Snapshot of the current pool state for diagnostics.

    Attributes
    ----------
    total_tenants:
        Total number of cached tenant entries (including idle ones).
    active_tenants:
        Number of tenants accessed within the current TTL window.
    cached_tenants:
        Alias for ``total_tenants`` (total entries in the cache).
    """

    total_tenants: int
    active_tenants: int
    cached_tenants: int


# ── Pool manager ────────────────────────────────────────────────


class ConnectionPoolManager:
    """Manages connection pools for per-tenant checkpointer and store.

    Responsibilities
    ----------------
    - Lazily create :class:`AsyncPostgresSaver` and :class:`AsyncPostgresStore`
      instances for each tenant.
    - Cache created instances to avoid redundant pool creation.
    - Track pool lifetimes and support recycling/expiry.
    - Provide diagnostics via ``stats()``.

    Parameters
    ----------
    ttl_seconds:
        Time-to-live for an idle tenant pool (seconds).  Pools not accessed
        within this window are eligible for recycling.
    max_pool_size:
        Maximum number of cached tenant pools.  When the limit is reached
        the oldest idle pools are evicted before creating new ones.
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        max_pool_size: int = 1000,
    ) -> None:
        self._pools: dict[str, _TenantPoolEntry] = {}
        self._lock = asyncio.Lock()
        self.ttl_seconds = ttl_seconds
        self.max_pool_size = max_pool_size

    # ── properties ──────────────────────────────────────────

    @property
    def pool_size(self) -> int:
        """Number of currently cached tenant pools."""
        return len(self._pools)

    def stats(self) -> PoolStats:
        """Return a snapshot of pool statistics.

        Returns
        -------
        :class:`PoolStats` with current totals and active count.
        """
        now = datetime.now(timezone.utc)
        active = 0
        for entry in self._pools.values():
            if (now - entry.last_used_at).total_seconds() <= self.ttl_seconds:
                active += 1
        return PoolStats(
            total_tenants=len(self._pools),
            active_tenants=active,
            cached_tenants=len(self._pools),
        )

    # ── core accessors ──────────────────────────────────────

    async def get_or_create_checkpointer(
        self, user_id: str
    ) -> AsyncPostgresSaver:
        """Return the checkpointer for *user_id*, creating it on first access.

        The entire check-then-create path is protected by
        ``self._lock`` to prevent race conditions when two
        concurrent requests trigger creation for the same tenant.

        Parameters
        ----------
        user_id:
            Tenant / user identifier.

        Returns
        -------
        An initialised :class:`AsyncPostgresSaver` bound to the tenant DB.
        """
        entry = await self._ensure_entry(user_id)
        async with self._lock:
            if entry.checkpointer is not None:
                entry.last_used_at = datetime.now(timezone.utc)
                return entry.checkpointer

            checkpointer = await self._create_checkpointer(
                entry.database_url, entry
            )
            entry.checkpointer = checkpointer
            entry.last_used_at = datetime.now(timezone.utc)
            logger.info("Checkpointer created for user_id=%s", user_id)
            return checkpointer

    async def get_or_create_store(self, user_id: str) -> AsyncPostgresStore:
        """Return the store for *user_id*, creating it on first access.

        The entire check-then-create path is protected by
        ``self._lock`` to prevent race conditions when two
        concurrent requests trigger creation for the same tenant.

        Parameters
        ----------
        user_id:
            Tenant / user identifier.

        Returns
        -------
        An initialised :class:`AsyncPostgresStore` bound to the tenant DB.
        """
        entry = await self._ensure_entry(user_id)
        async with self._lock:
            if entry.store is not None:
                entry.last_used_at = datetime.now(timezone.utc)
                return entry.store

            store = await self._create_store(entry.database_url, entry)
            entry.store = store
            entry.last_used_at = datetime.now(timezone.utc)
            logger.info("Store created for user_id=%s", user_id)
            return store

    async def get_or_create_both(
        self, user_id: str
    ) -> tuple[AsyncPostgresSaver, AsyncPostgresStore]:
        """Return both checkpointer and store for *user_id*.

        A convenience method that avoids double-locking when both instances
        are needed simultaneously.  All mutation is protected by
        ``self._lock``.

        Parameters
        ----------
        user_id:
            Tenant / user identifier.

        Returns
        -------
        Tuple of ``(checkpointer, store)``.
        """
        entry = await self._ensure_entry(user_id)
        async with self._lock:
            entry.last_used_at = datetime.now(timezone.utc)

            if entry.checkpointer is None:
                entry.checkpointer = await self._create_checkpointer(
                    entry.database_url, entry
                )

            if entry.store is None:
                entry.store = await self._create_store(
                    entry.database_url, entry
                )

            result: tuple[AsyncPostgresSaver, AsyncPostgresStore] = (
                entry.checkpointer,
                entry.store,
            )

        logger.info(
            "Checkpointer + store ready for user_id=%s", user_id
        )
        return result

    # ── lifecycle management ────────────────────────────────

    async def remove(self, user_id: str) -> None:
        """Close and remove all pools for *user_id*.

        Safely closes the underlying context managers for checkpointer
        and store, then removes the entry from the cache.

        Parameters
        ----------
        user_id:
            Tenant / user identifier.
        """
        async with self._lock:
            entry = self._pools.pop(user_id, None)

        if entry is None:
            return

        await self._close_entry(entry, user_id)
        logger.info("Pools removed for user_id=%s", user_id)

    async def close_all(self) -> None:
        """Close every cached pool.  Call on application shutdown.

        The clear operation is protected by ``self._lock``, then
        context-managers are closed *outside* the lock to avoid
        blocking other coroutines during I/O.
        """
        async with self._lock:
            to_close: list[_TenantPoolEntry] = list(self._pools.values())
            self._pools.clear()

        for entry in to_close:
            await self._close_entry(entry, entry.user_id)

        logger.info("All pools closed (%d entries)", len(to_close))

    async def recycle_expired(self) -> int:
        """Evict pools that have been idle longer than ``ttl_seconds``.

        Returns
        -------
        Number of pools that were recycled.
        """
        now = datetime.now(timezone.utc)

        async with self._lock:
            expired_uids = [
                uid
                for uid, entry in self._pools.items()
                if (now - entry.last_used_at).total_seconds() > self.ttl_seconds
            ]

        count = 0
        for uid in expired_uids:
            await self.remove(uid)
            count += 1

        if count:
            logger.info("Recycled %d expired pools", count)
        return count

    async def force_recycle(self, user_id: str) -> None:
        """Force-recycle a single tenant's pools (close + remove).

        Useful when a database connection is known to be stale and
        needs to be re-established on the next access.

        Parameters
        ----------
        user_id:
            Tenant / user identifier.
        """
        logger.info("Force-recycling pools for user_id=%s", user_id)
        await self.remove(user_id)

    # ── internal helpers ────────────────────────────────────

    async def _ensure_entry(self, user_id: str) -> _TenantPoolEntry:
        """Return (or create) the ``_TenantPoolEntry`` for *user_id*.

        Uses a lock-free fast path for hits, and double-checked locking
        on the slow path.

        Parameters
        ----------
        user_id:
            Tenant / user identifier.

        Returns
        -------
        The existing or newly created :class:`_TenantPoolEntry`.
        """
        # Fast path — no lock needed
        if user_id in self._pools:
            return self._pools[user_id]

        async with self._lock:
            # Double-check inside the lock
            if user_id in self._pools:
                return self._pools[user_id]

            # Evict if over max size
            if self.max_pool_size and len(self._pools) >= self.max_pool_size:
                await self._evict_expired_unlocked()

            db_uri = _build_db_uri(user_id)
            db_name = f"{settings.TENANT_PREFIX}{user_id}"

            entry = _TenantPoolEntry(
                user_id=user_id,
                tenant_db_name=db_name,
                database_url=db_uri,
            )
            self._pools[user_id] = entry
            return entry

    async def _create_checkpointer(
        self, db_uri: str, entry: _TenantPoolEntry
    ) -> AsyncPostgresSaver:
        """Create an ``AsyncPostgresSaver`` and run ``.setup()``.

        The returned saver stays open until the entry is explicitly
        removed; the context manager is stored on *entry* so it can
        be closed via ``_close_entry``.

        Parameters
        ----------
        db_uri:
            PostgreSQL connection URI for the tenant database.
        entry:
            The :class:`_TenantPoolEntry` that will own the checkpointer.

        Returns
        -------
        An initialised :class:`AsyncPostgresSaver`.
        """
        saver_cm = AsyncPostgresSaver.from_conn_string(db_uri)
        saver = await saver_cm.__aenter__()
        await saver.setup()
        entry.checkpointer_cm = saver_cm
        logger.info("Checkpointer pool created for %s", db_uri)
        return saver

    async def _create_store(
        self, db_uri: str, entry: _TenantPoolEntry
    ) -> AsyncPostgresStore:
        """Create an ``AsyncPostgresStore`` and run ``.setup()``.

        The returned store stays open until the entry is explicitly
        removed; the context manager is stored on *entry* so it can
        be closed via ``_close_entry``.

        Parameters
        ----------
        db_uri:
            PostgreSQL connection URI for the tenant database.
        entry:
            The :class:`_TenantPoolEntry` that will own the store.

        Returns
        -------
        An initialised :class:`AsyncPostgresStore`.
        """
        store_cm = AsyncPostgresStore.from_conn_string(db_uri)
        store = await store_cm.__aenter__()
        await store.setup()
        entry.store_cm = store_cm
        logger.info("Store pool created for %s", db_uri)
        return store

    async def _close_entry(self, entry: _TenantPoolEntry, user_id: str) -> None:
        """Safely close the checkpointer and store context managers.

        Each ``__aexit__`` call is wrapped individually so that failure
        to close one does not prevent the other from being released.

        Parameters
        ----------
        entry:
            The :class:`_TenantPoolEntry` whose pools should be closed.
        user_id:
            Human-readable identifier for log messages.
        """
        try:
            if entry.checkpointer_cm is not None:
                try:
                    await entry.checkpointer_cm.__aexit__(None, None, None)
                except Exception as exc:
                    logger.warning(
                        "Error closing checkpointer for user_id=%s: %s",
                        user_id,
                        exc,
                    )
            if entry.store_cm is not None:
                try:
                    await entry.store_cm.__aexit__(None, None, None)
                except Exception as exc:
                    logger.warning(
                        "Error closing store for user_id=%s: %s",
                        user_id,
                        exc,
                    )
        except Exception as exc:
            logger.error(
                "Unexpected error closing pools for user_id=%s: %s",
                user_id,
                exc,
            )

    async def _evict_expired_unlocked(self) -> None:
        """Evict expired pools (caller must hold ``self._lock`` before calling).

        NOTE: This releases the lock per-tenant because ``remove()`` does its
        own locking and we avoid deadlock.
        """
        now = datetime.now(timezone.utc)
        expired = [
            uid
            for uid, entry in self._pools.items()
            if (now - entry.last_used_at).total_seconds() > self.ttl_seconds
        ]
        if not expired:
            # Evict very last entry as a safety net
            oldest_uid = min(
                self._pools,
                key=lambda uid: self._pools[uid].last_used_at,
            )
            expired = [oldest_uid]

        for uid in expired:
            # Release lock for the per-tenant close
            self._lock.release()
            try:
                await self.remove(uid)
            finally:
                await self._lock.acquire()

        if expired:
            logger.info("Evicted %d idle pools", len(expired))


# ── URI helpers ─────────────────────────────────────────────────


def _build_db_uri(user_id: str) -> str:
    """Build the database URI for a given *user_id*.

    Parameters
    ----------
    user_id:
        Tenant / user identifier.

    Returns
    -------
    A ``postgresql://…`` URI pointing at the tenant database,
    e.g. ``postgresql://user:pass@host:port/deepagent_user-abc``.
    """
    db_name = f"{settings.TENANT_PREFIX}{user_id}"
    return f"{settings.postgres_uri}/{db_name}"


# ── Module-level singleton ────────────────────────────────────────

_pool_manager: ConnectionPoolManager | None = None


def get_pool_manager() -> ConnectionPoolManager:
    """Return (or lazily create) the singleton :class:`ConnectionPoolManager`.

    Uses the configured ``TENANT_DEFAULT_TTL_SECONDS`` and
    ``TENANT_MAX_CACHE_SIZE`` from :data:`app.core.config.settings`.

    Returns
    -------
    The global :class:`ConnectionPoolManager` instance.
    """
    global _pool_manager
    if _pool_manager is None:
        _pool_manager = ConnectionPoolManager(
            ttl_seconds=settings.TENANT_DEFAULT_TTL_SECONDS,
            max_pool_size=settings.TENANT_MAX_CACHE_SIZE,
        )
    return _pool_manager


async def reset_pool_manager() -> None:
    """Close all pools and reset the singleton.  Mainly for tests."""
    global _pool_manager
    if _pool_manager is not None:
        await _pool_manager.close_all()
    _pool_manager = None


__all__ = [
    "ConnectionPoolManager",
    "PoolStats",
    "get_pool_manager",
    "reset_pool_manager",
    "_build_db_uri",
]
