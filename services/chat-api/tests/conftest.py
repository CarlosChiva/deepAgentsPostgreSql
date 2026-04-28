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
    - ``client`` – an ``httpx.TestClient`` that overrides the app's
      dependency injection so every request uses the test checkpointer / store.
    - ``test_agent`` – a mock ``DeepAgent`` wired to the test checkpointer
      (avoids real LLM calls during tests).
"""

from __future__ import annotations

from typing import AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient, TestClient
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.async_postgres import AsyncPostgresStore


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
    """Reset module-level state in ``app.database`` so every test gets a
    fresh checkpointer / store pair rather than reusing a previous one.
    """
    from app import database  # noqa: E402

    database._checkpointer = None
    database._checkpointer_initialized = False
    database._store = None
    database._store_initialized = False


def _reset_agent_singleton():
    """Clear the cached agent singleton so the test build a new one."""
    from app import agent as agent_mod  # noqa: E402

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
    )
    return str(_test_settings.postgres_url)


# ── checkpointer ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_checkpointer(test_settings, test_db_uri) -> Generator[PostgresSaver, None, None]:
    """Provide a fresh ``PostgresSaver`` for each test function.

    The checkpointer is set up (tables created) automatically.  On teardown
    the connection pool is closed gracefully; errors from double-close are
    silently ignored.
    """
    _reset_db_module_singletons()

    saver = PostgresSaver.from_conn_string(test_db_uri)
    saver.setup()

    yield saver

    # Teardown
    try:
        pool = getattr(saver, "_pool", None)
        if pool is not None:
            pool.close()
            pool.dispose()
    except Exception:
        pass


# ── store ─────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
async def test_store(test_settings, test_db_uri) -> AsyncGenerator[AsyncPostgresStore, None]:
    """Provide a fresh ``AsyncPostgresStore`` for each test function.

    Teardown closes the connection pool asynchronously.
    """
    _reset_db_module_singletons()

    store = AsyncPostgresStore.from_conn_string(test_db_uri)
    await store.setup()

    yield store

    # Teardown
    try:
        pool = getattr(store, "_pool", None)
        if pool is not None:
            await pool.close()
            await pool.dispose()
    except Exception:
        pass


# ── helper: inject checkpointer / store into ``app.database`` module ────────────

def _inject_into_database_module(test_checkpointer, test_store_obj):
    """Mutate ``app.database`` so its singleton getters return our test objects."""
    from app import database as db_mod  # noqa: E402

    db_mod._checkpointer = test_checkpointer
    db_mod._checkpointer_initialized = True
    db_mod._store = test_store_obj
    db_mod._store_initialized = True


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

    monkeypatch.setattr("app.agent._agent", mock_agent)
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
    from app import agent as agent_mod  # noqa: E402
    agent_mod._agent = test_agent_monkeypatch

    from app.routers.chat import router as chat_router  # noqa: E402

    # Dependency overrides for the router -- ``send_message`` and ``get_history``
    # call ``get_agent`` and ``get_checkpointer`` internally; by overriding them
    # at the module level we ensure the service layer sees our test doubles.
    from app import database as db_mod  # noqa: E402

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
    from app import database as _orig_db  # noqa: E402

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

    from app import agent as agent_mod  # noqa: E402
    agent_mod._agent = test_agent_monkeypatch

    from app import database as db_mod  # noqa: E402

    mock_get_checkpointer = MagicMock(return_value=test_checkpointer)
    mock_get_store = MagicMock(return_value=test_store)

    db_mod.get_checkpointer = mock_get_checkpointer  # type: ignore[assignment]
    db_mod.get_store = mock_get_store  # type: ignore[assignment]

    from app.main import app as _app  # noqa: E402

    transport = ASGITransport(app=_app)  # type: ignore[arg-type]
    ac = AsyncClient(app=transport, base_url="http://test")  # type: ignore[arg-type]

    yield ac

    import importlib  # noqa: E402
    from app import database as _orig_db  # noqa: E402

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

    monkeypatch.setattr("app.agent._agent", mock_agent)
    _reset_agent_singleton()

    return mock_agent
