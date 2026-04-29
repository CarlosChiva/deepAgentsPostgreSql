"""Tests for the user-id extraction middleware (app/middleware/user_id_extractor.py).

Covers:
- Extraction from X-User-ID header, query param, and cookie.
- Priority ordering: header > query > cookie.
- Validation (pattern, length, blocklist empty, empty string).
- Middleware injection into request.state.
- 401 enforcement behaviour.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from starlette.routing import Match

from app.config import settings as _settings


# ────────────────────────────────────────────
# Helpers: build a Tiny app with UserMiddleware attached
# ────────────────────────────────────────────


def _build_app(enforce_user_id: bool = True) -> FastAPI:
    """Create a minimal FastAPI app that has UserMiddleware wired in."""
    from app.middleware.user_id_extractor import UserMiddleware  # noqa: E402

    app = FastAPI()

    @app.get("/echo")
    async def echo(request: Request) -> dict[str, Any]:
        uid = getattr(request.state, "user_id", None)
        return {"user_id": uid}

    if enforce_user_id:
        app.add_middleware(
            UserMiddleware,
            enforce_user_id=True,
        )
    else:
        app.add_middleware(
            UserMiddleware,
            enforce_user_id=False,
        )

    return app


# ────────────────────────────────────────────
# Extraction tests
# ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_user_id_from_header():
    """X-User-ID header is extracted when present."""
    app = _build_app(enforce_user_id=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo", headers={"X-User-ID": "alice"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "alice"


@pytest.mark.asyncio
async def test_extract_user_id_from_query_param():
    """When no header is present, user_id comes from the query string."""
    app = _build_app(enforce_user_id=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo?user_id=bob123")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "bob123"


@pytest.mark.asyncio
async def test_extract_user_id_from_cookie():
    """When no header and no query param, user_id comes from the cookie."""
    app = _build_app(enforce_user_id=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo", cookies={"user_id": "cookie-user"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "cookie-user"


@pytest.mark.asyncio
async def test_extract_user_id_header_priority_over_query():
    """Header wins over query param when both are present."""
    app = _build_app(enforce_user_id=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo", headers={"X-User-ID": "header-user"}, params={"user_id": "query-user"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "header-user"


@pytest.mark.asyncio
async def test_extract_user_id_header_priority_over_cookie():
    """Header wins over cookie when both are present."""
    app = _build_app(enforce_user_id=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get(
            "/echo",
            headers={"X-User-ID": "header-user"},
            cookies={"user_id": "cookie-user"},
        )
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "header-user"


# ────────────────────────────────────────────
# Validation tests
# ────────────────────────────────────────────

from app.middleware.user_id_extractor import validate_user_id  # noqa: E401, E402


@pytest.mark.asyncio
async def test_validate_valid_user_ids():
    """A set of valid user-ids are accepted."""
    valid_ids = [
        "alice", "bob123", "test-user", "a", "user_123", "USER-ABC_456",
    ]
    for uid in valid_ids:
        result = validate_user_id(uid)
        assert result == uid


@pytest.mark.asyncio
async def test_validate_empty_user_id():
    """Empty string is rejected."""
    with pytest.raises(Exception) as exc_info:
        validate_user_id("")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_validate_too_long_user_id():
    """A user-id longer than 53 characters is rejected."""
    too_long = "a" * 54
    with pytest.raises(Exception) as exc_info:
        validate_user_id(too_long)
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_invalid_characters():
    """Spaces, @, / etc. are rejected."""
    invalid = ["user id", "user@id", "user/id", "user.name"]
    for uid in invalid:
        with pytest.raises(Exception) as exc_info:
            validate_user_id(uid)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_blocklisted_names():
    """Blocklisted user-id names (postgres system databases / special prefixes) are rejected."""
    blocklisted = [
        "postgres",
        "template0",
        "template1",
        "information_schema",
        "pg_catalog",
        "pg_test",
        "pg_something",
    ]
    for uid in blocklisted:
        with pytest.raises(Exception) as exc_info:
            validate_user_id(uid)
        assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_validate_special_characters():
    """Various special characters cause rejection."""
    special_ids = ["user name", "user@domain", "user/id", "user.name", "...", "!!"]
    for uid in special_ids:
        with pytest.raises(Exception) as exc_info:
            validate_user_id(uid)
        assert exc_info.value.status_code == 400


# ────────────────────────────────────────────
# Middleware integration
# ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_middleware_injects_user_id():
    """Middleware injects user_id into request.state.user_id."""
    app = _build_app(enforce_user_id=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo", headers={"X-User-ID": "inject-test"})
    assert resp.status_code == 200
    assert resp.json()["user_id"] == "inject-test"


@pytest.mark.asyncio
async def test_middleware_returns_401_when_enforced():
    """When TENANT_ENFORCE_USER_ID=True and no user_id is supplied → 401."""
    app = _build_app(enforce_user_id=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo")
    assert resp.status_code == 401
    body = resp.json()
    assert "user_id" in body.get("detail", "").lower()


@pytest.mark.asyncio
async def test_middleware_allows_request_when_not_enforced():
    """When TENANT_ENFORCE_USER_ID=False and no user_id is supplied → 200."""
    app = _build_app(enforce_user_id=False)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo")
    assert resp.status_code == 200
    # user_id may be None — that is acceptable
    assert resp.json()["user_id"] is None


@pytest.mark.asyncio
async def test_validation_error_propagates_from_middleware():
    """An invalid user-id (e.g. spaces) raises 400 from the middleware, not 401."""
    app = _build_app(enforce_user_id=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        resp = await ac.get("/echo", headers={"X-User-ID": "user name"})
    assert resp.status_code == 400
