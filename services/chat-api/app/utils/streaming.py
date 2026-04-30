"""SSE streaming utilities for chat-agent responses.

This module extracts Server-Sent Event (SSE) streaming helpers out of
`chat_service.py` so that the streaming logic is reusable, testable, and
easy to reason about on its own.

Public API
----------
* ``sse_format_event`` — format a data payload as an SSE-ready dict.
* ``sse_generator`` — async generator that yields SSE events from an agent's
  ``astream`` output.
* ``stream_messages`` — high-level wrapper with retry logic around
  ``sse_generator``.
* ``sse_error_handler`` — default ``event_error_handler`` callback (logs errors).

Private helpers
---------------
* ``_get_message_content`` — extract text content from a single message.
* ``_find_assistant_content`` — locate the last assistant / AI message.
* ``_extract_reply`` — normalise a raw agent result to plain ``str | None``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SSE event formatting
# ---------------------------------------------------------------------------


def sse_format_event(
    data: str | dict | list | bytes,
    event: str | None = None,
) -> dict[str, str]:
    """Format *data* as an SSE-ready dictionary.

    Returns a ``dict`` compatible with
    ``sse_starlette.EventSourceResponse`` expectations
    (``{"data": ..., "event": ...}``).

    Parameters
    ----------
    data:
        The payload — a string, dict, list, or bytes.
        Dicts and lists are automatically JSON-encoded.
    event:
        Optional SSE event name (e.g. ``"message"``, ``"done"``).

    Returns
    -------
    dict[str, str]
        ``{"event": "<event>", "data": "<str>"}`` when *event* is given,
        otherwise ``{"data": "<str>"}``.
    """
    if isinstance(data, (dict, list)):
        data = json.dumps(data)
    elif isinstance(data, bytes):
        data = data.decode("utf-8")
    else:
        data = str(data)

    result: dict[str, str] = {"data": data}
    if event is not None:
        result["event"] = event
    return result


# ---------------------------------------------------------------------------
# SSE event generator
# ---------------------------------------------------------------------------


async def sse_generator(
    agent: Any,
    user_id: str,
    *,
    messages: list[dict[str, str]] | None = None,
    config: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """Async generator that yields SSE events from ``agent.astream``.

    Each yielded ``dict`` is consumed by ``sse_starlette.EventSourceResponse``
    and has the shape ``{"event": "message", "data": <json_str>}``.

    A final ``{"done": True}`` event is always emitted once the stream ends.

    Parameters
    ----------
    agent:
        The DeepAgent instance with an ``astream`` method.
    user_id:
        The current user's identifier — included in every ``data`` payload.
    messages:
        The list of input messages to pass to the agent. Defaults to ``[]``.
    config:
        The LangGraph ``config`` dict (thread_id, etc.). Defaults to ``{}``.

    Yields
    ------
    dict[str, str]
        SSE event dictionaries via :func:`sse_format_event`.
    """
    if messages is None:
        messages = []
    if config is None:
        config = {}

    async for stream_data in agent.astream(
        {"messages": messages},
        config=config,
        stream_mode="updates",
    ):
        if isinstance(stream_data, dict):
            for msg in stream_data.get("messages", []):
                content = _get_message_content(msg)
                if content is not None:
                    data = json.dumps({"chunk": content, "user_id": user_id})
                    yield sse_format_event(data, event="message")

    data = json.dumps({"done": True, "user_id": user_id})
    yield sse_format_event(data, event="message")


def _get_message_content(msg: Any) -> str | None:
    """Extract the text content from a single message (dict or object).

    Parameters
    ----------
    msg:
        A LangChain / LangGraph message — either a ``dict`` or an object
        with a ``.content`` attribute.

    Returns
    -------
    str | None
        The message content as a string, or ``None`` if not present.
    """
    if isinstance(msg, dict) and isinstance(msg.get("content"), str):
        return msg["content"]
    if hasattr(msg, "content") and isinstance(msg.content, str):
        return msg.content
    return None


# ---------------------------------------------------------------------------
# High-level streaming with retry
# ---------------------------------------------------------------------------


async def stream_messages(
    agent: Any,
    user_id: str,
    max_retries: int = 3,
    *,
    messages: list[dict[str, str]] | None = None,
    config: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, str]]:
    """High-level wrapper with retry logic around :func:`sse_generator`.

    Re-attempts the ``astream`` call up to *max_retries* times on failure
    before emitting an error event.

    Parameters
    ----------
    agent:
        The DeepAgent instance.
    user_id:
        The current user's identifier.
    max_retries:
        Maximum number of retries after the first attempt. Defaults to 3.
    messages:
        Input messages forwarded to :func:`sse_generator`.
    config:
        LangGraph config forwarded to :func:`sse_generator`.

    Yields
    ------
    dict[str, str]
        SSE event dictionaries. On exhaustion, yields a single error event.
    """
    if messages is None:
        messages = []
    if config is None:
        config = {}

    attempt = 0
    last_error: Exception | None = None

    while attempt <= max_retries:
        try:
            async for event in sse_generator(
                agent, user_id, messages=messages, config=config
            ):
                yield event
            return  # success — exit early
        except Exception as exc:
            last_error = exc
            attempt += 1
            if attempt > max_retries:
                break
            logger.warning("Streaming attempt %d failed: %s", attempt, exc)

    # All retries exhausted — emit a single error event
    data = json.dumps({"error": str(last_error), "user_id": user_id})
    yield sse_format_event(data, event="message")


# ---------------------------------------------------------------------------
# SSE error handler
# ---------------------------------------------------------------------------


def sse_error_handler(exc: Exception) -> None:
    """Default error handler for ``EventSourceResponse``.

    Logs the exception at ``ERROR`` level and silently returns so that the
    SSE connection is closed gracefully.

    Parameters
    ----------
    exc:
        The exception raised by the SSE event generator.
    """
    logger.error("SSE streaming error: %s", exc)


# ---------------------------------------------------------------------------
# Reply extraction
# ---------------------------------------------------------------------------


def _extract_reply(result: Any) -> str | None:
    """Normalise a raw agent invocation result to a plain-text string.

    Handles the following structures:

    * ``str`` — returned directly (stripped).
    * ``dict`` with a ``"messages"`` key — extracts the last assistant / AI
      message content.
    * Any object with a ``.messages`` attribute — same logic via ``getattr``.

    Parameters
    ----------
    result:
        The raw return value from ``agent.ainvoke`` or similar.

    Returns
    -------
    str | None
        The extracted assistant reply, or ``None`` if no content is found.
    """
    if isinstance(result, str):
        return result.strip() or None

    if isinstance(result, dict):
        messages = result.get("messages", [])
        return _find_assistant_content(messages)

    # Object with .messages attribute (e.g. LangChain State-like objects)
    messages = getattr(result, "messages", None)
    if isinstance(messages, (list, tuple)):
        return _find_assistant_content(messages)

    return str(result).strip() or None


def _find_assistant_content(messages: list[Any]) -> str | None:
    """Return the content of the last assistant / AI message in the list.

    Parameters
    ----------
    messages:
        A list of message dicts or message objects.

    Returns
    -------
    str | None
        The assistant content, or ``None`` if not found.
    """
    for msg in reversed(messages):
        if isinstance(msg, dict):
            role = msg.get("role") or msg.get("type", "")
            if role in ("assistant", "ai") and msg.get("content"):
                return str(msg["content"]).strip() or None
        else:
            msg_type = getattr(msg, "type", None)
            content = getattr(msg, "content", None)
            if msg_type == "ai" and content:
                return str(content).strip() or None

    return None
