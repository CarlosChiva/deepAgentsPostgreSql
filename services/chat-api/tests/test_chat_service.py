"""Unit tests for the service layer (app/services/chat_service.py).

Tests ``send_message``, ``get_history``, and response structures using
``unittest.mock.patch`` and ``unittest.mock.MagicMock``.  No real database
or LLM calls are made during these tests.
"""

from __future__ import annotations

import json
import sys
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sse_starlette import EventSourceResponse
from app.models.chat import ChatResponse


# ===== Helpers =================================================================

def _reset_state():
    """Reset all module-level singletons used by agent / database."""
    from app import agent, database  # noqa: E402
    agent.reset_agent()
    database._checkpointer = None
    database._checkpointer_initialized = False
    database._store = None
    database._store_initialized = False


def _patch_agent_and_db(mock_agent=None, mock_checkpointer=None):
    """Return a context-manager patching get_agent and get_checkpointer.

    Returns (mock_agent, mock_checkpointer) for assertion after the ``with`` block.
    """
    from app import agent as agent_mod, database as db_mod  # noqa: E402

    if mock_agent is None:
        mock_agent = MagicMock()
        mock_agent.ainvoke = AsyncMock(
            return_value={"messages": [{"role": "assistant", "content": "Test reply"}]}
        )
    if mock_checkpointer is None:
        mock_checkpointer = MagicMock()

    return (
        patch.object(agent_mod, "get_agent", return_value=mock_agent),
        patch.object(db_mod, "get_checkpointer", return_value=mock_checkpointer),
    ), mock_agent, mock_checkpointer


# ===== send_message tests ======================================================


@pytest.mark.asyncio
async def test_send_message_calls_agent_invoke_correctly():
    """``send_message`` calls agent.ainvoke with the correct input dict and config."""
    _reset_state()

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={"messages": [{"role": "assistant", "content": "Hello World"}]}
    )
    mock_checkpointer = MagicMock()

    with patch.object(__import__("app.agent").agent, "get_agent", return_value=mock_agent), \
         patch.object(__import__("app.database").database, "get_checkpointer", return_value=mock_checkpointer):

        from app.services.chat_service import send_message  # noqa: E402
        result = await send_message(message="Hello", thread_id="test-thread")

    # Verify agent.ainvoke was called correctly
    mock_agent.ainvoke.assert_called_once()
    call_args = mock_agent.ainvoke.call_args
    input_dict = call_args[0][0]
    assert input_dict == {"messages": [{"role": "user", "content": "Hello"}]}

    config = call_args[1]["config"]
    assert config["configurable"]["thread_id"] == "test-thread"

    mock_checkpointer.setup.assert_called()

    # Result is a ChatResponse
    assert isinstance(result, ChatResponse)
    assert result.message == "Hello World"
    assert result.thread_id == "test-thread"


@pytest.mark.asyncio
async def test_send_message_returns_streaming_response_when_requested():
    """When stream=True, send_message returns an EventSourceResponse with SSE headers."""
    _reset_state()

    from app import agent as agent_mod, database as db_mod  # noqa: E402

    mock_agent = MagicMock()

    async def _fake_stream(*args, **kwargs):
        yield {"messages": [{"role": "assistant", "content": "chunk1"}]}
        yield {"messages": [{"role": "assistant", "content": "chunk2"}]}

    mock_agent.astream = _fake_stream

    mock_checkpointer = MagicMock()

    with patch.object(agent_mod, "get_agent", return_value=mock_agent), \
         patch.object(db_mod, "get_checkpointer", return_value=mock_checkpointer):

        from app.services.chat_service import send_message  # noqa: E402
        result = await send_message(message="stream me", thread_id="sse-thread", stream=True)

    assert isinstance(result, EventSourceResponse)


