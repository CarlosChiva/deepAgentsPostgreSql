"""
Utility modules for the chat-api application.

This package contains helper utilities that don't fit into other
domain-specific modules (agents, db, middleware, etc.).
"""

__all__ = [
    # streaming utilities (Task #13)
    "sse_format_event",
    "sse_generator",
    "stream_messages",
    "sse_error_handler",
    "_extract_reply",
]

from app.utils.streaming import (
    _extract_reply,
    sse_error_handler,
    sse_format_event,
    sse_generator,
    stream_messages,
)
