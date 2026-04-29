"""Service layer for chat functionality."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sse_starlette import EventSourceResponse

from app.agent import get_agent_for_user
from app.config import Settings
from app.models.chat import ChatResponse
from app.models.history import ChatHistoryResponse, MessageItem

logger = logging.getLogger(__name__)


async def send_message(
    user_id: str,
    message: str,
    thread_id: str,
    stream: bool = False,
    settings: Settings | None = None,
) -> ChatResponse | EventSourceResponse:
    """Send a user message to the chat agent and return its reply."""

    if not message or not message.strip():
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if not thread_id or not thread_id.strip():
        raise HTTPException(status_code=400, detail="Thread ID is required")

    if not user_id and settings is not None and settings.TENANT_ENFORCE_USER_ID:
        raise HTTPException(status_code=400, detail="user_id is required")

    try:
        agent = await get_agent_for_user(user_id)
    except Exception as e:
        logger.error("Failed to obtain agent: %s", e)
        raise HTTPException(status_code=503, detail="Service unavailable") from e

    config = {"configurable": {"thread_id": thread_id}}

    if stream:
        uid = user_id

        async def _event_generator():
            async for _stream_data in agent.astream(
                {"messages": [{"role": "user", "content": message}]},
                config=config,
                stream_mode="updates",
            ):
                if isinstance(_stream_data, dict):
                    msgs = _stream_data.get("messages", [])
                    for _msg in msgs:
                        if isinstance(_msg, dict) and isinstance(_msg.get("content"), str):
                            data = json.dumps({"chunk": _msg["content"], "user_id": uid})
                            yield {"event": "message", "data": data}
                        elif hasattr(_msg, "content") and isinstance(_msg.content, str):
                            data = json.dumps({"chunk": _msg.content, "user_id": uid})
                            yield {"event": "message", "data": data}
            data = json.dumps({"done": True, "user_id": uid})
            yield {"event": "message", "data": data}

        def _error_handler(exc):
            logger.error("SSE streaming error: %s", exc)

        return EventSourceResponse(_event_generator(), event_error_handler=_error_handler)

    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": message}]},
            config=config,
        )
    except Exception as e:
        logger.error("Agent invocation failed: %s", e)
        raise HTTPException(status_code=500, detail="Agent processing failed") from e

    reply = _extract_reply(result)
    if not reply:
        reply = "(empty response)"

    return ChatResponse(
        user_id=user_id,
        thread_id=thread_id,
        message=reply,
        timestamp=datetime.now(UTC),
    )


def _extract_reply(result: Any) -> str | None:
    """Normalise an agent invocation result to plain-text."""
    if isinstance(result, str):
        return result.strip() or None

    if isinstance(result, dict):
        messages = result.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, dict):
                # LangChain usa "role": "assistant" en dicts
                role = msg.get("role") or msg.get("type", "")
                if role in ("assistant", "ai") and msg.get("content"):
                    return str(msg["content"]).strip() or None
            else:
                # Objetos LangChain (AIMessage) usan .type == "ai", no .role
                msg_type = getattr(msg, "type", None)
                content = getattr(msg, "content", None)
                if msg_type == "ai" and content:
                    return str(content).strip() or None

    return str(result).strip() or None


async def get_history(
    user_id: str,
    thread_id: str,
) -> ChatHistoryResponse:
    """Return the full message history for *thread_id*."""

    if not user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    if not thread_id or not thread_id.strip():
        raise HTTPException(status_code=400, detail="Thread ID is required")

    try:
        agent = await get_agent_for_user(user_id)
    except Exception as e:
        logger.error("Failed to obtain agent: %s", e)
        raise HTTPException(status_code=503, detail="Service unavailable") from e

    # The agent encapsulates the tenant-specific checkpointer
    checkpointer = agent.checkpointer

    config = {"configurable": {"thread_id": thread_id}}

    try:
        # Aget devuelve el checkpoint más reciente (es async)
        # No usar get_state_history() — no existe en AsyncPostgresSaver
        checkpoint = await checkpointer.aget(config)
    except Exception as e:
        logger.error("aget failed for thread_id=%s: %s", thread_id, e)
        raise HTTPException(status_code=503, detail="Service unavailable") from e

    # Thread sin historial — respuesta vacía válida, no es error
    if checkpoint is None:
        return ChatHistoryResponse(
            thread_id=thread_id,
            user_id=user_id,
            messages=[],
            message_count=0,
        )

    # Los mensajes viven en channel_values["messages"]
    raw_messages: list = checkpoint.get("channel_values", {}).get("messages", [])

    message_items: list[MessageItem] = []
    for msg in raw_messages:
        if hasattr(msg, "type") and hasattr(msg, "content"):
            # Objeto LangChain: HumanMessage (.type="human"), AIMessage (.type="ai")
            role = _normalize_role(msg.type)
            content = str(msg.content)
        elif isinstance(msg, dict):
            raw_role = msg.get("type") or msg.get("role", "unknown")
            role = _normalize_role(raw_role)
            content = str(msg.get("content", ""))
        else:
            continue

        # Filtrar mensajes de herramientas internos si no se quieren exponer
        if not content:
            continue

        message_items.append(
            MessageItem(
                role=role,
                content=content,
                timestamp=datetime.now(UTC),
            )
        )

    return ChatHistoryResponse(
        thread_id=thread_id,
        user_id=user_id,
        messages=message_items,
        message_count=len(message_items),
    )


def _normalize_role(msg_type: str) -> str:
    """Convierte tipos internos de LangChain a roles legibles por la API."""
    return {
        "human": "user",
        "ai": "assistant",
        "tool": "tool",
        "system": "system",
        "function": "tool",
    }.get(msg_type, msg_type)
