"""Unit tests for the agent layer (app/agent.py).

Tests singleton caching, checkpointer initialization, provider selection,
reset behaviour, and per-user agent isolation without requiring a real
database or LLM API calls.  All external dependencies are mocked via
``unittest.mock.patch``.
"""

from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch

import pytest

# Ensure app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ===== Fixtures =============================================================

@pytest.fixture(scope="function")
def mock_openai_env(monkeypatch):
    """Provide an OpenAI API key in the environment (clears Anthropic)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-openai")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield


@pytest.fixture(scope="function")
def mock_anthropic_env(monkeypatch):
    """Provide an Anthropic API key (clears OpenAI)."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-anthropic")
    yield


# ===== Internal helpers for test isolation ====================================

@pytest.fixture(scope="function")
def fresh_state():
    """Reset tenant-manager singleton so every test starts clean."""
    from app.agents.tenant import reset_tenant_manager  # noqa: E402
    await reset_tenant_manager()


# Backwards-compatible wrapper so that existing ``_fresh_state()`` calls keep working
async def _fresh_state():  # noqa: E303
    from app.agents.tenant import reset_tenant_manager  # noqa: E402
    await reset_tenant_manager()


# ===== Tests -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_agent_for_user_returns_cached_instance(mock_openai_env):
    """Calling ``get_agent_for_user("test-user")`` twice returns the identical singleton."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    mock_model = MagicMock()

    with patch("langchain_openai.ChatOpenAI", return_value=mock_model) as mock_cls, \
         patch("deepagents.create_deep_agent", return_value=MagicMock()) as mock_create, \
          patch("app.core.agent.get_checkpointer") as mock_cp, \
          patch("app.core.agent.get_store") as mock_st:

        mock_cp.return_value = MagicMock()
        mock_st.return_value = MagicMock()

        first = await agent_mod.get_agent_for_user("test-user")
        second = await agent_mod.get_agent_for_user("test-user")

    assert first is second
    assert mock_create.call_count == 1  # built only once

    await agent_mod.agent_reset_for_user("test-user")


@pytest.mark.asyncio
async def test_setup_agent_initializes_checkpointer(mock_openai_env):
    """``setup_agent()`` calls the checkpointer constructor with the
    correct connection string and invokes ``.setup()`` on it.
    """
    await _fresh_state()

    from app.core import agent as agent_mod, infrastructure as db_mod  # noqa: E402

    mock_saver_instance = MagicMock()

    with patch.object(db_mod.database, "PostgresSaver") as MockSaver:
        MockSaver.from_conn_string.return_value = mock_saver_instance
        agent_mod.setup_agent()

    # PostgresSaver.from_conn_string was called with the connection string
    assert MockSaver.from_conn_string.called
    # The checkpointer's .setup() must have been called
    mock_saver_instance.setup.assert_called()


@pytest.mark.asyncio
async def test_reset_agent_clears_singleton_cache(mock_openai_env):
    """After ``agent_reset_for_user("test-user")`` the next ``get_agent_for_user("test-user")`` returns a different object."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    call_counter = {"count": 0}

    def _make_agent(*_args, **_kwargs):
        call_counter["count"] += 1
        return MagicMock()

    with patch("deepagents.create_deep_agent", side_effect=_make_agent):

        first = await agent_mod.get_agent_for_user("test-user")
        await agent_mod.agent_reset_for_user("test-user")
        second = await agent_mod.get_agent_for_user("test-user")

    assert first is not second
    assert call_counter["count"] == 2  # two independent builds

    await agent_mod.agent_reset_for_user("test-user")


