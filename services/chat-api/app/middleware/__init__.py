"""Middleware module.

Re-exports middleware symbols from ``app.infrastructure.middleware`` as a
compatibility layer.  Once the code is migrated into this package the
import path will be updated accordingly.
"""

from app.infrastructure.middleware import (
    UserMiddleware,
    _extract_user_id_from_request,
    validate_user_id,
)

__all__ = [
    "UserMiddleware",
    "_extract_user_id_from_request",
    "validate_user_id",
]
