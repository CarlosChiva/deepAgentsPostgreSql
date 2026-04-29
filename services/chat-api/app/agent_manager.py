"""Per-user DeepAgent with isolated PostgreSQL databases.

One database per user_id: deepagent_{user_id}
Agents are cached in memory after first load.
"""

from __future__ import annotations

import logging
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

# { user_id: agent }
_agents: dict[str, Any] = {}
_checkpointer_cms: dict[str, Any] = {}
_store_cms: dict[str, Any] = {}

def _db_uri(user_id: str) -> str:
    db_name = f"{settings.TENANT_PREFIX}{user_id}"
    return f"{settings.postgres_uri}/{db_name}"

def _superuser_uri() -> str:
    return (
        f"postgresql://{settings.POSTGRES_USER}:{settings.POSTGRES_PASSWORD}"
        f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/postgres"
    )
async def _ensure_database(user_id: str) -> None:
    """Create the user database if it doesn't exist."""
    import psycopg

    db_name = f"{settings.TENANT_PREFIX}{user_id}"

    conn = await psycopg.AsyncConnection.connect(
        _superuser_uri(), autocommit=True
    )
    async with conn:
        cur = conn.cursor()
        await cur.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
        )
        if not await cur.fetchone():
            await cur.execute(f'CREATE DATABASE "{db_name}"')
            logger.info("Created database %s", db_name)


async def get_agent(user_id: str) -> Any:
    """Get or create the DeepAgent for a specific user_id.
    
    Each user gets their own isolated PostgreSQL database.
    """
    if user_id in _agents:
        return _agents[user_id]

    await _ensure_database(user_id)

    db_uri = _db_uri(user_id)

    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.store.postgres import AsyncPostgresStore

    checkpointer_cm = AsyncPostgresSaver.from_conn_string(db_uri)
    checkpointer = await checkpointer_cm.__aenter__()
    await checkpointer.setup()

    store_cm = AsyncPostgresStore.from_conn_string(db_uri)
    store = await store_cm.__aenter__()
    await store.setup()

    from deepagents import create_deep_agent
    from deepagents_backends import PostgresBackend, PostgresConfig
    from app.agent import _build_model

    model = _build_model()

    backend = PostgresBackend(config=PostgresConfig(
        host=settings.POSTGRES_HOST,
        port=settings.POSTGRES_PORT,
        database=f"{settings.TENANT_PREFIX}{user_id}",
        user=settings.POSTGRES_USER,
        password=settings.POSTGRES_PASSWORD,
        table="agent_files",
    ))
    await backend.initialize()

    agent = create_deep_agent(
        model=model,
        system_prompt=(
            "You are a helpful chatbot assistant. "
            "Answer questions clearly and concisely."
        ),
        checkpointer=checkpointer,
        store=store,
        backend=backend,
    )

    _checkpointer_cms[user_id] = checkpointer_cm
    _store_cms[user_id] = store_cm
    _agents[user_id] = agent

    logger.info("Agent loaded for user_id=%s (db=%s)", user_id, f"{settings.TENANT_PREFIX}{user_id}")
    return agent


async def close_all() -> None:
    """Close all agents. Call on app shutdown."""
    for user_id in list(_agents.keys()):
        await close_agent(user_id)


async def close_agent(user_id: str) -> None:
    """Close resources for a specific user."""
    if user_id not in _agents:
        return

    try:
        if user_id in _checkpointer_cms:
            await _checkpointer_cms.pop(user_id).__aexit__(None, None, None)
        if user_id in _store_cms:
            await _store_cms.pop(user_id).__aexit__(None, None, None)
    except Exception as e:
        logger.warning("Error closing agent for user_id=%s: %s", user_id, e)

    _agents.pop(user_id, None)
    logger.info("Agent closed for user_id=%s", user_id)