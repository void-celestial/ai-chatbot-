import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..db.connection import db_connection
from ..models.schemas import SendMessageIn
from ..services.chat_service import (
    get_conversation,
    insert_message,
    last_user_message,
    sse_event,
    stream_assistant_reply,
)
from ..services.memory_extraction import extract_memories_from_exchange
from ..services.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

router = APIRouter()


async def run_memory_extraction(
    settings, ollama, conversation_id: str, user_message_seq: int
) -> None:
    """One extraction job: run by the app's ExtractionScheduler as a
    background task after the response completes.

    Targets the exchange that starts at `user_message_seq` — the exchange
    whose turn this job was queued for — so a job that gets preempted by a
    newer message and re-queued still extracts its own exchange rather than
    whatever happens to be newest when it finally runs.

    Opens its own database connection and contains its own catch-all: an
    extraction failure must never affect the chat reply, streaming, or
    message persistence. Cancellation (asyncio.CancelledError, when a new
    foreground message preempts extraction) is deliberately not caught here —
    the scheduler re-queues the job so it runs again after the turn.
    """
    try:
        with db_connection(settings.db_path) as conn:
            result = await extract_memories_from_exchange(
                conn,
                ollama,
                settings,
                conversation_id,
                user_message_seq=user_message_seq,
            )
        # Counts and reason codes only — never message or memory content.
        logger.info(
            "Memory extraction candidates=%d saved=%d skipped=%d error=%s "
            "(conversation=%s, user_seq=%d)",
            result.candidates_seen,
            len(result.saved),
            len(result.skipped),
            result.error,
            conversation_id,
            user_message_seq,
        )
    except Exception:
        logger.warning(
            "Memory extraction failed for conversation=%s (user_seq=%d); "
            "chat response was unaffected.",
            conversation_id,
            user_message_seq,
        )


SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    payload: SendMessageIn,
    request: Request,
    background_tasks: BackgroundTasks,
) -> StreamingResponse:
    """Persist the user message first, then stream the assistant reply.

    The user message is inserted and committed in its own connection so it
    survives even if generation fails or the client disconnects mid-stream.
    The streaming generator opens its own connection and holds it for the
    duration of the stream.

    Memory extraction goes through the app's ExtractionScheduler: it is
    queued when the message arrives and runs from this response's background
    phase, only once no foreground turn is streaming. A reply streaming now
    preempts any extraction already in flight, so the model is never made to
    wait behind background work. Extraction never blocks or alters the reply
    and never claims a memory was saved unless persistence succeeded.
    """
    settings = request.app.state.settings
    ollama: OllamaClient = request.app.state.ollama
    scheduler = request.app.state.extraction_scheduler
    with db_connection(settings.db_path) as conn:
        if get_conversation(conn, conversation_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        user_record = insert_message(conn, conversation_id, "user", payload.content)
        user_payload = user_record.model_dump(mode="json")

    async def event_stream():
        # Foreground bracket lives inside the generator so the increment and
        # decrement are always paired: if the generator never starts there is
        # no turn to balance, and once it starts every exit path (completion,
        # error, disconnect / generator close) runs the finally.
        scheduler.foreground_started()
        try:
            with db_connection(settings.db_path) as stream_conn:
                yield sse_event({"type": "user_message", "message": user_payload})
                async for event in stream_assistant_reply(
                    stream_conn, ollama, settings, conversation_id, request
                ):
                    yield sse_event(event)
        finally:
            scheduler.foreground_finished()

    scheduler.schedule(background_tasks, conversation_id, user_record.seq)
    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=SSE_HEADERS, background=background_tasks
    )


@router.post("/api/conversations/{conversation_id}/retry")
async def retry_generation(
    conversation_id: str, request: Request, background_tasks: BackgroundTasks
) -> StreamingResponse:
    """Re-run generation for the latest user message after a failed attempt.

    Uses the same foreground-priority extraction scheduling as
    `send_message`: the retry streams with priority, and extraction queued
    for this conversation is coalesced with anything already pending.
    """
    settings = request.app.state.settings
    ollama: OllamaClient = request.app.state.ollama
    scheduler = request.app.state.extraction_scheduler
    with db_connection(settings.db_path) as conn:
        if get_conversation(conn, conversation_id) is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        last_message = last_user_message(conn, conversation_id)
        if last_message is None:
            raise HTTPException(
                status_code=400, detail="No user message in this conversation to answer."
            )

    async def event_stream():
        scheduler.foreground_started()
        try:
            with db_connection(settings.db_path) as stream_conn:
                async for event in stream_assistant_reply(
                    stream_conn, ollama, settings, conversation_id, request
                ):
                    yield sse_event(event)
        finally:
            scheduler.foreground_finished()

    scheduler.schedule(background_tasks, conversation_id, last_message.seq)
    return StreamingResponse(
        event_stream(), media_type="text/event-stream", headers=SSE_HEADERS, background=background_tasks
    )
