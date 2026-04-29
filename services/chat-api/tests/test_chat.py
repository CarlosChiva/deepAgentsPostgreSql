"""Tests for the ``/chat`` endpoints (T013 + tenant-aware updates).

Tests the full HTTP layer against the FastAPI app using the ``async_client``
fixture from ``tests/conftest.py``, which wires up an ``httpx.AsyncClient``
with a test checkpointer, test store, and a mock ``DeepAgent`` (avoids real
LLM calls).

Endpoints under test::

    POST   /api/v1/chat/          – send a message (text or SSE)
    GET    /api/v1/chat/<id>      – retrieve conversation history

All tests include ``user_id`` in request payloads or headers to exercise
multi-tenancy.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import httpx_sse

# ── POST /api/v1/chat/ – happy path (with user_id) ────

@pytest.mark.asyncio
async def test_chat_post_returns_response(async_client: httpx.AsyncClient) -> None:
    """POST /api/v1/chat/ returns a valid ChatResponse (200) with user_id."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={"message": "Hello", "user_id": "test-user-1"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body.get("thread_id") is not None
    assert isinstance(body["message"], str)
    assert len(body["message"]) > 0
    assert body.get("user_id") == "test-user-1"

    # timestamp must be present and parseable as datetime
    ts_raw = body.get("timestamp")
    assert ts_raw is not None
    ts = datetime.fromisoformat(ts_raw)
    assert ts.tzinfo is not None


# ── POST /api/v1/chat/ – explicit thread_id with user_id ─

@pytest.mark.asyncio
async def test_chat_post_with_custom_thread_id(async_client: httpx.AsyncClient) -> None:
    """POST with a user-supplied thread_id echoes it back (200), with user_id."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={
            "message": "Hi",
            "thread_id": "custom-thread-42",
            "user_id": "test-user-2",
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["thread_id"] == "custom-thread-42"
    assert body.get("user_id") == "test-user-2"
    assert isinstance(body["message"], str)


# ── GET /api/v1/chat/<thread_id> – history after messages ─

@pytest.mark.asyncio
async def test_chat_history_returns_messages(async_client: httpx.AsyncClient) -> None:
    """Sending a message then fetching history returns those messages (with user_id)."""

    # 1. Post a message (uses test checkpointer)
    post_resp = await async_client.post(
        "/api/v1/chat/",
        json={"message": "What is AI?", "user_id": "test-user-3"},
    )
    assert post_resp.status_code == 200
    thread_id = post_resp.json()["thread_id"]

    # 2. Fetch history for that thread
    hist_resp = await async_client.get(
        f"/api/v1/chat/{thread_id}",
        headers={"X-User-ID": "test-user-3"},
    )
    assert hist_resp.status_code == 200

    body = hist_resp.json()
    assert body["thread_id"] == thread_id
    assert body["message_count"] >= 1

    # 3. The stored message roles should include user and assistant
    roles = [msg["role"] for msg in body["messages"]]
    assert "user" in roles
    assert "assistant" in roles


# ── GET /api/v1/chat/<thread_id> – empty for new thread ─

@pytest.mark.asyncio
async def test_chat_history_empty_for_new_thread(async_client: httpx.AsyncClient) -> None:
    """A brand-new thread_id yields an empty message list (no error), with user_id."""

    response = await async_client.get(
        "/api/v1/chat/brand-new-never-used",
        headers={"X-User-ID": "test-user-4"},
    )
    assert response.status_code == 200

    body = response.json()
    assert body["thread_id"] == "brand-new-never-used"
    assert body["messages"] == []
    assert body["message_count"] == 0


# ── POST /api/v1/chat/ (stream=true) – SSE headers + events ─

@pytest.mark.asyncio
async def test_chat_stream_returns_sse_events(async_client: httpx.AsyncClient) -> None:
    """Streaming endpoint returns SSE headers and at least one data event (with user_id)."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={
            "message": "stream me",
            "stream": True,
            "user_id": "test-user-5",
        },
    )

    # 1. Status & content-type
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]

    # 2. Parse events via httpx-sse
    with httpx_sse.SSE(response):
        events = list(response.aiter_sse())
    assert len(events) >= 1

    # 3. First event must carry a JSON data payload
    first = events[0]
    assert hasattr(first, "data") or hasattr(first, "dict")

    # httpx_sse provides the payload via the ``data`` attribute
    payload = getattr(first, "data", None) or (
        first.dict()["data"] if hasattr(first, "dict") else None
    )
    assert payload is not None
    assert isinstance(payload, str)


# ── POST /api/v1/chat/ – invalid request body ─

@pytest.mark.asyncio
async def test_invalid_request_body(async_client: httpx.AsyncClient) -> None:
    """Missing required field ``message`` yields 422, even with user_id."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={"user_id": "test-user-6"},
    )

    assert response.status_code == 422


# ──────────── ──────────── ──────────── ────────────
# Tenant-specific tests
# ──────────── ──────────── ──────────── ────────────

@pytest.mark.asyncio
async def test_chat_with_user_id(async_client: httpx.AsyncClient) -> None:
    """Chat request includes user_id in the response, confirming tenant tracking."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={
            "message": "Hello tenant",
            "user_id": "tenant-user-999",
            "thread_id": "tenant-thread-999",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body.get("user_id") == "tenant-user-999"
    assert body.get("thread_id") == "tenant-thread-999"
    assert len(body.get("message", "")) > 0


@pytest.mark.asyncio
async def test_chat_history_with_user_id(async_client: httpx.AsyncClient) -> None:
    """Chat history response includes user_id, confirming the thread is tied to the tenant."""

    # 1. Post a message with explicit user_id
    post_resp = await async_client.post(
        "/api/v1/chat/",
        json={
            "message": "Check user_id in history",
            "user_id": "history-tenant-user",
            "thread_id": "history-thread-123",
        },
    )
    assert post_resp.status_code == 200
    body = post_resp.json()
    assert body.get("user_id") == "history-tenant-user"

    # 2. Fetch the history with the same user_id
    hist_resp = await async_client.get(
        "/api/v1/chat/history-thread-123",
        headers={"X-User-ID": "history-tenant-user"},
    )
    assert hist_resp.status_code == 200
    hist_body = hist_resp.json()
    assert hist_body.get("user_id") == "history-tenant-user"
    assert hist_body["messages"] is not None
    # Should contain at least the user message we sent
    assert hist_body["message_count"] >= 1
