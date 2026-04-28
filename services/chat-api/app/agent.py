"""DeepAgent agent factory with model provider selection and singleton caching."""

from __future__ import annotations

import logging
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # ← CORREGIDO
from langgraph.store.postgres import AsyncPostgresStore

from .config import settings
from .database import get_checkpointer, get_store, is_checkpointer_ready

logger = logging.getLogger(__name__)

_agent: Any | None = None


async def get_agent() -> Any:
    """Build and return a singleton DeepAgent instance.

    Model provider selection (priority order):
    1. OpenAI  — si settings.OPENAI_API_KEY está definido
    2. Anthropic — si settings.ANTHROPIC_API_KEY está definido
    3. ChatOllama — si settings.CHATOLLAMA_BASE_URL está definido

    Raises ValueError cuando ningún proveedor está configurado.
    """
    global _agent

    if _agent is not None:
        return _agent

    await _ensure_model_and_db()
    _agent = await _build_agent()
    return _agent


# ---------- internal helpers ----------


async def _ensure_model_and_db() -> None:
    """Valida que haya un modelo disponible y el checkpointer esté listo."""
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

    # Solo inicializa el checkpointer si no fue inicializado ya por el lifespan.
    # Si usas init_db() en el lifespan de FastAPI, esto es un no-op.
    if not is_checkpointer_ready():
        await get_checkpointer()
        await get_store()


def _is_model_available() -> bool:
    """Return True cuando hay un proveedor LLM configurado e instalado."""
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


async def _build_agent() -> Any:
    """Construye el DeepAgent con el proveedor de modelo apropiado."""

    # ---------- model ----------
    if settings.OPENAI_API_KEY:
        from langchain_openai import ChatOpenAI
        model = ChatOpenAI(
            model=settings.LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
        )
    elif settings.ANTHROPIC_API_KEY:
        from langchain_anthropic import ChatAnthropic
        model = ChatAnthropic(
            model=settings.LLM_MODEL,
            api_key=settings.ANTHROPIC_API_KEY,
        )
    elif settings.CHATOLLAMA_BASE_URL:
        from langchain_ollama import ChatOllama
        model = ChatOllama(
            model=settings.LLM_MODEL,
            base_url=settings.CHATOLLAMA_BASE_URL,
        )
    else:
        raise RuntimeError("No provider selected — this should not be reached")

    # ---------- checkpointer & store ----------
    # get_checkpointer() y get_store() devuelven el singleton ya inicializado
    # (ya sea por init_db() en el lifespan o por _ensure_model_and_db() arriba)
    checkpointer: AsyncPostgresSaver = await get_checkpointer()  # ← tipo corregido
    store: AsyncPostgresStore = await get_store()

    # ---------- agent ----------
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
        "DeepAgent built with model=%s",
        getattr(model, "model_name", model),
    )
    return agent


# ---------- public helpers ----------


def reset_agent() -> None:
    """Limpia el singleton del agente (útil para tests / reload).

    El agente se reconstruirá en el próximo get_agent().
    """
    global _agent
    _agent = None


async def setup_agent() -> None:
    """Inicializa el checkpointer y el store si no están ya listos.

    Idempotente — seguro llamar múltiples veces.
    Normalmente no es necesario llamar esto directamente si usas
    init_db() en el lifespan de FastAPI.
    """
    if not is_checkpointer_ready():
        await get_checkpointer()
        await get_store()