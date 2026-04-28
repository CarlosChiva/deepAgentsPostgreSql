"""Database and persistence helpers.

Provides singleton factories para AsyncPostgresSaver (checkpointer)
y AsyncPostgresStore (long-term memory store), utilidad check_db_connection,
y flag de graceful-shutdown.

IMPORTANTE:
- Para checkpointing de conversación usa AsyncPostgresSaver (aio)
- Para long-term memory store usa AsyncPostgresStore
- Ambos deben inicializarse en el lifespan de FastAPI, NO lazily
- autocommit=True y row_factory=dict_row son obligatorios al pasar conexiones manuales
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # ← CORREGIDO: era PostgresSaver
from langgraph.store.postgres import AsyncPostgresStore

from app.config import settings

logger = logging.getLogger(__name__)


async def ensure_db_exists(postgres_url: str) -> None:
    """Ensure the database specified in postgres_url exists."""
    from psycopg import OperationalError, AsyncConnection

    parts = postgres_url.replace("postgresql://", "").replace("postgres://", "").split("/")
    if len(parts) < 2:
        return

    db_name = parts[-1].split("?")[0]  # quitar query params si los hay
    no_db_url = postgres_url.rsplit("/", 1)[0]

    for fallback_db in ("template1", "postgres"):
        try:
            conn = await AsyncConnection.connect(f"{no_db_url}/{fallback_db}", autocommit=True)
            async with conn:
                cur = conn.cursor()
                await cur.execute(
                    "SELECT 1 FROM pg_database WHERE datname = %s", (db_name,)
                )
                if not await cur.fetchone():
                    await cur.execute(f'CREATE DATABASE "{db_name}"')
            return
        except OperationalError:
            continue


# ------ singleton state ------

_checkpointer: AsyncPostgresSaver | None = None
_checkpointer_cm = None
_checkpointer_initialized: bool = False

_store: AsyncPostgresStore | None = None
_store_cm = None
_store_initialized: bool = False


async def get_checkpointer() -> AsyncPostgresSaver:
    """Return singleton AsyncPostgresSaver, lazily initializing it.
    
    MEJOR PRÁCTICA: inicializar en el lifespan de FastAPI en lugar de usar esta función.
    Ver init_db() más abajo.
    """
    global _checkpointer, _checkpointer_cm, _checkpointer_initialized

    if _checkpointer is not None:
        return _checkpointer

    # AsyncPostgresSaver.from_conn_string() → async context manager
    # Hay que usar __aenter__, nunca __enter__
    _checkpointer_cm = AsyncPostgresSaver.from_conn_string(settings.postgres_url)
    _checkpointer = await _checkpointer_cm.__aenter__()  # ← CORREGIDO

    try:
        await _checkpointer.setup()  # ← CORREGIDO: es async en AsyncPostgresSaver
    except Exception as e:
        err = str(e)
        if "already exists" not in err and "already initialized" not in err:
            logger.warning(f"Checkpointer setup warning: {e}")

    _checkpointer_initialized = True
    return _checkpointer


async def get_store() -> AsyncPostgresStore:
    """Return singleton AsyncPostgresStore, lazily initializing it."""
    global _store, _store_cm, _store_initialized

    if _store is not None:
        return _store

    if not _store_initialized:
        _store_cm = AsyncPostgresStore.from_conn_string(settings.postgres_url)
        _store = await _store_cm.__aenter__()
        try:
            await _store.setup()
        except Exception as e:
            err = str(e)
            if "already exists" not in err and "already initialized" not in err:
                logger.warning(f"Store setup warning: {e}")
        _store_initialized = True

    return _store


@asynccontextmanager
async def init_db():
    """
    Async context manager para usar en el lifespan de FastAPI.
    
    Este es el patrón RECOMENDADO en producción:
    
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with init_db():
                yield
    
    Mantiene las conexiones abiertas durante toda la vida de la app
    y las cierra limpiamente al apagar.
    """
    global _checkpointer, _checkpointer_cm, _checkpointer_initialized
    global _store, _store_cm, _store_initialized

    async with (
        AsyncPostgresSaver.from_conn_string(settings.postgres_url) as checkpointer,
        AsyncPostgresStore.from_conn_string(settings.postgres_url) as store,
    ):
        await checkpointer.setup()
        await store.setup()

        _checkpointer = checkpointer
        _checkpointer_initialized = True
        _store = store
        _store_initialized = True

        logger.info("PostgreSQL checkpointer y store inicializados correctamente.")
        yield  # la app corre aquí

    # Al salir del context manager, las conexiones se cierran automáticamente
    _checkpointer = None
    _checkpointer_initialized = False
    _store = None
    _store_initialized = False
    logger.info("PostgreSQL checkpointer y store cerrados.")


# ------ historial de conversación por thread_id ------

async def get_chat_history(thread_id: str) -> list[dict]:
    """
    Recupera el historial de mensajes de un thread_id dado.
    
    Devuelve lista de mensajes en formato:
        [{"role": "human"|"ai"|"tool", "content": "..."}]
    
    Uso:
        history = await get_chat_history("mi-thread-123")
    """
    checkpointer = await get_checkpointer()
    config = {"configurable": {"thread_id": thread_id}}

    # get() devuelve el checkpoint más reciente del thread
    checkpoint = await checkpointer.aget(config)  # ← método async

    if checkpoint is None:
        return []

    messages = checkpoint.get("channel_values", {}).get("messages", [])

    result = []
    for msg in messages:
        # Los mensajes pueden ser objetos LangChain o dicts
        if hasattr(msg, "type"):
            role = {"human": "human", "ai": "ai", "tool": "tool"}.get(msg.type, msg.type)
            result.append({"role": role, "content": msg.content})
        elif isinstance(msg, dict):
            result.append(msg)

    return result


async def list_thread_checkpoints(thread_id: str) -> list:
    """
    Lista todos los checkpoints de un thread (útil para time-travel / debug).
    
    Uso:
        checkpoints = await list_thread_checkpoints("mi-thread-123")
    """
    checkpointer = await get_checkpointer()
    config = {"configurable": {"thread_id": thread_id}}
    return [c async for c in checkpointer.alist(config)]  # ← método async iterator


# ------ utility helpers ------

def check_db_connection(postgres_url: str | None = None) -> bool:
    """Attempt a brief connection to PostgreSQL and ping it."""
    url = postgres_url or settings.postgres_url
    from psycopg import connect  # psycopg3 síncrono

    try:
        with connect(url) as conn:
            cur = conn.cursor()
            cur.execute("SELECT 1")  # ← CORREGIDO: string, no text() de SQLAlchemy
            row = cur.fetchone()
            return row is not None and row[0] == 1
    except Exception:
        return False


def is_checkpointer_ready() -> bool:
    """Return True when the checkpointer has been successfully set up."""
    return _checkpointer_initialized


# ------ graceful shutdown flag ------

__is_shutting_down: bool = False
_shutdown_event = threading.Event()


def set_shutting_down(value: bool = True) -> None:
    global __is_shutting_down
    __is_shutting_down = value
    if value:
        _shutdown_event.set()
    else:
        _shutdown_event.clear()


def is_shutting_down() -> bool:
    return __is_shutting_down


async def close_checkpointer() -> None:
    """Cierra el checkpointer. Idempotente."""
    global _checkpointer, _checkpointer_cm, _checkpointer_initialized

    if _checkpointer is None:
        return

    if _checkpointer_cm is not None:
        await _checkpointer_cm.__aexit__(None, None, None)  # ← CORREGIDO: es async
        _checkpointer_cm = None

    _checkpointer = None
    _checkpointer_initialized = False


async def close_store() -> None:
    """Cierra el store. Idempotente."""
    global _store, _store_cm, _store_initialized

    if _store is None:
        return

    if _store_cm is not None:
        await _store_cm.__aexit__(None, None, None)
        _store_cm = None

    _store = None
    _store_initialized = False