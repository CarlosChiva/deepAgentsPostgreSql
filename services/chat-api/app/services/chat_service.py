"""Service layer for chat functionality.

This module implements the business logic for the chat system, decoupling
the API routes from the underlying DeepAgent and PostgresSaver persistence.
Routers should import and call :func:`send_message` and :func:`get_history`
for all chat operations.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sse_starlette import EventSourceResponse

from app.agent import get_agent
from app.database import get_checkpointer
from app.models.chat import ChatResponse
from app.models.history import ChatHistoryResponse, MessageItem

logger = logging.getLogger(__name__)


async def send_message(
    message: str,
    thread_id: str,
    stream: bool = False,
) -> ChatResponse | EventSourceResponse:
    """Send a user message to the chat agent and return its reply.

    When *stream* is ``False``, a :class:`ChatResponse` is returned with the
    full assistant message.  When *stream* is ``True``, a SSE-compatible
    :class:`EventSourceResponse` is returned that yields chunked events.

    Args:
        message: The user's message (must not be empty).
        thread_id: The conversation thread identifier (must not be empty).
        stream: Whether to return an SSE streaming response.

    Returns:
        A :class:`ChatResponse` (non-streaming) or
        :class:`EventSourceResponse` (streaming) with the agent's reply.

    Raises:
        HTTPException(400): *message* or *thread_id* is empty.
        HTTPException(500): Agent processing failure.
        HTTPException(503): Database unavailable.
    """

    # 1. Validate inputs ---------------------------------------------------
    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")

    if not thread_id or not thread_id.strip():
        raise HTTPException(status_code=400, detail="Thread ID is required")

    # 2. Get agent & checkpointer -----------------------------------------
    try:
        agent = get_agent()
        checkpointer = get_checkpointer()
        checkpointer.setup()
    except Exception as e:
        logger.error("Failed to obtain agent or checkpointer: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from e

    # 3. Build invocation config --------------------------------------------
    config = {"configurable": {"thread_id": thread_id}}

    # 4. Stream or invoke the agent -----------------------------------------
    if stream:
        # --- SSE streaming path ------------------------------------
        async def _event_generator():
            """Yield SSE events from the agent's chunked output."""
            async for _stream_data in agent.astream(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                stream_mode="updates",
            ):
                # Extract assistant text chunks from each stream item
                if isinstance(_stream_data, dict):
                    msgs = _stream_data.get("messages", [])
                    for _msg in msgs:
                        if isinstance(_msg, dict) and isinstance(_msg.get("content"), str):
                            yield {"event": "message", "data": json.dumps({"chunk": _msg["content"]})}
                        elif hasattr(_msg, "content") and isinstance(_msg.content, str):
                            yield {"event": "message", "data": json.dumps({"chunk": _msg.content})}
            yield {"event": "message", "data": json.dumps({"done": True})}

        def _error_handler(exc):
            """Log server-side errors without leaking internals to the client."""
            logger.error("SSE streaming error: %s", exc)

        return EventSourceResponse(
            _event_generator(),
            event_error_handler=_error_handler,
        )

    # Non-streaming (default) path
    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
        )
    except Exception as e:
        logger.error("Agent invocation failed: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Agent processing failed",
        ) from e

    # 5. Extract the assistant reply text -----------------------------------
    reply = _extract_reply(result)
    if not reply:
        reply = "(empty response)"

    # 6. Return -------------------------------------------------------------
    return ChatResponse(
        thread_id=thread_id,
        message=reply,
        timestamp=datetime.now(timezone.utc),
    )


def _extract_reply(result: Any) -> str | None:
    """Normalise an agent invocation result to plain-text.

    DeepAgent may return a ``dict``, an ``AgentInvocationResult``, or a bare
    string depending on its configuration.  This helper handles the most
    common shapes.
    """
    if isinstance(result, str):
        stripped = result.strip()
        return stripped or None

    if isinstance(result, dict):
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, dict):
                if msg.get("role") == "assistant" and msg.get("content"):
                    return str(msg["content"]).strip() or None
            else:
                role = getattr(msg, "role", None)
                content = getattr(msg, "content", None)
                if role == "assistant" and content:
                    return str(content).strip() or None

    # Last resort
    raw = str(result)
    return raw or None


async def get_history(
    thread_id: str,
) -> ChatHistoryResponse:
    """Return the full message history for *thread_id*.

    Args:
        thread_id: The conversation thread identifier (must not be empty).

    Returns:
        A :class:`ChatHistoryResponse` with the thread's message list.
        If the thread has no recorded history, returns an empty list (no error).

    Raises:
        HTTPException(400): *thread_id* is empty.
        HTTPException(503): Checkpointer unavailable.
    """

    # 1. Validate input -----------------------------------------------------
    if not thread_id or not thread_id.strip():
        raise HTTPException(status_code=400, detail="Thread ID is required")

    # 2. Get checkpointer ---------------------------------------------------
    try:
        checkpointer = get_checkpointer()
    except Exception as e:
        logger.error("Failed to obtain checkpointer: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Service unavailable",
        ) from e

    # 3. Read checkpoint states for the thread ------------------------------
    config = {"configurable": {"thread_id": thread_id}}

    try:
        states = list(checkpointer.get_state_history(config))
    except Exception as e:
        logger.error("Get state history failed: %s", e)
        raise HTTPException(
            status_code=503,
            detail="Service unavailable",
        ) from e

    # 4. Reconstruct the ordered list of messages ---------------------------
    all_messages: list[tuple[str, str, datetime]] = []

    for state in states:
        values = getattr(state, "values", {}) or {}
        messages = values.get("messages", [])
        metadata = getattr(state, "metadata", {}) or {}
        ts = metadata.get("updated_at") or datetime.now(timezone.utc)

        for msg in messages:
            if isinstance(msg, dict):
                role = msg.get("role", "unknown")
                content = str(msg.get("content", ""))
            else:
                role = getattr(msg, "role", "unknown")
                content = str(getattr(msg, "content", ""))

            if role and content:
                all_messages.append((role, content, ts))

    # 5. Return history (empty list is valid — no error for missing thread)
    message_items = [
        MessageItem(role=role, content=content, timestamp=ts)
        for role, content, ts in all_messages
    ]

    return ChatHistoryResponse(
        thread_id=thread_id,
        messages=message_items,
        message_count=len(message_items),
    )
