from datetime import datetime, timezone

from pydantic import BaseModel, Field, ConfigDict


class ChatRequest(BaseModel):
    """Schema for the POST /chat request body."""

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
        description="The chat agent's reply content",
        examples=["What is Python?"],
    )
    thread_id: str | None = Field(
        default=None,
        description="optional thread ID for conversation continuity",
        examples=["conv-abc123"],
    )
    stream: bool = Field(
        default=False,
        description="whether to enable SSE streaming",
        examples=[False],
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
