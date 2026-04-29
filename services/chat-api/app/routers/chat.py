from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request

from app.config import settings
from app.models.chat import ChatRequest
from app.models.history import ChatHistoryResponse
from app.services.chat_service import get_history, send_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/",
    summary="Send a message to the chat",
    description=(
        "Submit a message to the chatbot. Optionally enable real-time "
        "streaming by setting stream=True. Multi-tenancy: user_id is "
        "extracted from the request's JSON body."
    ),
)
async def post_chat(
    body: ChatRequest
):
    """Accept a user message and return the agent's response (or SSE stream).

    user_id is extracted from the parsed request body.
    """

    # Extract user_id from the parsed request body.
    # The Pydantic model (ChatRequest) already enforces min_length=1
    # and pattern validation, so we don't need an additional enforcement check.
    user_id = body.user_id

    # Validate format if a user_id is present
    # if user_id:
    #     try:
    #         user_id = validate_user_id(user_id)
    #     except HTTPException as e:
    #         raise HTTPException(
    #             status_code=e.status_code, detail=e.detail
    #         )

    thread_id = body.thread_id or str(uuid4())
    effective_stream = body.stream if body.stream is not None else False

    try:
        if effective_stream:
            result = await send_message(
                user_id=user_id,
                message=body.message,
                thread_id=thread_id,
                stream=True,
            )
            return result
        else:
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
    except Exception as e:
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
    user_id:str,
    thread_id: str,

):
    """Return the message history for a specific conversation thread.

    user_id is extracted from the middleware-injected request state,
    which may come from headers or from the request body if provided.
    """

    # GET endpoint: user_id may come from headers/query params
    # injected by the middleware into scope["state"], or from the
    # request body if provided via X-User-ID header.
    
    # Enforce presence when TENANT_ENFORCE_USER_ID is on
    if not user_id and settings.TENANT_ENFORCE_USER_ID:
        raise HTTPException(
            status_code=401,
            detail="Missing or invalid user_id",
        )

    # Validate format if a user_id is present
    # if user_id:
    #     try:
    #         user_id = validate_user_id(user_id)
    #     except HTTPException as e:
    #         raise HTTPException(
    #             status_code=e.status_code,
    #             detail=e.detail,
    #         )

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
    except Exception as e:
        err = str(e)
        raise HTTPException(
            status_code=500,
            detail=f"Internal service error: {err!r}",
        ) from None
