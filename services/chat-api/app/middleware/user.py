"""User middleware module.

Provides middleware for extracting and validating the user identifier from
incoming requests.  This module was originally located at
``app.infrastructure.middleware`` and has been relocated as part of the
middleware directory refactor.

Exposes:
    - ``UserMiddleware`` — Starlette ``BaseHTTPMiddleware`` subclass
    - ``validate_user_id`` — standalone validation function
    - ``_extract_user_id_from_request`` — helper for reading user_id
"""

from __future__ import annotations

import logging
import re
from typing import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

logger = logging.getLogger(__name__)

VALID_USER_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")
BLOCKLIST_PREFIXES = ("pg_",)
BLOCKLIST_NAMES = {"postgres", "template0", "template1"}
MAX_USER_ID_LENGTH = 53  # 63 - len("deepagent_")


def validate_user_id(user_id: str) -> str:
    """Validate ``user_id`` format.

    - Must be non-empty
    - Must match ``^[a-zA-Z0-9_-]+$``
    - Max 53 characters
    - Blocklisted names are rejected
    """
    if not user_id:
        raise ValueError("user_id is required")

    if len(user_id) > MAX_USER_ID_LENGTH:
        raise ValueError(
            f"user_id too long (max {MAX_USER_ID_LENGTH} characters)"
        )

    if not VALID_USER_ID_PATTERN.match(user_id):
        raise ValueError(
            f"user_id contains invalid characters. "
            f"Only alphanumeric, _ and - are allowed."
        )

    if user_id in BLOCKLIST_NAMES:
        raise ValueError(f"user_id '{user_id}' is a reserved name")

    if any(user_id.startswith(prefix) for prefix in BLOCKLIST_PREFIXES):
        raise ValueError(
            f"user_id '{user_id}' starts with a reserved prefix"
        )

    return user_id


def _extract_user_id_from_request(request: Request) -> str | None:
    """Extract user_id from request with priority: header > query > cookie."""
    # Priority 1: X-User-ID header
    user_id = request.headers.get("x-user-id")
    if user_id:
        return user_id

    # Priority 2: user_id query parameter
    user_id = request.query_params.get("user_id")
    if user_id:
        return user_id

    # Priority 3: Cookie
    user_id = request.cookies.get("user_id")
    if user_id:
        return user_id

    return None


class UserMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware that extracts user_id from the request.

    Injects validated user_id into request.state.user_id.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Extract and validate user_id, then pass to the next handler."""
        raw = _extract_user_id_from_request(request)

        if raw:
            try:
                validated = validate_user_id(raw)
            except ValueError as exc:
                logger.warning("Invalid user_id: %s", exc)
                return Response(
                    content=f"Invalid user_id: {exc}",
                    status_code=400,
                )
            request.state.user_id = validated
        else:
            request.state.user_id = None

        return await call_next(request)


__all__ = ["UserMiddleware", "validate_user_id", "_extract_user_id_from_request"]
