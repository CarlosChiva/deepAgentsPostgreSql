"""Agent factory — construction logic for DeepAgent instances.

Centralises agent creation, model provider selection, and singleton
management so that the rest of the application can request a ready-to-use
agent without concerning itself with checkpointer, store, or backend wiring.

Key public API
--------------
- :func:`get_agent` — return the singleton agent (lazy initialisation).
- :func:`build_model` — select an LLM provider based on configured API keys.
- :func:`create_deep_agent` — factory to build a DeepAgent with explicit
  parameters (useful for multi-tenant scenarios).
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Module-level singleton state ─────────────────────────────────────

_agent: Any = None
_checkpointer: AsyncPostgresSaver | None = None
_store: AsyncPostgresStore | None = None
_initialized: bool = False

# ── Public API ───────────────────────────────────────────────────────


async def get_agent() -> Any:
    """Return the singleton DeepAgent (creates it lazily on first call).

    The agent is wired to a shared :class:`AsyncPostgresSaver` checkpointer,
    :class:`AsyncPostgresStore` store, and a :class:`PostgresBackend`
    backend — all backed by the application's PostgreSQL database.

    Returns
    -------
    The configured DeepAgent instance.
    """
    global _agent, _checkpointer, _store, _initialized
    if _initialized and _agent is not None:
        return _agent

    model = build_model()
    checkpointer = await _get_checkpointer()
    store = await _get_store()
    backend = await _build_backend()
    _agent = _build_agent(model, checkpointer, store, backend)
    _initialized = True
    return _agent


def build_model() -> Any:
    """Return the LLM model instance using the first available provider.

    Provider priority
    ------------------
    1. OpenAI  — requires :data:`settings.OPENAI_API_KEY` and *langchain-openai*.
    2. Anthropic — requires :data:`settings.ANTHROPIC_API_KEY` and *langchain-anthropic*.
    3. Ollama  — requires :data:`settings.CHATOLLAMA_BASE_URL` and *langchain-ollama*.

    Returns
    -------
    An instantiated LangChain chat model (e.g. :class:`ChatOpenAI`).

    Raises
    ------
    ValueError
        If none of the providers are configured or their packages
        are importable.
    """
    if settings.OPENAI_API_KEY:
        try:
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=settings.LLM_MODEL,
                api_key=settings.OPENAI_API_KEY,
            )
        except ImportError:
            pass

    if settings.ANTHROPIC_API_KEY:
        try:
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=settings.LLM_MODEL,
                api_key=settings.ANTHROPIC_API_KEY,
            )
        except ImportError:
            pass

    if settings.CHATOLLAMA_BASE_URL:
        try:
            from langchain_ollama import ChatOllama

            return ChatOllama(
                model=settings.LLM_MODEL,
                base_url=settings.CHATOLLAMA_BASE_URL,
            )
        except ImportError:
            pass

    # None of the providers are available — build a helpful error message.
    missing: list[str] = []
    if not settings.OPENAI_API_KEY:
        missing.append("OPENAI_API_KEY")
    if not settings.ANTHROPIC_API_KEY:
        missing.append("ANTHROPIC_API_KEY")
    if not settings.CHATOLLAMA_BASE_URL:
        missing.append("CHATOLLAMA_BASE_URL")
    raise ValueError(
        f"No LLM provider configured. Set one of: {', '.join(missing)}."
    )


def create_deep_agent(
    model: Any,
    checkpointer: AsyncPostgresSaver,
    store: AsyncPostgresStore,
    backend: Any,
    checkpointer_uri: str | None = None,
    model_name: str | None = None,
) -> Any:
    """Factory function to create a DeepAgent with explicit parameters.

    Unlike :func:`get_agent` which returns a singleton, this function
    allows callers to supply their own checkpointer, store, and backend
    instances — useful for per-tenant or test scenarios.

    Parameters
    ----------
    model :
        The LLM model instance (e.g. from :func:`build_model`).
    checkpointer :
        A prepared :class:`AsyncPostgresSaver` instance.
    store :
        A prepared :class:`AsyncPostgresStore` instance.
    backend :
        The filesystem/storage backend (e.g. :class:`PostgresBackend`).
    checkpointer_uri :
        Optional URI the checkpointer was created with (for diagnostics).
    model_name :
        Optional name of the model (for diagnostics).

    Returns
    -------
    A fully configured DeepAgent graph.
    """
    from deepagents import create_deep_agent as _create

    return _create(
        model=model,
        system_prompt=(
            "You are a helpful chatbot assistant. "
            "Answer questions clearly and concisely."
        ),
        checkpointer=checkpointer,
        store=store,
        backend=backend,
    )


# ── Internal helpers ─────────────────────────────────────────────────


def _build_agent(
    model: Any,
    checkpointer: AsyncPostgresSaver,
    store: AsyncPostgresStore,
    backend: Any,
) -> Any:
    """Wire together the model, checkpointer, store and backend into a DeepAgent."""
    from deepagents import create_deep_agent

    return create_deep_agent(
        model=model,
        system_prompt=(
            "You are a helpful chatbot assistant. "
            "Answer questions clearly and concisely."
        ),
        checkpointer=checkpointer,
        store=store,
        backend=backend,
    )


async def _get_checkpointer() -> AsyncPostgresSaver:
    """Lazily create and cache the shared checkpointer."""
    global _checkpointer
    if _checkpointer is not None:
        return _checkpointer

    from app.infrastructure.database import get_db_uri  # avoid circular import

    saver = AsyncPostgresSaver.from_conn_string(get_db_uri())
    await saver
    _checkpointer = saver
    logger.info("Shared checkpointer created")
    return _checkpointer


async def _get_store() -> AsyncPostgresStore:
    """Lazily create and cache the shared store."""
    global _store
    if _store is not None:
        return _store

    from app.infrastructure.database import get_db_uri  # avoid circular import

    store = AsyncPostgresStore.from_conn_string(get_db_uri())
    await store
    _store = store
    logger.info("Shared store created")
    return _store


async def _build_backend() -> Any:
    """Build the backend for the shared agent.

    For a single-tenant deployment we simply return a :class:`PostgresBackend`
    that points at the application's own database.
    """
    from deepagents_backends import PostgresBackend
    from deepagents_backends import PostgresConfig

    config = PostgresConfig(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        table="agent_files",
    )
    backend = PostgresBackend(config=config)
    await backend.initialize()
    return backend
