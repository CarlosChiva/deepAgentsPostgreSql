"""Unit tests for the agent layer (app/agent.py).

Tests singleton caching, checkpointer initialization, provider selection,
and reset behaviour without requiring a real database or LLM API calls.
All external dependencies are mocked via ``unittest.mock.patch``.
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

def _fresh_state():
    """Reset module-level singletons so every test starts clean."""
    from app import agent, database  # noqa: E402
    agent.reset_agent()
    database._checkpointer = None
    database._checkpointer_initialized = False
    database._store = None
    database._store_initialized = False


# ===== Tests -------------------------------------------------------------------

def test_get_agent_returns_cached_instance(mock_openai_env):
    """Calling ``get_agent()`` twice returns the identical singleton."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    mock_model = MagicMock()

    with patch("langchain_openai.ChatOpenAI", return_value=mock_model) as mock_cls, \
         patch("deepagents.create_deep_agent", return_value=MagicMock()) as mock_create, \
         patch("app.agent.get_checkpointer") as mock_cp, \
         patch("app.agent.get_store") as mock_st:

        mock_cp.return_value = MagicMock()
        mock_st.return_value = MagicMock()

        first = agent_mod.get_agent()
        second = agent_mod.get_agent()

    assert first is second
    assert mock_create.call_count == 1  # built only once

    agent_mod.reset_agent()


def test_setup_agent_initializes_checkpointer(mock_openai_env):
    """``setup_agent()`` calls the checkpointer constructor with the
    correct connection string and invokes ``.setup()`` on it.
    """
    _fresh_state()

    from app import agent as agent_mod, database as db_mod  # noqa: E402

    mock_saver_instance = MagicMock()

    with patch.object(db_mod, "PostgresSaver") as MockSaver:
        MockSaver.from_conn_string.return_value = mock_saver_instance
        agent_mod.setup_agent()

    # PostgresSaver.from_conn_string was called with the connection string
    assert MockSaver.from_conn_string.called
    # The checkpointer's .setup() must have been called
    mock_saver_instance.setup.assert_called()


def test_reset_agent_clears_singleton_cache(mock_openai_env):
    """After ``reset_agent()`` the next ``get_agent()`` returns a different object."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    call_counter = {"count": 0}

    def _make_agent(*_args, **_kwargs):
        call_counter["count"] += 1
        return MagicMock()

    with patch("deepagents.create_deep_agent", side_effect=_make_agent):

        first = agent_mod.get_agent()
        agent_mod.reset_agent()
        second = agent_mod.get_agent()

    assert first is not second
    assert call_counter["count"] == 2  # two independent builds

    agent_mod.reset_agent()


def test_provider_selection_openai_when_key_present(mock_openai_env):
    """When OPENAI_API_KEY is set, the agent is built with ChatOpenAI."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    with patch(
        "langchain_openai.ChatOpenAI", return_value=MagicMock()
    ) as mock_openai, \
         patch("deepagents.create_deep_agent", return_value=MagicMock()):

        agent = agent_mod.get_agent()

    # ChatOpenAI constructor must have been called once
    mock_openai.assert_called_once()
    call_kwargs = mock_openai.call_args[1]
    assert call_kwargs["model"] == "claude-sonnet-4-5-20250929"
    assert call_kwargs["api_key"] == "sk-test-openai"
    assert hasattr(agent, "ainvoke")

    agent_mod.reset_agent()


def test_provider_selection_anthropic_when_openai_absent(mock_anthropic_env):
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

        agent = agent_mod.get_agent()

    # Anthropic constructor must have been called, OpenAI NOT
    mock_anthropic.assert_called_once()
    mock_openai.assert_not_called()
    assert hasattr(agent, "ainvoke")

    agent_mod.reset_agent()


def test_no_provider_errors_gracefully(monkeypatch):
    """When neither API key is configured, get_agent() raises ValueError."""
    _fresh_state()

    from app import agent as agent_mod  # noqa: E402

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")

    with pytest.raises(ValueError, match="No LLM API key configured"):
        agent_mod.get_agent()