@pytest.mark.asyncio
async def test_send_message_handles_agent_errors():
    """When agent.ainvoke raises, the service wraps it as HTTPException(500)."""
    _reset_state()

    from fastapi import HTTPException  # noqa: E402
    from app import agent as agent_mod, database as db_mod  # noqa: E402

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(side_effect=Exception("LLM error"))
    mock_checkpointer = MagicMock()

    with patch.object(agent_mod, "get_agent", return_value=mock_agent), \
         patch.object(db_mod, "get_checkpointer", return_value=mock_checkpointer):

        from app.services.chat_service import send_message  # noqa: E402

        with pytest.raises(HTTPException) as exc_info:
            await send_message(message="Hello", thread_id="test-thread")

    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_send_message_validates_empty_message():
    """Empty or whitespace-only message raises HTTPException(400)."""
    from fastapi import HTTPException  # noqa: E402
    from app.services.chat_service import send_message  # noqa: E402

    with pytest.raises(HTTPException) as exc_info:
        await send_message(message="  ", thread_id="test-thread")

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_send_message_validates_empty_thread_id():
    """Empty thread_id raises HTTPException(400)."""
    from fastapi import HTTPException  # noqa: E402
    from app.services.chat_service import send_message  # noqa: E402

    with pytest.raises(HTTPException) as exc_info:
        await send_message(message="Hello", thread_id="")

    assert exc_info.value.status_code == 400


# ===== get_history tests =======================================================


@pytest.mark.asyncio
async def test_get_history_returns_messages_from_store():
    """get_history returns messages reconstructed from checkpoint state history."""
    _reset_state()

    from app import database as db_mod  # noqa: E402

    # Build mock checkpoint states
    state1 = MagicMock()
    state1.values = {
        "messages": [
            {"role": "user", "content": "Hello"},
        ]
    }
    state1.metadata = {"updated_at": datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)}

    state2 = MagicMock()
    state2.values = {
        "messages": [
            {"role": "assistant", "content": "Hi there"},
        ]
    }
    state2.metadata = {"updated_at": datetime(2024, 1, 15, 12, 1, 0, tzinfo=timezone.utc)}

    mock_checkpointer = MagicMock()
    mock_checkpointer.get_state_history = MagicMock(return_value=[state1, state2])

    with patch.object(db_mod, "get_checkpointer", return_value=mock_checkpointer):

        from app.services.chat_service import get_history  # noqa: E402
        result = await get_history(thread_id="test-thread")

    assert result.thread_id == "test-thread"
    assert result.message_count == 2
    assert result.messages[0].role == "user"
    assert result.messages[0].content == "Hello"
    assert result.messages[1].role == "assistant"
    assert result.messages[1].content == "Hi there"


@pytest.mark.asyncio
async def test_get_history_returns_empty_for_new_thread():
    """A brand-new thread_id yields an empty message list (no error)."""
    _reset_state()

    from app import database as db_mod  # noqa: E402

    mock_checkpointer = MagicMock()
    mock_checkpointer.get_state_history = MagicMock(return_value=[])

    with patch.object(db_mod, "get_checkpointer", return_value=mock_checkpointer):

        from app.services.chat_service import get_history  # noqa: E402
        result = await get_history(thread_id="nonexistent-thread")

    assert result.thread_id == "nonexistent-thread"
    assert result.messages == []
    assert result.message_count == 0


# ===== ChatResponse structure test =============================================


@pytest.mark.asyncio
async def test_chat_response_has_expected_structure():
    """The returned ChatResponse has all expected fields with correct types."""
    _reset_state()

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={"messages": [{"role": "assistant", "content": "Hi"}]}
    )
    mock_checkpointer = MagicMock()

    with patch.object(__import__("app.agent").agent, "get_agent", return_value=mock_agent), \
         patch.object(__import__("app.database").database, "get_checkpointer", return_value=mock_checkpointer):

        from app.services.chat_service import send_message  # noqa: E402
        result = await send_message(message="Hello", thread_id="test-thread")

    assert isinstance(result, ChatResponse)
    assert result.thread_id == "test-thread"
    assert result.message == "Hi"
    assert isinstance(result.timestamp, datetime)
    assert result.timestamp.tzinfo is not None


