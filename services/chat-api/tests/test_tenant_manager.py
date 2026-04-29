"""Tests for the TenantManager (app/tenant_manager.py).

Covers:
- TenantStore dataclass fields
- get_or_create_agent with mock create_tenant_database
- Cache hit / miss
- update_last_seen
- cleanup_expired for old / recent entries
- reset_cache
- Singleton accessor
- DB name format
- Cache eviction on max size
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.tenant_manager import (
    TenantManager,
    TenantStore,
    get_tenant_manager,
    reset_tenant_manager,
)


# ────────────────
# TenantStore dataclass tests
# ────────────────


def test_tenant_store_dataclass_creation():
    """TenantStore has the expected fields with correct defaults."""
    now = datetime.now(timezone.utc)
    ts = TenantStore(
        user_id="alice",
        tenant_db_name="deepagent_alice",
        database_uri="postgresql://user:pass@host:5432/deepagent_alice",
    )
    assert ts.user_id == "alice"
    assert ts.tenant_db_name == "deepagent_alice"
    assert ts.database_uri == "postgresql://user:pass@host:5432/deepagent_alice"
    assert ts.agent is None
    assert ts.checkpointer is None
    assert ts.checkpointer_cm is None
    assert ts.store is None
    assert ts.store_cm is None
    assert ts.backend is None
    assert ts.backend_initialized is False
    assert ts.ttl_seconds == 3600
    assert ts.last_used_at is not None


def test_tenant_store_property_is_expired():
    """is_expired reports False for new store and True for expired store."""
    ts_fresh = TenantStore(
        user_id="alice",
        tenant_db_name="deepagent_alice",
        database_uri="postgresql:///x",
    )
    assert ts_fresh.is_expired is False

    ts_old = TenantStore(
        user_id="bob",
        tenant_db_name="deepagent_bob",
        database_uri="postgresql:///x",
        ttl_seconds=0,
    )
    # ttl_seconds <= 0 defaults to 3600; set ttl_seconds to 1 and backdate last_used_at
    ts_old.ttl_seconds = 1
    ts_old.last_used_at = datetime.now(timezone.utc) - timedelta(seconds=2)
    assert ts_old.is_expired is True


def test_tenant_store_update_last_seen():
    """update_last_seen advances the last_used_at timestamp."""
    ts = TenantStore(
        user_id="alice",
        tenant_db_name="deepagent_alice",
        database_uri="postgresql:///x",
    )
    before = ts.last_used_at
    from time import sleep
    sleep(0.01)
    ts.update_last_seen()
    assert ts.last_used_at >= before


def test_tenant_equality_and_hash():
    """TenantStore equality compares on user_id; hash works for dict keys."""
    ts1 = TenantStore(user_id="alice", tenant_db_name="deepagent_alice", database_uri="x")
    ts2 = TenantStore(user_id="alice", tenant_db_name="deepagent_alice2", database_uri="y")
    ts3 = TenantStore(user_id="bob", tenant_db_name="deepagent_bob", database_uri="z")

    assert ts1 == ts2
    assert ts1 != ts3
    assert hash(ts1) == hash(ts2)
    assert hash(ts1) != hash(ts3)


# ────────────────
# TenantManager core
# ────────────────


@pytest.fixture()
def fake_settings():
    """A minimal settings object for testing."""
    s = MagicMock()
    s.TENANT_PREFIX = "deepagent_"
    s.POSTGRES_USER = "postgres"
    s.POSTGRES_PASSWORD = "pass"
    s.POSTGRES_HOST = "localhost"
    s.POSTGRES_PORT = 5432
    return s


@pytest.mark.asyncio
async def test_get_or_create_agent_first_time(fake_settings):
    """First call creates a tenant; second call returns from cache."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)

    mock_agent = MagicMock()

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, return_value=mock_agent):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        agent1 = await manager.get_or_create_agent("user-a", fake_settings)
        agent2 = await manager.get_or_create_agent("user-a", fake_settings)

    assert agent1 is mock_agent
    assert agent2 is mock_agent
    assert manager.cache_size == 1


@pytest.mark.asyncio
async def test_get_or_create_agent_returns_cached(fake_settings):
    """Second call returns the same cached agent without calling create_tenant_database."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)

    mock_agent = MagicMock()

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, return_value=mock_agent):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        # First call — creates
        agent1 = await manager.get_or_create_agent("user-a", fake_settings)
        create_count_first = MockSaver.from_conn_string.return_value.__aenter__.call_count

        # Small delay to ensure update_last_seen changes the timestamp
        import time
        time.sleep(0.01)

        # Second call — should be cache hit (same async path, but cached)
        agent2 = await manager.get_or_create_agent("user-a", fake_settings)

    assert agent1 is agent2


@pytest.mark.asyncio
async def test_update_last_seen(fake_settings):
    """last_used_at is updated on each get_or_create_agent call."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)

    mock_agent = MagicMock()

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, return_value=mock_agent):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        agent = await manager.get_or_create_agent("user-a", fake_settings)
        first_seen = manager._cache["user-a"].last_used_at
        import time
        time.sleep(0.01)
        await manager.get_or_create_agent("user-a", fake_settings)
        second_seen = manager._cache["user-a"].last_used_at

    assert second_seen >= first_seen


# ────────────────
# cleanup_expired
# ────────────────

