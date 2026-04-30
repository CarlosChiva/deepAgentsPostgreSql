"""Centralised FastAPI dependency injection for the chat API.

Provides reusable dependency functions that routers can wire into
path operations via ``Annotated[..., Depends(...)]``.

Every dependency here follows the pattern:

1. A plain function that performs the work (extraction, validation,
   resource acquisition).
2. An ``Annotated`` type alias ending in ``Dep`` for convenient reuse in
   endpoint signatures.
"""

from __future__ import annotations

import logging
from typing import Annotated, AsyncGenerator

from fastapi import Depends, HTTPException, Request

from app.agents.tenant import TenantManager, get_tenant_manager as _get_tenant_manager_orig
from app.core.config import settings
from app.infrastructure.middleware import (
    _extract_user_id_from_request,
    validate_user_id,
)

logger = logging.getLogger(__name__)

# ── TenantManager dependency ─────────────────────────────────────────────


def get_tenant_manager() -> TenantManager:
    """Return the singleton :class:`~app.agents.tenant.TenantManager`.

    Delegates to :func:`~app.agents.tenant.get_tenant_manager` so that the
    existing module-level singleton and its lazy-initialisation logic are
    preserved.

    Returns
    -------
    The global :class:`~app.agents.tenant.TenantManager` instance.
    """
    return _get_tenant_manager_orig()


TenantManagerDep = Annotated[TenantManager, Depends(get_tenant_manager)]

# ── Validated user_id dependency ─────────────────────────────────────────


def get_validated_user_id(request: Request) -> str:
    """Extract and validate ``user_id`` from the current request.

    Priority order for extraction:

    1. **``X-User-ID`` header** — primary source for API clients.
    2. **``user_id`` query parameter** — fallback for browser-based clients.
    3. **``user_id`` cookie** — last-resort browser fallback.

    Validation runs the same rules as :func:`~app.infrastructure.middleware.validate_user_id`:
    regex pattern, length blocklist, and reserved-name blocklist.

    Parameters
    ----------
    request:
        The incoming :class:`~fastapi.Request` instance (injected by FastAPI).

    Returns
    -------
    A validated, non-empty, non-blocklisted user identifier string.

    Raises
    ------
    HTTPException
        * **401** — when no user_id is found and
          :data:`settings.TENANT_ENFORCE_USER_ID` is ``True``.
        * **400** — when the extracted value fails format or block-list
          validation.
    """
    user_id: str | None = _extract_user_id_from_request(request)

    if user_id is None:
        if settings.TENANT_ENFORCE_USER_ID:
            raise HTTPException(
                status_code=401,
                detail="Missing user_id — provide X-User-ID header, "
                "user_id query param, or user_id cookie",
            )
        return ""

    try:
        return validate_user_id(user_id)
    except ValueError as exc:
        logger.warning("Invalid user_id: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc)) from exc


ValidatedUserIdDep = Annotated[str, Depends(get_validated_user_id)]

# ── Database session dependency (stub / future use) ──────────────────────


async def get_db() -> AsyncGenerator[None, None]:
    """Async database-session dependency — **stub for future use**.

    The codebase currently manages PostgreSQL connections through
    :class:`~langgraph.checkpoint.postgres.aio.AsyncPostgresSaver` and
    :class:`~langgraph.store.postgres.AsyncPostgresStore` context managers
    inside :class:`~app.agents.tenant.TenantManager`.  This placeholder
    provides a standard ``yield``-based dependency signature for when a
    shared ORM session or connection-pool pattern is introduced.

    When a real session is wired up, the body will look like::

        async with async_session() as session:
            yield session

    Yields
    ------
    None
        Currently yields ``None`` (no-op).  Replace the body once a
        connection-managed session is in place.
    """
    yield


DBDep = Annotated[None, Depends(get_db)]
