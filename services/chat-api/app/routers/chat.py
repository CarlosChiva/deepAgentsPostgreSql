"""Chat router with strong typing, Annotated parameters, and dependency aliases.

All path/query parameters use ``Annotated`` declarations and the router
leverages the dependency aliases from ``app.core.dependencies`` for
reusable, testable injection of validated values.
"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Path
from sse_starlette import EventSourceResponse

from app.core.config import settings
from app.core.dependencies import ValidatedUserIdDep
from app.models.request import ChatRequest
from app.models.response import ChatHistoryResponse, ChatResponse
from app.services.chat_service import get_history, send_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/",
    response_model=ChatResponse,
    summary="Send a message to the chat",
    description=(
        "Submit a message to the chatbot. Optionally enable real-time "
        "streaming by setting stream=True. Multi-tenancy: user_id is "
        "extracted from the request's JSON body."
    ),
)
async def post_chat(
    body: ChatRequest,
) -> ChatResponse | EventSourceResponse:
    """Accept a user message and return the agent's response (or SSE stream).

    The user_id is taken from the request body, validated by the
    Pydantic model, then forwarded to the chat service.
    """
    user_id = body.user_id

    thread_id = body.thread_id or str(uuid4())
    effective_stream = body.stream if body.stream is not None else False

    try:
        if effective_stream:
            return await send_message(
                user_id=user_id,
                message=body.message,
                thread_id=thread_id,
                stream=True,
            )

        response = await send_message(
            user_id=user_id,
            message=body.message,
            thread_id=thread_id,
            stream=False,
        )
        response.user_id = user_id
        return response
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        err = str(e)
        raise HTTPException(
            status_code=500,
            detail=f"Internal service error: {err!r}",
        ) from None


@router.get(
    "/{user_id}/{thread_id}",
    response_model=ChatHistoryResponse,
    summary="Get chat history for a thread",
    description=(
        "Retrieve the full conversation history for the given thread_id. "
        "Multi-tenancy: user_id is extracted from the request's "
        "middleware-injected state (X-User-ID header > query param "
        "> cookie, or body if provided)."
    ),
)
async def get_chat_history(
    user_id: Annotated[str, Path(description="The user ID for the tenant")],
    thread_id: Annotated[str, Path(description="The thread identifier")],
    validated_user_id: ValidatedUserIdDep,
) -> ChatHistoryResponse:
    """Return the message history for a specific conversation thread.

    user_id is taken from the URL path; the ``ValidatedUserIdDep``
    dependency validates the identifier from headers / query / cookie
    before the handler body runs. The validated_user_id is then compared
    against the path user_id to prevent cross-user access.
    """
    if not user_id and settings.TENANT_ENFORCE_USER_ID:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid user_id",
        )

    if user_id != validated_user_id:
        raise HTTPException(
            status_code=403,
            detail="Access denied",
        )

    try:
        history = await get_history(user_id=user_id, thread_id=thread_id)
        history.user_id = user_id
        return history
    except HTTPException as e:
        if e.status_code == 400:
            detail = str(e.detail)
            raise HTTPException(
                status_code=404,
                detail=detail,
            ) from None
        raise
    except Exception as e:  # noqa: BLE001
        err = str(e)
        raise HTTPException(
            status_code=500,
            detail=f"Internal service error: {err!r}",
        ) from None
