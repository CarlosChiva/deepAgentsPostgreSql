"""Custom exception classes for the DeepAgents Chat API.

Centralises all domain-specific exceptions so that business logic
never raises bare ``HTTPException`` calls scattered throughout
the code base.

Each class inherits from :class:`fastapi.exceptions.HTTPException
<fastapi.HTTPException>` to integrate seamlessly with FastAPI's
error-flow.

Usage
-----
Register the handlers once during application startup:

.. code-block:: python

    # app/main.py
    from fastapi import FastAPI
    from app.core.exceptions import register_exception_handlers

    app = FastAPI()
    register_exception_handlers(app)

Raising in service / router code:

.. code-block:: python

    from app.core.exceptions import TenantNotFoundError

    async def get_tenant(user_id: str):
        tenant = find_tenant(user_id)
        if tenant is None:
            raise TenantNotFoundError(user_id=user_id)
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.exceptions import HTTPException
from fastapi.requests import Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


# ── Custom exception classes ────────────────────────────────


class TenantNotFoundError(HTTPException):
    """Raised when a tenant database or agent cannot be located.

    Status code: **404 — Not Found**

    Parameters
    ----------
    detail : str, optional
        Human-readable message.  Defaults to a template that includes
        the failing *user_id* when provided.
    user_id : str, optional
        The tenant identifier that was not found.  Used to build a
        default error message.
    """

    def __init__(
        self, *, detail: str | None = None, user_id: str | None = None
    ) -> None:
        if detail is None:
            detail = (
                f"Tenant for user_id='{user_id}' not found"
                if user_id is not None
                else "Tenant not found"
            )
        super().__init__(status_code=404, detail=detail)


class AgentUnavailableError(HTTPException):
    """Raised when the underlying LLM agent model is unavailable.

    Status code: **503 — Service Unavailable**

    Parameters
    ----------
    detail : str, optional
        Human-readable message.  Defaults to
        ``"Agent model is currently unavailable"``.
    """

    def __init__(self, *, detail: str | None = None) -> None:
        if detail is None:
            detail = "Agent model is currently unavailable"
        super().__init__(status_code=503, detail=detail)


class InvalidUserIdError(HTTPException):
    """Raised when *user_id* fails validation (format, block-list, length).

    Status code: **422 — Unprocessable Entity**

    Parameters
    ----------
    detail : str, optional
        Human-readable message.  Defaults to
        ``"Invalid user_id"``.
    user_id : str, optional
        The offending identifier, logged server-side for debugging.
    """

    def __init__(
        self, *, detail: str | None = None, user_id: str | None = None
    ) -> None:
        if detail is None:
            detail = "Invalid user_id"
        super().__init__(status_code=422, detail=detail)
        if user_id is not None:
            logger.warning("InvalidUserIdError for user_id=%r", user_id)


class DatabaseConnectionError(HTTPException):
    """Raised when a connection to PostgreSQL cannot be established.

    Status code: **500 — Internal Server Error**

    Parameters
    ----------
    detail : str, optional
        Human-readable message.  Defaults to
        ``"Database connection failed"``.
    """

    def __init__(self, *, detail: str | None = None) -> None:
        if detail is None:
            detail = "Database connection failed"
        super().__init__(status_code=500, detail=detail)


class TenantQuotaExceededError(HTTPException):
    """Raised when the maximum number of cached tenants is reached.

    Status code: **429 — Too Many Requests**

    Parameters
    ----------
    detail : str, optional
        Human-readable message.  Defaults to
        ``"Tenant cache quota exceeded"``.
    max_cache_size : int, optional
        The configured limit, included in the default message for
        debugging.
    """

    def __init__(
        self, *, detail: str | None = None, max_cache_size: int | None = None
    ) -> None:
        if detail is None:
            if max_cache_size is not None:
                detail = (
                    f"Tenant cache quota exceeded "
                    f"(max_cache_size={max_cache_size})"
                )
            else:
                detail = "Tenant cache quota exceeded"
        super().__init__(status_code=429, detail=detail)


# ── Exception handlers ──────────────────────────────────────

_EXCEPTION_HANDLERS: tuple[type[HTTPException], ...] = (
    TenantNotFoundError,
    AgentUnavailableError,
    InvalidUserIdError,
    DatabaseConnectionError,
    TenantQuotaExceededError,
)


def _http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    """Generic handler for all custom ``HTTPException`` subclasses.

    Returns a JSON response with the *status_code* and *detail* from
    the exception, while logging the original error for server-side
    traceability.
    """
    logger.warning(
        "%s: %s (url=%s)",
        exc.__class__.__name__,
        exc.detail,
        request.url.path,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Register custom exception handlers on *app*.

    Call this once during application startup (e.g. at the bottom of
    ``app/main.py`` or inside the lifespan callback).
    """
    for exc_class in _EXCEPTION_HANDLERS:
        app.add_exception_handler(exc_class, _http_exception_handler)
        logger.info("Registered exception handler for %s", exc_class.__name__)