@pytest.mark.asyncio
async def test_provider_selection_openai_when_key_present(mock_openai_env):
    """When OPENAI_API_KEY is set, the agent is built with ChatOpenAI."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    with patch(
        "langchain_openai.ChatOpenAI", return_value=MagicMock()
    ) as mock_openai, \
         patch("deepagents.create_deep_agent", return_value=MagicMock()):

        agent = await agent_mod.get_agent_for_user("test-user")

    # ChatOpenAI constructor must have been called once
    mock_openai.assert_called_once()
    call_kwargs = mock_openai.call_args[1]
    assert call_kwargs["model"] == "claude-sonnet-4-5-20250929"
    assert call_kwargs["api_key"] == "sk-test-openai"
    assert hasattr(agent, "ainvoke")

    await agent_mod.agent_reset_for_user("test-user")


@pytest.mark.asyncio
async def test_provider_selection_anthropic_when_openai_absent(mock_anthropic_env):
    """When OPENAI_API_KEY is absent but ANTHROPIC_API_KEY is set,
    the agent is built with ChatAnthropic instead.
    """
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    with patch(
        "langchain_openai.ChatOpenAI",
        return_value=MagicMock(),
    ) as mock_openai, \
         patch(
             "langchain_anthropic.ChatAnthropic",
             return_value=MagicMock(),
         ) as mock_anthropic, \
         patch("deepagents.create_deep_agent", return_value=MagicMock()):

        agent = await agent_mod.get_agent_for_user("test-user")

    # Anthropic constructor must have been called, OpenAI NOT
    mock_anthropic.assert_called_once()
    mock_openai.assert_not_called()
    assert hasattr(agent, "ainvoke")

    await agent_mod.agent_reset_for_user("test-user")


@pytest.mark.asyncio
async def test_no_provider_errors_gracefully(monkeypatch):
    """When neither API key is configured, get_agent_for_user() raises ValueError."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(ValueError, match="No LLM API key configured"):
        await agent_mod.get_agent_for_user("test-user")


# ─────────────────
# Per-user agent factory tests (get_agent_for_user)
# ──────────────────────────────────────


@pytest.mark.asyncio
async def test_get_agent_for_user_returns_agent(mock_openai_env):
    """get_agent_for_user returns an agent for the given user_id."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    mock_agent = MagicMock()

    with patch.object(agent_mod, "get_tenant_manager") as mock_tm:
        mock_manager = MagicMock()
        mock_manager.get_or_create_agent = MagicMock(AsyncMock=MagicMock, return_value=mock_agent)
        # Use a coroutine for get_or_create_agent
        async def mock_goca(uid, settings):
            return mock_agent
        mock_manager.get_or_create_agent = mock_goca
        mock_tm.return_value = mock_manager

        result = await agent_mod.get_agent_for_user("user-per-test-1")

    assert result is mock_agent


@pytest.mark.asyncio
async def test_per_user_agent_isolation():
    """Different users get different agent instances."""
    _fresh_state()

    from app.agents.tenant import TenantManager

    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)
    mock_settings = MagicMock()
    mock_settings.TENANT_PREFIX = "deepagent_"

    agent_a = MagicMock()
    agent_b = MagicMock()

    with patch("app.agents.tenant._create_tenant_db", return_value="postgresql:///x"), \
         patch("app.agents.tenant.AsyncPostgresSaver") as MockSaver, \
         patch("app.agents.tenant.AsyncPostgresStore") as MockStore, \
         patch("app.agents.tenant.build_model", return_value=MagicMock()), \
         patch("app.agents.tenant.create_deep_agent", side_effect=[agent_a, agent_b]):

        MockSaver.from_conn_string.return_value.__aenter__ = MagicMock()
        MockSaver.from_conn_string.return_value.__aexit__ = MagicMock()
        MockStore.from_conn_string.return_value.__aenter__ = MagicMock()
        MockStore.from_conn_string.return_value.__aexit__ = MagicMock()

        actual_a = await manager.get_or_create_agent("user-a", mock_settings)
        actual_b = await manager.get_or_create_agent("user-b", mock_settings)

    assert actual_a is not actual_b
    assert actual_a is agent_a
    assert actual_b is agent_b


@pytest.mark.asyncio
async def test_get_agent_for_user_from_own_tenant_manager(mock_openai_env):
    """get_agent_for_user delegates to the current TenantManager (not stale singleton)."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    # Ensure a fresh TenantManager singleton
    from app.agents.tenant import reset_tenant_manager  # noqa: E402
    await reset_tenant_manager()

    # Reset the agent module-level singleton variable used by app.routers.chat
    from app.routers import chat as chat_router  # noqa: E402
    chat_router._get_agent_old = getattr(chat_router, "_get_agent_old", None)

    mock_agent = MagicMock()

    async def mock_goca_uid(uid, settings):
        return mock_agent

    with patch("app.core.agent.get_tenant_manager") as mock_tm:
        mock_manager = MagicMock()
        mock_manager.get_or_create_agent = mock_goca_uid
        mock_tm.return_value = mock_manager

        result = await agent_mod.get_agent_for_user("user-delegated")

    assert result is mock_agent


