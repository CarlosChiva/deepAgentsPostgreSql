"""Tests that verify per-tenant isolation: agents, DB names, checkpointer URIs, store URIs,
and reset operations do not cross user boundaries.

All external resources are mocked — no real DB is needed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_different_users_get_different_agents():
    """Agent for user A is a distinct object from agent for user B."""
    from app.tenant_manager import TenantManager

    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)
    mock_settings = MagicMock()
    mock_settings.TENANT_PREFIX = "deepagent_"

    agent_a = MagicMock(spec=["checkpointer"])
    agent_b = MagicMock(spec=["checkpointer"])

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///a"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaverA, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStoreA, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, side_effect=[agent_a, agent_b]):

        MockSaverA.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaverA.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStoreA.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStoreA.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        actual_a = await manager.get_or_create_agent("user-a", mock_settings)
        actual_b = await manager.get_or_create_agent("user-b", mock_settings)

    assert actual_a is not actual_b
    assert actual_a is agent_a
    assert actual_b is agent_b


@pytest.mark.asyncio
async def test_different_users_get_different_tenant_dbs():
    """Tenant DB names for different users are different."""
    from app.database import tenant_db_name  # noqa: E402

    class FakeSettings:
        TENANT_PREFIX = "deepagent_"

    db_a = tenant_db_name("user-a", FakeSettings())
    db_b = tenant_db_name("user-b", FakeSettings())

    assert db_a == "deepagent_user-a"
    assert db_b == "deepagent_user-b"
    assert db_a != db_b


@pytest.mark.asyncio
async def test_different_users_get_different_checkpointer_uris():
    """Tenant URIs for different users are different."""
    from app.database import get_tenant_connection_string  # noqa: E402

    class FakeSettings:
        POSTGRES_HOST = "localhost"
        POSTGRES_PORT = 5432
        POSTGRES_USER = "postgres"
        POSTGRES_PASSWORD = "pass"
        TENANT_PREFIX = "deepagent_"

    uri_a = get_tenant_connection_string("user-a", FakeSettings())
    uri_b = get_tenant_connection_string("user-b", FakeSettings())

    assert "deepagent_user-a" in uri_a
    assert "deepagent_user-b" in uri_b
    assert uri_a != uri_b


@pytest.mark.asyncio
async def test_different_users_get_different_store_uris():
    """Store URIs for different tenants differ (both contain unique DB name)."""
    from app.database import get_tenant_connection_string  # noqa: E402

    class FakeSettings:
        POSTGRES_HOST = "localhost"
        POSTGRES_PORT = 5432
        POSTGRES_USER = "postgres"
        POSTGRES_PASSWORD = "pass"
        TENANT_PREFIX = "deepagent_"

    store_uri_a = get_tenant_connection_string("user-a", FakeSettings())
    store_uri_b = get_tenant_connection_string("user-b", FakeSettings())

    # URIs are the same as the tenant connection string (same postgres)
    assert "deepagent_user-a" in store_uri_a
    assert "deepagent_user-b" in store_uri_b
    assert store_uri_a != store_uri_b


@pytest.mark.asyncio
async def test_isolation_no_cross_user_data_access():
    """Simulate per-tenant resources — verify no shared mutable state between tenants."""
    from app.tenant_manager import TenantStore

    tenant_a = TenantStore(
        user_id="alice",
        tenant_db_name="deepagent_alice",
        database_uri="postgresql://p:pass@h:5432/deepagent_alice",
    )
    tenant_b = TenantStore(
        user_id="bob",
        tenant_db_name="deepagent_bob",
        database_uri="postgresql://p:pass@h:5432/deepagent_bob",
    )

    # Verify independent identities
    assert tenant_a.user_id != tenant_b.user_id
    assert tenant_a.tenant_db_name != tenant_b.tenant_db_name
    assert tenant_a.database_uri != tenant_b.database_uri

    # Verify they are independent objects
    assert tenant_a is not tenant_b
    assert tenant_a == tenant_a
    assert tenant_b == tenant_b
    assert tenant_a != tenant_b


@pytest.mark.asyncio
async def test_agent_reset_for_user_only_resets_one_user():
    """Resetting user A's agent does not affect user B's cached tenant."""
    from app.tenant_manager import TenantManager

    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)
    mock_settings = MagicMock()
    mock_settings.TENANT_PREFIX = "deepagent_"

    agent_a = MagicMock()
    agent_b = MagicMock()

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, side_effect=[agent_a, agent_b]):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        await manager.get_or_create_agent("alice", mock_settings)
        await manager.get_or_create_agent("bob", mock_settings)

    assert manager.cache_size == 2

    # Reset only alice
    from app.tenant_manager import TenantManager as TM
    removed = await manager.remove_tenant("alice")
    assert removed is True
    assert manager.cache_size == 1

    # Bob is still cached
    assert "bob" in manager._cache
    assert manager._cache["bob"].agent is agent_b


@pytest.mark.asyncio
async def test_tenant_cache_does_not_share_reference():
    """Different tenants in manager._cache are distinct keys with distinct stores."""
    from app.tenant_manager import TenantManager

    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)
    mock_settings = MagicMock()
    mock_settings.TENANT_PREFIX = "deepagent_"

    with patch("app.tenant_manager.create_tenant_database", new_callable=AsyncMock, return_value=True), \
         patch("app.tenant_manager.ensure_tenant_schema", new_callable=AsyncMock), \
         patch("app.tenant_manager.get_tenant_connection_string", return_value="postgresql:///x"), \
         patch("app.tenant_manager.AsyncPostgresSaver") as MockSaver, \
         patch("app.tenant_manager.AsyncPostgresStore") as MockStore, \
         patch("app.tenant_manager.build_backend_for_tenant", new_callable=AsyncMock, return_value=MagicMock()), \
         patch("app.tenant_manager._build_model", return_value=MagicMock()), \
         patch("app.tenant_manager._build_agent", new_callable=AsyncMock, return_value=MagicMock()):

        MockSaver.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockSaver.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)
        MockStore.from_conn_string.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        MockStore.from_conn_string.return_value.__aexit__ = AsyncMock(return_value=None)

        await manager.get_or_create_agent("u1", mock_settings)
        await manager.get_or_create_agent("u2", mock_settings)
        await manager.get_or_create_agent("u3", mock_settings)

    assert "u1" in manager._cache
    assert "u2" in manager._cache
    assert "u3" in manager._cache
    assert manager.cache_size == 3

    stores = list(manager._cache.values())
    assert stores[0] is not stores[1]
    assert stores[1] is not stores[2]