# ===== _extract_reply helper tests =============================================


def test_extract_reply_from_dict():
    """_extract_reply extracts assistant content from a dict result."""
    from app.services.chat_service import _extract_reply  # noqa: E402

    result = _extract_reply(
        {"messages": [{"role": "user", "content": "Hi"}, {"role": "assistant", "content": "Hello"}]}
    )
    assert result == "Hello"


def test_extract_reply_from_string():
    """_extract_reply returns stripped string when result is already a string."""
    from app.services.chat_service import _extract_reply  # noqa: E402

    assert _extract_reply("  Hello  ") == "Hello"


def test_extract_reply_from_dict_with_object_msgs():
    """_extract_reply handles message objects with role/content attributes."""
    from app.services.chat_service import _extract_reply  # noqa: E402

    mock_ai = MagicMock()
    mock_ai.role = "assistant"
    mock_ai.content = "Object reply"

    result = _extract_reply({"messages": [{"role": "user", "content": "Hi"}, mock_ai]})
    assert result == "Object reply"


@pytest.mark.asyncio
async def test_send_message_handles_db_unavailable():
    """When get_checkpointer raises, send_message returns HTTPException(503)."""
    from fastapi import HTTPException  # noqa: E402
    from app import agent as agent_mod, database as db_mod  # noqa: E402

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={"messages": [{"role": "assistant", "content": "OK"}]}
    )

    with patch.object(agent_mod, "get_agent", return_value=mock_agent), \
         patch.object(db_mod, "get_checkpointer", side_effect=OSError("connection refused")):

        from app.services.chat_service import send_message  # noqa: E402

        with pytest.raises(HTTPException) as exc_info:
            await send_message(message="Hello", thread_id="test-thread")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_send_message_handles_checkpointer_setup_error():
    """If checkpointer.setup() fails during send_message, it is re-raised."""
    from app import agent as agent_mod, database as db_mod  # noqa: E402

    mock_agent = MagicMock()
    mock_agent.ainvoke = AsyncMock(
        return_value={"messages": [{"role": "assistant", "content": "OK"}]}
    )

    mock_cp = MagicMock()
    mock_cp.setup = MagicMock(side_effect=OSError("table not found"))

    with patch.object(agent_mod, "get_agent", return_value=mock_agent), \
         patch.object(db_mod, "get_checkpointer", return_value=mock_cp):

        from app.services.chat_service import send_message  # noqa: E402

        # setup() raises, which bubbles up through send_message
        with pytest.raises(OSError):
            await send_message(message="Hello", thread_id="test-thread")

    mock_cp.setup.assert_called()


# ===== SSE event content test ==================================================


@pytest.mark.asyncio
async def test_send_message_streaming_yields_sse_events():
    """Streaming response contains at least one 'message' event with chunk data."""
    _reset_state()

    from app import agent as agent_mod, database as db_mod  # noqa: E402

    async def _fake_stream(*args, **kwargs):
        yield {"messages": [{"role": "assistant", "content": "chunk1"}]}
        yield {"messages": [{"role": "assistant", "content": "chunk2"}]}

    mock_agent = MagicMock()
    mock_agent.astream = _fake_stream
    mock_checkpointer = MagicMock()

    with patch.object(agent_mod, "get_agent", return_value=mock_agent), \
         patch.object(db_mod, "get_checkpointer", return_value=mock_checkpointer):

        from app.services.chat_service import send_message  # noqa: E402

        result = await send_message(message="Hi", thread_id="test-thread", stream=True)

    assert isinstance(result, EventSourceResponse)

    # Consume the stream and verify events
    events = []
    async for event in result.body_iterator:
        events.append(event)

    # Should have messages + the done event
    assert len(events) >= 1