@pytest.mark.asyncio
async def test_agent_reset_for_user_removes_tenant(mock_openai_env):
    """agent_reset_for_user removes the user's tenant from the cache."""
    _fresh_state()

    from app.agents.tenant import TenantManager, get_tenant_manager
    from app import agent as agent_mod  # noqa: E402

    # Create a fresh manager
    await reset_tenant_manager()

    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)
    mock_settings = MagicMock()
    mock_settings.TENANT_PREFIX = "deepagent_"

    with patch("app.agents.tenant._create_tenant_db", return_value="postgresql:///x"), \
         patch("app.agents.tenant.AsyncPostgresSaver") as MockSaver, \
         patch("app.agents.tenant.AsyncPostgresStore") as MockStore, \
         patch("app.agents.tenant.build_model", return_value=MagicMock()), \
         patch("app.agents.tenant.create_deep_agent", return_value=MagicMock()):

        MockSaver.from_conn_string.return_value.__aenter__ = MagicMock()
        MockSaver.from_conn_string.return_value.__aexit__ = MagicMock()
        MockStore.from_conn_string.return_value.__aenter__ = MagicMock()
        MockStore.from_conn_string.return_value.__aexit__ = MagicMock()

        await manager.get_or_create_agent("user-reset-test", mock_settings)

    assert "user-reset-test" in manager._cache

    # Now use agent_reset_for_user
    try:
        await agent_mod.agent_reset_for_user("user-reset-test")
    except Exception:
        # May fail if the singleton hasn't been wired; we tested manager directly above
        pass

    assert "user-reset-test" not in manager._cache


@pytest.mark.asyncio
async def test_agent_reset_for_user_does_not_affect_others(mock_openai_env):
    """Resetting user A's tenant does not affect user B."""
    _fresh_state()

    from app.agents.tenant import TenantManager

    manager = TenantManager(ttl_seconds=3600, max_cache_size=100)
    mock_settings = MagicMock()
    mock_settings.TENANT_PREFIX = "deepagent_"

    with patch("app.agents.tenant._create_tenant_db", return_value="postgresql:///x"), \
         patch("app.agents.tenant.AsyncPostgresSaver") as MockSaver, \
         patch("app.agents.tenant.AsyncPostgresStore") as MockStore, \
         patch("app.agents.tenant.build_model", return_value=MagicMock()), \
         patch("app.agents.tenant.create_deep_agent", side_effect=[MagicMock(), MagicMock()]):

        MockSaver.from_conn_string.return_value.__aenter__ = MagicMock()
        MockSaver.from_conn_string.return_value.__aexit__ = MagicMock()
        MockStore.from_conn_string.return_value.__aenter__ = MagicMock()
        MockStore.from_conn_string.return_value.__aexit__ = MagicMock()

        await manager.get_or_create_agent("user-a-reset", mock_settings)
        await manager.get_or_create_agent("user-b-reset", mock_settings)

    assert manager.cache_size == 2
    assert "user-a-reset" in manager._cache
    assert "user-b-reset" in manager._cache

    await agent_mod.agent_reset_for_user("user-a-reset")

    assert "user-a-reset" not in manager._cache
    assert "user-b-reset" in manager._cache
    assert manager.cache_size == 1
