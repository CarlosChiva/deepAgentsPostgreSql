"""Tests for the ``/chat`` endpoints (T013).

Tests the full HTTP layer against the FastAPI app using the ``async_client``
fixture from ``tests/conftest.py``, which wires up an ``httpx.AsyncClient``
with a test checkpointer, test store, and a mock ``DeepAgent`` (avoids real
LLM calls).

Endpoints under test::

    POST   /api/v1/chat/          – send a message (text or SSE)
    GET    /api/v1/chat/<id>      – retrieve conversation history
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import httpx_sse

# ── POST /api/v1/chat/ – happy path (no explicit thread_id) ────────────

@pytest.mark.asyncio
async def test_chat_post_returns_response(async_client: httpx.AsyncClient) -> None:
    """"POST /api/v1/chat/ returns a valid ChatResponse (200)."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={"message": "Hello"},
    )

    assert response.status_code == 200

    body = response.json()

    assert body.get("thread_id") is not None
    assert isinstance(body["message"], str)
    assert len(body["message"]) > 0

    # timestamp must be present and parseable as datetime
    ts_raw = body.get("timestamp")
    assert ts_raw is not None
    ts = datetime.fromisoformat(ts_raw)
    assert ts.tzinfo is not None


# ── POST /api/v1/chat/ – explicit thread_id ─────────────────────────────

@pytest.mark.asyncio
async def test_chat_post_with_custom_thread_id(async_client: httpx.AsyncClient) -> None:
    """POST with a user-supplied thread_id echoes it back (200)."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={
            "message": "Hi",
            "thread_id": "custom-thread-42",
        },
    )

    assert response.status_code == 200

    body = response.json()
    assert body["thread_id"] == "custom-thread-42"
    assert isinstance(body["message"], str)


# ── GET /api/v1/chat/<thread_id> – history after messages ──────────────

@pytest.mark.asyncio
async def test_chat_history_returns_messages(async_client: httpx.AsyncClient) -> None:
    """Sending a message then fetching history returns those messages."""

    # 1. Post a message (uses test checkpointer)
    post_resp = await async_client.post(
        "/api/v1/chat/",
        json={"message": "What is AI?"},
    )
    assert post_resp.status_code == 200
    thread_id = post_resp.json()["thread_id"]

    # 2. Fetch history for that thread
    hist_resp = await async_client.get(f"/api/v1/chat/{thread_id}")
    assert hist_resp.status_code == 200

    body = hist_resp.json()
    assert body["thread_id"] == thread_id
    assert body["message_count"] >= 1

    # 3. The stored message roles should include user and assistant
    roles = [msg["role"] for msg in body["messages"]]
    assert "user" in roles
    assert "assistant" in roles


# ── GET /api/v1/chat/<thread_id> – empty for new thread ─────────────────

@pytest.mark.asyncio
async def test_chat_history_empty_for_new_thread(async_client: httpx.AsyncClient) -> None:
    """A brand-new thread_id yields an empty message list (no error)."""

    response = await async_client.get("/api/v1/chat/brand-new-never-used")
    assert response.status_code == 200

    body = response.json()
    assert body["thread_id"] == "brand-new-never-used"
    assert body["messages"] == []
    assert body["message_count"] == 0


# ── POST /api/v1/chat/ (stream=true) – SSE headers + events ───────────

@pytest.mark.asyncio
async def test_chat_stream_returns_sse_events(async_client: httpx.AsyncClient) -> None:
    """Streaming endpoint returns SSE headers and at least one data event."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={
            "message": "stream me",
            "stream": True,
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


# ── POST /api/v1/chat/ – invalid request body ───────────────────────────

@pytest.mark.asyncio
async def test_invalid_request_body(async_client: httpx.AsyncClient) -> None:
    """Missing required field ``message`` yields 422."""

    response = await async_client.post(
        "/api/v1/chat/",
        json={},
    )

    assert response.status_code == 422
