"""DeepAgent agent factory with model provider selection and singleton caching.

Provides ``get_agent()`` — a cached singleton that builds a DeepAgent backed by
a ``PostgresSaver`` checkpointer and ``AsyncPostgresStore``, selecting between
OpenAI, Anthropic, and ChatOllama providers based on environment configuration.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.async_postgres import AsyncPostgresStore

from .config import settings
from .database import get_checkpointer, get_store, setup_checkpointer

logger = logging.getLogger(__name__)

# Singleton cache — agent is lazily constructed on first call to
# ``get_agent()``, then reused for the process lifetime.
_agent: Any | None = None


def get_agent() -> Any:
    """Build and return a singleton DeepAgent instance.

    Model provider selection (priority order):

    1. **OpenAI** — if ``settings.OPENAI_API_KEY`` is non-empty.
    2. **Anthropic** — if ``settings.ANTHROPIC_API_KEY`` is non-empty.
    3. **ChatOllama** — if ``settings.CHATOLLAMA_BASE_URL`` is non-empty.

    Raises ``ValueError`` when no provider is configured.

    The checkpointer is set up automatically on first call, which is a no-op
    once the target tables already exist.
    """

    global _agent

    # Return cached instance when already built
    if _agent is not None:
        return _agent

    _ensure_model_and_db()
    _agent = _build_agent()
    return _agent


# ---------- internal helpers --------------------------------------------------

def _ensure_model_and_db() -> None:
    """Validate that a model provider is available and the checkpointer is set up."""
    if _is_model_available():
        setup_checkpointer()
        return

    keys: list[str] = []
    if not settings.OPENAI_API_KEY:
        keys.append("OPENAI_API_KEY")
    if not settings.ANTHROPIC_API_KEY:
        keys.append("ANTHROPIC_API_KEY")
    if not settings.CHATOLLAMA_BASE_URL:
        keys.append("CHATOLLAMA_BASE_URL")

    raise ValueError(
        f"No LLM provider configured. Set one of: {', '.join(keys)}."
    )


def _is_model_available() -> bool:
    """Return ``True`` when an LLM provider key and corresponding library exist."""
    if settings.OPENAI_API_KEY:
        try:
            from langchain_openai import ChatOpenAI  # noqa: F401

            return True
        except ImportError:
            pass

    if settings.ANTHROPIC_API_KEY:
        try:
            from langchain_anthropic import ChatAnthropic  # noqa: F401

            return True
        except ImportError:
            pass

    if settings.CHATOLLAMA_BASE_URL:
        try:
            from langchain_ollama import ChatOllama  # noqa: F401

            return True
        except ImportError:
            pass

    return False


def _build_agent() -> Any:
    """Instantiate the DeepAgent with an appropriate model provider."""

    # ---------- model instantiation ----------
    if settings.OPENAI_API_KEY:
        from langchain_openai import ChatOpenAI

        model = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
        )
        provider = "openai"
    elif settings.ANTHROPIC_API_KEY:
        from langchain_anthropic import ChatAnthropic

        model = ChatAnthropic(
            model=settings.LLM_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
        )
        provider = "anthropic"
    elif settings.CHATOLLAMA_BASE_URL:
        from langchain_ollama import ChatOllama

        model = ChatOllama(
            model=settings.LLM_MODEL,
            base_url=settings.CHATOLLAMA_BASE_URL,
        )
        provider = "chatollama"
    else:
        raise RuntimeError("No provider selected — this should not be reached")

    # ---------- checkpointer & store ----------
    checkpointer: PostgresSaver = get_checkpointer()
    store: AsyncPostgresStore = get_store()

    # ---------- agent construction ----------
    from deepagents import create_deep_agent

    agent = create_deep_agent(
        model=model,
        system_prompt=(
            "You are a helpful chatbot assistant. "
            "Answer questions clearly and concisely."
        ),
        checkpointer=checkpointer,
        store=store,
    )

    logger.info(
        "DeepAgent built successfully with model=%s",
        getattr(model, "model_name", model),
    )
    return agent


# ---------------------------------------------------------------------------
# Public helpers ---------------------------------------------------------------
# ---------------------------------------------------------------------------

def reset_agent() -> None:
    """Clear the cached agent singleton (useful for testing / reloading)."""
    global _agent
    _agent = None


def setup_agent() -> None:
    """Set up the PostgreSQL checkpointer tables.

    Safe to call multiple times — ``setup_checkpointer()`` is idempotent
    within *langgraph-checkpoint-postgres*.
    """
    setup_checkpointer()
