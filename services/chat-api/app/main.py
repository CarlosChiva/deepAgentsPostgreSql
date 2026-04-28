"""Main FastAPI application entry-point for the DeepAgents Chat API.

Wires together the existing configuration, database, agent factory, and routers
into a single FastAPI instance.  Uses the modern *lifespan* pattern for startup
and shutdown lifecycle management (replaces the deprecated ``@app.on_event``).
"""

import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.agent import get_agent
from app.config import settings
from app.database import (
    close_checkpointer,
    close_store,
    ensure_db_exists,
    set_shutting_down,
    get_checkpointer,
    
)

logger = logging.getLogger(__name__)

# -------------- -------------- -------------- -------------- -------------- #
# Module-level flag set at the end of startup so ``/ready`` can read it.
# -------------- -------------- -------------- -------------- -------------- #
_startup_complete: bool = False
_startup_failed: bool = False


# ------ lifespan ------ ------ ------ ------ ------ ------ ------ ------ ->

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages startup and shutdown lifecycle events.

    On startup the PostgreSQL checkpointer tables are created (idempotent) and
    the DeepAgent instance is initialised so it is ready for the first request.

    On shutdown the checkpointer connection pool is closed and a module-level
    flag causes all subsequent requests to receive a 503.
    """

    # ---- startup phase ------ ------ ------ ------ ------ ------ ------ ->

    global _startup_complete, _startup_failed
    logger.info("Starting up DeepAgents Chat API …")

    # Ensure the target database exists before attempting table setup
    try:
        await ensure_db_exists(settings.postgres_url)
    except Exception as exc:
        logger.warning("ensure_db_exists failed (best effort): %s", exc)
        # Proceed anyway — downstream code will surface the real error

    try:
        await get_checkpointer()
        logger.info("PostgreSQL checkpointer initialised")
    except Exception as exc:
        logger.error("Failed to set up checkpointer during startup: %s", exc)
        _startup_failed = True

    try:
        await get_agent()
        logger.info("DeepAgent instance initialised")
    except Exception as exc:
        logger.error("Failed to initialise agent during startup: %s", exc)
        _startup_failed = True

    if not _startup_failed:
        _startup_complete = True
        logger.info("Application startup complete")
    else:
        logger.error(
            "Application startup completed with errors"
            " — some features unavailable"
        )

    # Yield -- app is now serving requests
    yield

    # ---- shutdown phase ------ ------ ------ ------ ------ ------ ----

    _startup_complete = False
    logger.info("Shutdown starting …")
    set_shutting_down(True)
    logger.info("Shutdown flag set -- no new requests accepted")

    await close_store()
    await close_checkpointer()

    logger.info("Shutdown complete")


# ------ application factory ------ ------ ------ ------ ------ ------ ------>

app = FastAPI(
    title="DeepAgents Chat API",
    description="Backend para chatbot multi-hilo con DeepAgents y PostgreSQL",
    version="0.1.0",
    lifespan=lifespan,
)


# ------ CORS middleware ------ ------ ------ ------ ------ ------ ------ ->

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------ request logging middleware ------ ------ ------ ------ ------ ->


@app.middleware("http")
async def log_requests_time(request: Request, call_next):
    """Log every request's method, path, status code, and duration."

    Middleware runs *before* the request handler and *after* the response.
    """

    timer = time.perf_counter()

    logger.info(
        "%s %s",
        request.method,
        request.url.path,
    )

    response = await call_next(request)

    elapsed = time.perf_counter() - timer
    logger.info(
        "%s -> %d  (%.3fs)",
        request.url.path,
        response.status_code,
        elapsed,
    )

    return response


# ------ shutdown-reject middleware ------ ------ ------ ------ ------ ->


@app.middleware("http")
async def reject_shutdown_requests(request: Request, call_next):
    """Return 503 when the app is in the shutdown phase."

    Any request arriving after ``set_shutting_down(True)`` is rejected.
    """
    from app.database import is_shutting_down  # noqa: E402 -- local import

    if is_shutting_down():
        body = json.dumps({"detail": "Service shutting down"}).encode()
        return Response(
            status_code=503,
            content=body,
            media_type="application/json",
        )

    return await call_next(request)


# ------ routers ------ ------ ------ ------ ------ ------ ------ ------ ---
# noqa: E402 — imported after app creation

from app.routers.chat import router as chat_router  # noqa: E402

app.include_router(chat_router, prefix="/api/v1")


# ------ /health endpoint ------ ------ ------ ------ ------ ------ ->


from app.health import check_health  # noqa: E402


@app.get("/health", tags=["utils"])
async def health() -> JSONResponse:
    """Extended health-check: postgres, checkpointer, agent."

    Returns ``status=200`` when all subsystems are healthy,
    ``status=503`` when one or more subsystems are degraded.
    """
    health_status = check_health()
    status_code = 503 if health_status.get("status") == "degraded" else 200
    return JSONResponse(content=health_status, status_code=status_code)


# ------ /ready endpoint ------ ------ ------ ------ ------ ------ ->


@app.get("/ready", tags=["utils"])
async def ready() -> JSONResponse:
    """Readiness probe — ``True`` once startup phase has completed."

    Typically used by K8s ``startupProbe`` / ``readinessProbe`.
    """
    if not _startup_complete or _startup_failed:
        return JSONResponse(
            {"ready": False, "reason": "startup incomplete"},
            status_code=503,
        )
    return JSONResponse({"ready": True}, status_code=200)


# Export the app instance for ``fastapi dev / fastapi run`` discovery.
__all__ = ["app"]
