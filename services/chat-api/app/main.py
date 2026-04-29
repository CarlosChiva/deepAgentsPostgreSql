"""Main FastAPI application entry-point for the DeepAgents Chat API.

Wires together the existing configuration, database, agent factory, and routers
into a single FastAPI instance.  Uses the modern *lifespan* pattern for startup
and shutdown lifecycle management (replaces the deprecated ``@app.on_event``).
"""

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import settings
from app.database import init_db_tables

logger = logging.getLogger(__name__)

# ---------- ---- ---------- ---- ---------- ---- ---------- ---- ---------- ---- #
# Module-level flag set at the end of startup so ``/ready`` can read it.
# ---------- ---- ---------- ---- ---------- ---- ---------- ---- ---------- ---- #
_startup_complete: bool = False
_startup_failed: bool = False


# ------ lifespan ------ ------ ------ ------ ------ ------ ------ ------ ->

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manages startup and shutdown lifecycle events.

    On startup the checkpointer and store tables are initialised on the
    shared PostgreSQL database.

    On shutdown all resources are cleaned up.
    """

    global _startup_complete, _startup_failed
    logger.info("Starting up DeepAgents Chat API \u2026")

    try:
        await init_db_tables()
        logger.info("Database tables initialised")
    except Exception as exc:
        logger.error("Failed to initialise database tables during startup: %s", exc)
        _startup_failed = True

    if not _startup_failed:
        _startup_complete = True
        logger.info("Application startup complete")
    else:
        logger.error(
            "Application startup completed with errors"
            " \u2014 some features unavailable"
        )

    # Yield -- app is now serving requests
    yield

    # ---- shutdown phase ------ ------ ------ ------ ------ ------ ----

    _startup_complete = False
    logger.info("Shutdown starting \u2026")
    logger.info("Shutdown complete")


# ------ application factory ------ ------ ------ ------ ------ ------ ------>

app = FastAPI(
    title="DeepAgents Chat API",
    description="Chat API with DeepAgents and PostgreSQL",
    version="0.1.0",
    lifespan=lifespan,
)


# ------ CORS middleware ------ ------ ------ ------ ------ ------ ------ ->

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------ request logging middleware ------ ------ ------ ------ ------ ->


@app.middleware("http")
async def log_requests_time(request: Request, call_next):
    """Log every request's method, path, status code, and duration."""

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


# ------ routers ------ ------ ------ ------ ------ ------ ------ ------ ---

from app.routers.chat import router as chat_router  # noqa: E402

app.include_router(chat_router, prefix="/api/v1")


# ------ /health endpoint ------ ------ ------ ------ ------ ------ ->


from app.health import check_health  # noqa: E402


@app.get("/health", tags=["utils"])
async def health() -> JSONResponse:
    """Extended health-check: postgres.

    Returns ``status=200`` when all subsystems are healthy,
    ``status=503`` when one or more subsystems are degraded.
    """
    health_status = check_health()
    status_code = 503 if health_status.get("status") == "degraded" else 200
    return JSONResponse(content=health_status, status_code=status_code)


# ------ /ready endpoint ------ ------ ------ ------ ------ ------ ->


@app.get("/ready", tags=["utils"])
async def ready() -> JSONResponse:
    """Readiness probe \u2014 ``True`` once startup phase has completed.

    Typically used by K8s ``startupProbe`` / ``readinessProbe``.
    """
    if not _startup_complete or _startup_failed:
        return JSONResponse(
            {"ready": False, "reason": "startup incomplete"},
            status_code=503,
        )
    return JSONResponse({"ready": True}, status_code=200)


# Export the app instance for ``fastapi dev / fastapi run`` discovery.
__all__ = ["app"]
