"""Pydantic response schemas for the Chat API."""

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class MessageItem(BaseModel):
    """Schema for an individual message in chat history."""

    role: str = Field(
        description="The sender role: agent, user, or system",
        examples=["user", "assistant", "system"],
    )
    content: str = Field(
        description="The message text content",
        examples=["Hello, how are you?"],
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the message was exchanged",
        examples=["2024-01-15T12:00:00Z"],
    )


class ChatResponse(BaseModel):
    """Schema for a standard (non-streamed) chat response."""

    user_id: str = Field(
        description="The user ID associated with this conversation",
        examples=["user-abc123"],
    )
    thread_id: str = Field(
        description="The thread this conversation is part of",
        examples=["conv-abc123"],
    )
    message: str = Field(
        description="The chat agent's reply content",
        examples=["Python is a popular programming language for web development."],
    )
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="When the response was generated",
        examples=["2024-01-15T12:00:00Z"],
    )


class ChatHistoryResponse(BaseModel):
    """Schema for the GET /chat/{thread_id} response body."""

    thread_id: str = Field(
        description="The thread identifier",
        examples=["conv-xyz789"],
    )
    user_id: str = Field(
        description="The user ID associated with this conversation history",
        examples=["user-abc123"],
    )
    messages: list[MessageItem] = Field(
        description="Ordered list of messages in the conversation",
        examples=[{"role": "user", "content": "Hello", "timestamp": "2024-01-15T12:00:00Z"}],
    )
    message_count: int = Field(
        description="Convenience field equal to the number of messages",
        examples=[1],
    )


__all__ = ["ChatResponse", "MessageItem", "ChatHistoryResponse"]