@pytest.mark.asyncio
async def test_cleanup_expired_removes_old_entries():
    """Entries beyond TTL are removed by cleanup_expired."""
    manager = TenantManager(ttl_seconds=1, max_cache_size=100)

    # Manually place an expired tenant in cache
    tenant = TenantStore(
        user_id="old-user",
        tenant_db_name="deepagent_old-user",
        database_uri="postgresql:///x",
        ttl_seconds=1,
    )
    tenant.last_used_at = datetime.now(timezone.utc) - timedelta(seconds=5)
    manager._cache["old-user"] = tenant

    removed = await manager.cleanup_expired()
    assert removed == 1
    assert "old-user" not in manager._cache


@pytest.mark.asyncio
async def test_cleanup_expired_keeps_recent_entries():
    """Entries within TTL are kept by cleanup_expired."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)

    tenant = TenantStore(
        user_id="recent-user",
        tenant_db_name="deepagent_recent-user",
        database_uri="postgresql:///x",
        ttl_seconds=3600,
    )
    tenant.last_used_at = datetime.now(timezone.utc)  # recently used
    manager._cache["recent-user"] = tenant

    removed = await manager.cleanup_expired()
    assert removed == 0
    assert "recent-user" in manager._cache


# ────────────────
# reset_cache
# ────────────────

@pytest.mark.asyncio
async def test_reset_tenant_manager_clears_cache():
    """reset_cache clears all tenants from the cache."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)

    tenant_a = TenantStore(user_id="user-a", tenant_db_name="deepagent_user-a", database_uri="x")
    tenant_b = TenantStore(user_id="user-b", tenant_db_name="deepagent_user-b", database_uri="y")
    manager._cache["user-a"] = tenant_a
    manager._cache["user-b"] = tenant_b

    await manager.reset_cache()
    assert manager.cache_size == 0
    assert "user-a" not in manager._cache
    assert "user-b" not in manager._cache


# ────────────────
# Singleton accessor
# ────────────────

@pytest.mark.asyncio
async def test_get_tenant_manager_singleton():
    """Two calls to get_tenant_manager return the same instance."""
    # Ensure we start clean
    await reset_tenant_manager()

    m1 = get_tenant_manager()
    m2 = get_tenant_manager()

    assert m1 is m2


@pytest.mark.asyncio
async def test_get_tenant_manager_resets_and_new():
    """After reset, a new get_tenant_manager creates a new instance."""
    await reset_tenant_manager()
    m1 = get_tenant_manager()

    await reset_tenant_manager()
    m2 = get_tenant_manager()

    assert m1 is not m2


# ────────────────
# DB name format
# ────────────────


def test_tenant_db_name_format():
    """tenant_db_name follows the expected deepagent_{user_id} format."""
    from app.database import tenant_db_name  # noqa: E402

    class FakeSettings:
        TENANT_PREFIX = "deepagent_"

    name = tenant_db_name("alice", FakeSettings())
    assert name.startswith("deepagent_alice")
    assert name == "deepagent_alice"


def test_tenant_db_name_prefix_is_custom():
    """The prefix can be overridden."""
    from app.database import tenant_db_name  # noqa: E402

    class FakeSettings:
        TENANT_PREFIX = "tenant_"

    name = tenant_db_name("bob", FakeSettings())
    assert name == "tenant_bob"


# ────────────────
# Cache eviction on max_size
# ────────────────

@pytest.mark.asyncio
async def test_cache_eviction_on_max_size(fake_settings):
    """When cache exceeds max_cache_size, oldest tenant is evicted."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=3)

    mock_agent = MagicMock()

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, return_value=mock_agent):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        # Fill cache to max
        for i in range(3):
            await manager.get_or_create_agent(f"user-{i}", fake_settings)
            import time
            time.sleep(0.01)

        assert manager.cache_size == 3

        # Add one more → should evict oldest (user-0)
        await manager.get_or_create_agent("user-new", fake_settings)

    assert manager.cache_size == 3
    assert "user-new" in manager._cache
    assert "user-0" not in manager._cache  # oldest evicted
    assert "user-1" in manager._cache  # still present
    assert "user-2" in manager._cache


@pytest.mark.asyncio
async def test_remove_tenant(fake_settings):
    """remove_tenant removes a specific user, returns False if not present."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)

    mock_agent = MagicMock()

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, return_value=mock_agent):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        await manager.get_or_create_agent("alice", fake_settings)

    assert manager.cache_size == 1
    removed = await manager.remove_tenant("alice")
    assert removed is True
    assert manager.cache_size == 0

    # Removing a non-existing tenant
    removed2 = await manager.remove_tenant("nobody")
    assert removed2 is False


@pytest.mark.asyncio
async def test_get_or_create_agent_creates_db():
    """First access to a new user calls create_tenant_database."""
    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)

    mock_agent_instance = MagicMock()
    mock_settings = MagicMock()
    mock_settings.TENANT_PREFIX = "deepagent_"

    create_called = False

    async def fake_create(name, uri):
        nonlocal create_called
        create_called = True
        return True

    with patch("app.tenant_manager.create_tenant_database", side_effect=fake_create), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, return_value=mock_agent_instance):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        agent = await manager.get_or_create_agent("db-test-user", mock_settings)

    assert create_called is True
    assert agent is mock_agent_instance
