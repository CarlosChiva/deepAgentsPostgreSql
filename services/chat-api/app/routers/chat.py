from uuid import uuid4

from fastapi import APIRouter, HTTPException
from sse_starlette import EventSourceResponse

from app.models.chat import ChatRequest, ChatResponse
from app.models.history import ChatHistoryResponse
from app.services.chat_service import get_history, send_message

router = APIRouter(prefix="/chat", tags=["chat"])


@router.post(
    "/",
    response_model=ChatResponse,
    summary="Send a message to the chat",
    description="Submit a message to the chatbot. Optionally enable real-time streaming by setting stream=True.",
)
async def post_chat(
    body: ChatRequest,
    stream: bool = False,
):
    """Accept a user message and return the agent's response (or SSE stream)."""

    thread_id = body.thread_id or str(uuid4())
    effective_stream = body.stream if body.stream is not None else stream

    try:
        if effective_stream:
            result = send_message(
                message=body.message,
                thread_id=thread_id,
                stream=True,
            )
            return result
        else:
            response = await send_message(
                message=body.message,
                thread_id=thread_id,
                stream=False,
            )
            return response
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal service error: {str(e)}")


@router.get(
    "/{thread_id}",
    response_model=ChatHistoryResponse,
    summary="Get chat history for a thread",
    description="Retrieve the full conversation history for the given thread_id.",
)
async def get_chat_history(thread_id: str):
    """Return the message history for a specific conversation thread."""

    try:
        history = await get_history(thread_id=thread_id)
        return history
    except HTTPException as e:
        if e.status_code == 400:
            raise HTTPException(status_code=404, detail=str(e.detail))
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal service error: {str(e)}")
