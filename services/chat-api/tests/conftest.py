"""Pytest fixtures for the DeepAgents Chat API.

Provides:
    - ``test_settings`` – pydantic Settings with test DB credentials via
      ``monkeypatch.setenv`` so downstream imports of ``app.config.settings``
      read the test values.
    - ``test_db_uri`` – the ``postgresql+psycopg://`` URI derived from
      ``test_settings``.
    - ``test_checkpointer`` – a fresh ``PostgresSaver`` backed by the test
      database (function-scoped, auto-teardown).
    - ``test_store`` – a fresh ``AsyncPostgresStore`` backed by the test
      database (function-scoped, auto-teardown).
    - ``tenant_manager`` – a fresh ``TenantManager`` with ``reset_tenant_manager()``.
    - ``tenant_settings`` – tenant-aware test settings overrides.
    - ``client`` – an ``httpx.TestClient`` that overrides the app's
      dependency injection so every request uses the test checkpointer / store.
    - ``test_agent`` – a mock ``DeepAgent`` wired to the test checkpointer
      (avoids real LLM calls during tests).
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient, TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import AsyncPostgresStore


# ── helpers ────────────────────────────────────────────────────────────────────

def _override_settings(monkeypatch):  # noqa: ANN201
    """Apply test-db environment variables so the real ``settings`` object
    re-reads them on next access.

    We also force the module to re-import by clearing the cached instance so
    a fresh ``Settings()`` picks up the monkeypatched vars.
    """
    test_env: dict[str, str | int] = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5433",
        "POSTGRES_DB": "test_chatdb",
        "POSTGRES_USER": "testuser",
        "POSTGRES_PASSWORD": "testpass",
        "OPENAI_API_KEY": "test-key",
        "ANTHROPIC_API_KEY": "test-key",
    }
    for key, value in test_env.items():
        monkeypatch.setenv(key, str(value))


def _reset_db_module_singletons():
    """Reset module-level state in ``app.infrastructure.database`` so every test gets a
    fresh checkpointer / store pair rather than reusing a previous one.
    """
    from app.infrastructure import database  # noqa: E402

    database._checkpointer = None
    database._checkpointer_initialized = False
    database._store = None
    database._store_initialized = False


def _reset_agent_singleton():
    """Clear the cached agent singleton so the test build a new one."""
    from app.core import agent as agent_mod  # noqa: E402

    agent_mod._agent = None


# ── settings ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_settings(monkeypatch) -> None:
    """Override DB credentials via environment variables.

    This fixture does not return a value -- it mutates the environment so that
    any subsequent imports of ``app.config.settings`` see the test DB values.
    """
    _override_settings(monkeypatch)


# ── DB URI helper ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db_uri(test_settings) -> str:
    """Build the PostgreSQL URL pointed at the test database."""
    from app.config import Settings  # noqa: E402

    _test_settings = Settings(
        POSTGRES_HOST="localhost",
        POSTGRES_PORT=5433,
        POSTGRES_DB="test_chatdb",
        POSTGRES_USER="testuser",
        POSTGRES_PASSWORD="testpass",
        postgres_uri=f"postgresql://testuser:testpass@localhost:5433/test_chatdb",
    )
    return str(_test_settings.postgres_uri)


# ── checkpointer ─────────────────────────────────────────────────────────────────

def _async_test_checkpointer(db_uri: str):  # noqa: ANN201
    """Helper to create a synchronous ``PostgresSaver`` from a connection URI.

    ``PostgresSaver.from_conn_string()`` returns a **synchronous** context manager,
    so we call ``__enter__`` / ``__exit__`` directly - never with ``await``.

    The caller is responsible for tearing down the returned context manager
    after the test completes.
    """
    cm = PostgresSaver.from_conn_string(db_uri)
    saver = cm.__enter__()
    saver.setup()  # also synchronous
    return saver, cm


@pytest.fixture(scope="function")
def test_checkpointer(test_settings, test_db_uri) -> Generator[PostgresSaver, None, None]:
    """Provide a fresh ``PostgresSaver`` for each test function.

    The checkpointer is set up (tables created) automatically.  On teardown
    the connection pool is closed gracefully; errors from double-close are
    silently ignored.
    """
    _reset_db_module_singletons()

    saver, _ = _async_test_checkpointer(test_db_uri)
    yield saver


# ── store ─────────────────────────────────────────────────────────────────


def _async_test_store(db_uri: str):  # noqa: ANN201
    """Helper to create an ``AsyncPostgresStore`` from a connection URI.

    ``AsyncPostgresStore.from_conn_string()`` returns an **async** context
    manager, so we call ``__aenter__`` / ``__aexit__`` with the event loop.
    """
    cm = AsyncPostgresStore.from_conn_string(db_uri)
    store = asyncio.get_event_loop().run_until_complete(cm.__aenter__())
    asyncio.get_event_loop().run_until_complete(store.setup())
    return store, cm


@pytest.fixture(scope="function")
def test_store(test_settings, test_db_uri) -> Generator[AsyncPostgresStore, None, None]:
    """Provide a fresh ``AsyncPostgresStore`` for each test function.

    The store is set up (tables created) automatically.  On teardown
    the connection pool is closed gracefully; errors from double-close are
    silently ignored.
    """
    _reset_db_module_singletons()

    store, cm = _async_test_store(test_db_uri)
    yield store

    # Teardown
    try:
        asyncio.get_event_loop().run_until_complete(cm.__aexit__(None, None, None))
    except Exception:
        pass


# ── helper: inject checkpointer / store into ``app.database`` module ────────────

def _inject_into_database_module(test_checkpointer, test_store):
    """Mutate ``app.database`` so its singleton getters return our test objects."""
    from app.infrastructure import database as db_mod  # noqa: E402

    db_mod.checkpointer = test_checkpointer
    db_mod.checkpointer_initialized = True
    db_mod.store = test_store
    db_mod.store_initialized = True


# ── client ────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_agent_monkeypatch(monkeypatch) -> MagicMock:
    """Return a mock ``DeepAgent`` that we will wire into the app.

    The agent is fully mockable -- ``ainvoke`` and ``astream`` return
    deterministic values so no real LLM calls are ever made.
    """
    mock_agent = MagicMock()

    # Default non-stream response
    mock_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [
                {"role": "assistant", "content": "Test reply"},
            ]
        },
    )

    # Default stream response
    async def _fake_stream(*_args, **_kwargs):
        yield {"messages": [{"role": "assistant", "content": "chunk1"}]}
        yield {"messages": [{"role": "assistant", "content": "chunk2"}]}

    mock_agent.astream = _fake_stream

    mock_agent.invoke = MagicMock(
        return_value={
            "messages": [
                {"role": "assistant", "content": "Test reply sync"},
            ]
        },
    )

    monkeypatch.setattr("app.core.agent._agent", mock_agent)
    return mock_agent


@pytest.fixture(scope="function")
def client(
    test_settings,
    test_checkpointer,
    test_store,
    test_agent_monkeypatch,
) -> Generator[TestClient, None, None]:
    """An ``httpx.TestClient`` configured to use the test checkpointer, store,
    and a mock agent.

    Dependency overrides replace the real ``get_agent`` and ``get_checkpointer``
    functions used by ``app.routers.chat`` and ``app.services.chat_service``.
    """
    # Ensure database singletons point to our test objects
    _inject_into_database_module(test_checkpointer, test_store)

    # Reset agent singleton -- test_agent_monkeypatch will replace it next
    _reset_agent_singleton()

    # Also monkeypatch the module-level agent variable for import-level overrides
    from app.core import agent as agent_mod  # noqa: E402
    agent_mod._agent = test_agent_monkeypatch

    from app.routers.chat import router as chat_router  # noqa: E402

    # Dependency overrides for the router -- ``send_message`` and ``get_history``
    # call ``get_agent`` and ``get_checkpointer`` internally; by overriding them
    # at the module level we ensure the service layer sees our test doubles.
    from app.infrastructure import database as db_mod  # noqa: E402

    mock_get_checkpointer = MagicMock(return_value=test_checkpointer)
    mock_get_store = MagicMock(return_value=test_store)

    db_mod.get_checkpointer = mock_get_checkpointer  # type: ignore[assignment]
    db_mod.get_store = mock_get_store  # type: ignore[assignment]

    # Build an httpx transport backed by the FastAPI ASGI app
    from app.main import app  # noqa: E402

    transport = ASGITransport(app=app)  # type: ignore[arg-type]
    sync_client = TestClient(app, transport=transport)  # type: ignore[arg-type]

    yield sync_client

    # ── teardown ────────────────────────────────────────────────────────────

    # Restore original functions on the database module
    import importlib  # noqa: E402
    from app.infrastructure import database as _orig_db  # noqa: E402

    importlib.reload(_orig_db)
    _reset_agent_singleton()


@pytest.fixture(scope="function")
async def async_client(
    test_settings,
    test_checkpointer,
    test_store,
    test_agent_monkeypatch,
) -> AsyncGenerator[AsyncClient, None]:
    """Asynchronous ``httpx.AsyncClient`` variant of the ``client`` fixture."""
    _inject_into_database_module(test_checkpointer, test_store)
    _reset_agent_singleton()

    from app.core import agent as agent_mod  # noqa: E402
    agent_mod._agent = test_agent_monkeypatch

    from app.infrastructure import database as db_mod  # noqa: E402

    mock_get_checkpointer = MagicMock(return_value=test_checkpointer)
    mock_get_store = MagicMock(return_value=test_store)

    db_mod.get_checkpointer = mock_get_checkpointer  # type: ignore[assignment]
    db_mod.get_store = mock_get_store  # type: ignore[assignment]

    from app.main import app as _app  # noqa: E402

    transport = ASGITransport(app=_app)  # type: ignore[arg-type]
    ac = AsyncClient(app=transport, base_url="http://test")  # type: ignore[arg-type]

    yield ac

    import importlib  # noqa: E402
    from app.infrastructure import database as _orig_db  # noqa: E402

    importlib.reload(_orig_db)
    _reset_agent_singleton()


# ── convenience: a pre-wired mock agent (for tests that don't need the full client)

@pytest.fixture(scope="function")
def test_agent(monkeypatch) -> MagicMock:
    """A standalone mock ``DeepAgent`` fixture.

    Use this when you need a mock agent but not an ``httpx.TestClient``.
    """
    mock_agent = MagicMock()

    mock_agent.ainvoke = AsyncMock(
        return_value={
            "messages": [
                {"role": "assistant", "content": "Test reply"},
            ]
        },
    )

    async def _fake_stream(*_args, **_kwargs):
        yield {"messages": [{"role": "assistant", "content": "chunk"}]}

    mock_agent.astream = _fake_stream
    mock_agent.invoke = MagicMock(
        return_value={
            "messages": [
                {"role": "assistant", "content": "sync reply"},
            ]
        },
    )

    monkeypatch.setattr("app.core.agent._agent", mock_agent)
    _reset_agent_singleton()

    return mock_agent


# ─────────────────────────────────────────────────────────────────────────────
# Tenant-aware fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _override_tenant_settings(monkeypatch):  # noqa: ANN201
    """Override tenant-related settings for tests."""
    tenant_env: dict[str, str | int | bool] = {
        "TENANT_PREFIX": "deepagent_",
        "TENANT_SUPERUSER_DB": "postgres",
        "TENANT_DEFAULT_TTL_SECONDS": "3600",
        "TENANT_ENFORCE_USER_ID": "true",
        "TENANT_MAX_CACHE_SIZE": "1000",
    }
    for key, value in tenant_env.items():
        monkeypatch.setenv(key, str(value))


@pytest.fixture(scope="function")
def test_tenant_settings(monkeypatch) -> None:
    """Override tenant settings via environment variables.

    This fixture does not return a value -- it mutates the environment so that
    any subsequent imports of ``app.config.settings`` see the test values.
    """
    _override_settings(monkeypatch)
    _override_tenant_settings(monkeypatch)


@pytest.fixture(scope="function")
async def tenant_manager(test_settings) -> "TenantManager":
    """Provide a fresh TenantManager for each test function.

    Yields the manager instance; on teardown calls ``reset_tenant_manager()``
    and clears the singleton.
    """
    from app.agents.tenant import TenantManager, reset_tenant_manager  # noqa: E402

    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)
    yield manager

    # Teardown
    await reset_tenant_manager()


@pytest.fixture(scope="function")
async def reset_tenant_manager_fixture(tenant_manager) -> None:
    """Fixture that provides a callable to reset the tenant manager singleton.

    Useful for tests that need to manually reset between sub-scenarios.
    """
    from app.agents.tenant import reset_tenant_manager  # noqa: E402
    yield reset_tenant_manager
