"""Pydantic request schemas for the Chat API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """Request payload for sending a chat message.

    Fields:
        user_id: Unique tenant identifier used to isolate agent state.
        message:  The user's free-text message sent to the chat agent.
        thread_id: Optional conversation thread identifier for continuity.
        stream:  Whether to return the response via Server-Sent Events.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: str = Field(
        min_length=1,
        max_length=53,
        pattern=r"^[a-zA-Z0-9_-]+$",
        description="Unique identifier for the user making this request (tenant identifier)",
        examples=["user-abc123", "user_xyz"],
    )
    message: str = Field(
        min_length=1,
        max_length=10000,
        description="The user's message text",
        examples=["What is Python?"],
    )
    thread_id: str | None = Field(
        None,
        max_length=255,
        description="Optional thread identifier for conversation continuity",
        examples=["conv-abc123"],
    )
    stream: bool = Field(
        False,
        description="Whether to enable SSE streaming for the response",
        examples=[False],
    )


__all__ = ["ChatRequest"]
