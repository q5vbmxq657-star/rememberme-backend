# AP1 server-owned chat context integration

Implemented: `MemoryConversationHistoryService`, repository and migration 023.
The reserved routes and iOS files have deliberately not been edited.
Until the following route integration lands, existing routes still accept raw client context.

## Native memory.py

Import `MemoryConversationHistoryService`. After `_authorized_profile_id`, replace
the existing retrieval/enrichment block and evidence closure with:

```python
history = MemoryConversationHistoryService()
enriched_request, context, authorize_evidence = history.prepare(
    request, principal=principal, retrieval_service=retrieval_service,
)
result = OpenAIMemoryService().generate_response(
    enriched_request, authorize=authorize_evidence,
)
history.complete(context, request=enriched_request,
                 assistant_message=result.text, authorize=authorize_evidence)
authorize_evidence()
return result
```

Keep existing profile authorization and sanitized error mapping, particularly 409
for `PGVectorStaleIndexError`. Do not separately retrieve with `request.recent_messages`.
For new clients optionally expose `context.conversation_id` and `context.reset` as
response headers; the legacy response body stays unchanged.

## streaming_memory.py

In `stream_memory_chat`, use the same `history.prepare(...)` before constructing the
response. Pass the enriched request, context, history, and its authorization closure
to `_authorized_events`. Replace its provider iterator construction with:

```python
events = history.stream_events(
    service.stream_response(request, authorize=authorize),
    context=context, request=request, authorize=authorize,
)
```

Keep the existing threadpool iteration, authorization before every outbound event,
HTTP/409 error handling, shielded `events.close()` and `service.close()`. The wrapper
persists only a complete turn, before the `done` acknowledgement. Cancellation,
upstream error, stale revisions and concurrent completion never save a partial turn.
Emotional reasoning now receives the same sanitized request as the primary model.

## iOS

Existing requests remain parseable. Raw `persona_context`, `recent_messages`, and
client memories are ignored by `prepare`; they are not imported into server history.
The current user message remains explicit input. Both request schemas now accept an
optional UUID `conversation_id`. Persist a UUID per actual profile-bound chat and
reuse it for streaming and nonstream fallback. A new chat gets a new UUID. Older
clients use a deterministic profile/user-scoped conversation, preserving continuity
without replaying untrusted history. Never reuse IDs as authorization credentials.

Streaming metadata/done include `conversation_id` and `context_reset`; show a concise
notice when the latter is true: "Your memory settings changed. This conversation
continues with your current settings." Keep historical local UI transcript separate
from provider input. Render 409 as a retryable changed-context response.

No server-owned persona text currently exists in this chat contract. Speaking style
is grounded in the current canonical evidence using the existing prompt. Client
biography must not be reintroduced; preserving additional onboarding style requires
a separately validated profile-owned style field with revision invalidation.

## Database and verification

Migration 023 adds profile/user cascading deletion, bounded stored history (last six
turns), optimistic turn revision and transactional invalidation on memory publication
or consent change. Storage uses no provider calls. The service rechecks purpose,
profile and evidence before provider use and completion. Completed history is
isolated by profile, user and conversation UUID.

Local tests: `tests/test_memory_conversation_history.py`. Real PostgreSQL tests cover
continuity, scope isolation, consent/memory invalidation, delayed writes, concurrent
completion and cascading deletion. Unit tests cover untrusted client context and SSE.

Verified 2026-09-21: **12 tests passed**, including **7 real PostgreSQL cases**.
JUnit evidence: `/private/tmp/AP1-chat-history-20260921.xml`.

Local migration runner currently rejects the outer BEGIN/COMMIT in concurrently
authored migration 022. This file was not edited. For local history tests only,
020 and 023 were applied transactionally with psql. This is not release migration
validation; rerun the canonical migration runner once 022 is corrected by its owner.
