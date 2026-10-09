from fastapi import APIRouter, Depends, HTTPException
import logging
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import Send
from anyio import CancelScope

from app.schemas.streaming_memory import StreamingMemoryChatRequest
from app.security.profile_authorization import require_profile_access
from app.security.purpose_authorization import require_profile_purposes
from app.security.user_auth import (
    AuthenticatedSessionPrincipal,
    require_authenticated_principal,
)
from app.services.memory_chat_retrieval_service import MemoryChatRetrievalService
from app.services.pgvector_memory_service import PGVectorStaleIndexError
from app.services.streaming_memory_service import StreamingMemoryService
from app.services.memory_conversation_history import MemoryConversationHistoryService
from app.services.chat_usage import ChatUsage
from app.services.conversation_usage import conversation_usage


router = APIRouter()
retrieval_service = MemoryChatRetrievalService()
logger = logging.getLogger(__name__)


class _ClosingMemoryStreamingResponse(StreamingResponse):
    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            # A disconnect can cancel send() while the generator is suspended at yield.
            with CancelScope(shield=True):
                await self.body_iterator.aclose()


@router.post("/chat")
def stream_memory_chat(
    request: StreamingMemoryChatRequest,
    principal: AuthenticatedSessionPrincipal = Depends(require_authenticated_principal),
):
    profile_id = _authorized_profile_id(request.profile_id, principal)
    consent = require_profile_purposes(profile_id, {"memory_context"})
    usage = None

    try:
        history = MemoryConversationHistoryService()
        enriched_request, context, authorize_context = history.prepare(
            request, principal=principal, retrieval_service=retrieval_service)
        if context.consent_revision != consent.revision:
            raise HTTPException(status_code=409, detail="Memory settings changed. Please try again.")
        def authorize():
            # The canonical context already verifies membership, consent revision,
            # and current evidence. Do not repeat its database reads per frame.
            authorize_context()
            if request.channel == "voice" and usage is not None:
                usage.reserve()
        authorize()
        usage = conversation_usage(principal, request, chat_factory=ChatUsage)
        usage.reserve()
        return _ClosingMemoryStreamingResponse(
            _authorized_events(enriched_request, history=history, context=context, authorize=authorize, usage=usage),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    except HTTPException:
        raise
    except PGVectorStaleIndexError as error:
        raise HTTPException(status_code=409, detail="Memory settings changed. Please try again.") from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="We could not start that response. Please try again.",
        ) from error


async def _authorized_events(request, *, history, context, authorize, usage=None):
    service = StreamingMemoryService()
    events = None
    source = None
    completed = False
    try:
        source = service.stream_response(request, authorize=authorize)
        events = history.stream_events(source, context=context, request=request, authorize=authorize)
        await run_in_threadpool(authorize)
        while True:
            event = await run_in_threadpool(next, events, None)
            if event is None:
                break
            await run_in_threadpool(authorize)
            if event.startswith("event: done\n") and usage is not None:
                await run_in_threadpool(usage.finish, completed=True)
                completed = True
            yield event
    except (HTTPException, PGVectorStaleIndexError) as error:
        logger.warning("memory_stream_interrupted channel=%s error_type=%s status=%s",
                       request.channel, type(error).__name__, getattr(error, "status_code", None))
        yield service._event("error", {
            "status": "failed",
            "message": "This conversation is no longer available.",
        })
    except Exception as error:
        logger.error("memory_stream_failed channel=%s error_type=%s",
                     request.channel, type(error).__name__)
        yield service._event("error", {
            "status": "failed",
            "message": "We could not complete that response. Please try again.",
        })
    finally:
        # Cancellation must close the upstream response, not only the client-facing iterator.
        with CancelScope(shield=True):
            try:
                if events is not None:
                    await run_in_threadpool(events.close)
            finally:
                try:
                    if source is not None:
                        await run_in_threadpool(source.close)
                finally:
                    try:
                        await run_in_threadpool(service.close)
                    finally:
                        if usage is not None and not completed:
                            await run_in_threadpool(usage.finish, completed=False)


def _authorized_profile_id(
    profile_id: str | None,
    principal: AuthenticatedSessionPrincipal,
) -> str:
    clean_profile_id = (profile_id or "").strip()
    if not clean_profile_id:
        raise HTTPException(
            status_code=422,
            detail="profile_id is required for canonical memory retrieval.",
        )
    require_profile_access(principal=principal, profile_id=clean_profile_id)
    return clean_profile_id
