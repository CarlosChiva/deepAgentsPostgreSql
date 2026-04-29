r"""Shared DeepAgent factory with model provider selection.

A single shared checkpointer/store backed by the application's
PostgreSQL database is used by all requests.

Key flow
========
1. *build_agent()* lazily creates a checkpointer + store + backend
   from the app's database URI, then creates a DeepAgent instance.
2. *get_agent()* returns the singleton agent (lazy initialisation).

USAGE::

    agent = await get_agent()
    await agent.ainvoke(...)
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from .config import settings

logger = logging.getLogger(__name__)

# ─── Module-level singleton (shared across all requests) ───

_agent: Any = None
_checkpointer: AsyncPostgresSaver | None = None
_store: AsyncPostgresStore | None = None
_initialized: bool = False


# ─── Public API ────────────────────────────────────────────


async def get_agent() -> Any:
    """Return the singleton DeepAgent (creates it lazily on first call)."""
    global _agent, _checkpointer, _store, _initialized
    if _initialized and _agent is not None:
        return _agent

    model = _build_model()
    checkpointer = await _get_checkpointer()
    store = await _get_store()
    backend = await _build_backend()
    _agent = _build_agent(model, checkpointer, store, backend)
    _initialized = True
    return _agent


# ─── DeepAgent construction ───────────────────────────────


def _build_agent(
    model: Any,
    checkpointer: AsyncPostgresSaver,
    store: AsyncPostgresStore,
    backend: Any,
) -> Any:
    """Build a DeepAgent (tenant-context helper)."""
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


# ─── Model provider selection ─────────────────────────────


def _build_model() -> Any:
    """Return the LLM model instance using the first available provider.

    Priority: OpenAI → Anthropic → ChatOllama → error.
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
    """Return ``True`` when at least one LLM provider is configured and installed."""
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


# ─── Checkpointer / store helpers ─────────────────────────


async def _get_checkpointer() -> AsyncPostgresSaver:
    """Lazily create and cache the shared checkpointer."""
    global _checkpointer, _initialized
    if _checkpointer is not None:
        return _checkpointer

    from .database import get_db_uri  # avoid circular import

    saver = AsyncPostgresSaver.from_conn_string(get_db_uri())
    await saver
    _checkpointer = saver
    logger.info("Shared checkpointer created")
    return _checkpointer


async def _get_store() -> AsyncPostgresStore:
    """Lazily create and cache the shared store."""
    global _store, _initialized
    if _store is not None:
        return _store

    from .database import get_db_uri  # avoid circular import

    store = AsyncPostgresStore.from_conn_string(get_db_uri())
    await store
    _store = store
    logger.info("Shared store created")
    return _store


# ─── Backend helper ───────────────────────────────────────


async def _build_backend() -> Any:
    """Build the backend for the shared agent.

    For a single‑tenant deployment we simply return a PostgresBackend
    that points at the application's own database.
    """
    from deepagents_backends import PostgresConfig

    config = PostgresConfig(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=settings.POSTGRES_DB,
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        table="agent_files",
    )
    from deepagents_backends import PostgresBackend

    backend = PostgresBackend(config=config)
    await backend.initialize()
    return backend


async def _ensure_model_and_db() -> None:
    """Validate that a model is available and the checkpointer is initialised."""
    if not _is_model_available():
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
async def get_agent_for_user(user_id: str) -> Any:
    from .agent_manager import get_agent
    return await get_agent(user_id)