"""Server-owned chat continuity; legacy client histories are never imported."""
from __future__ import annotations

import os
import json
from dataclasses import dataclass
from uuid import UUID, uuid5, NAMESPACE_URL

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.schemas.profile_consent import CONSENT_POLICY_VERSION
from app.security.profile_authorization import require_profile_access
from app.security.purpose_authorization import require_profile_purposes
from app.services.pgvector_memory_service import PGVectorStaleIndexError
from app.services.memory_chat_retrieval_service import require_current_memory_evidence


@dataclass(frozen=True)
class ConversationContext:
    profile_id: UUID
    user_id: UUID
    conversation_id: UUID
    revision: int
    evidence_version: list
    consent_revision: int
    messages: tuple[str, ...]
    reset: bool


class MemoryConversationHistoryRepository:
    def __init__(self, database_url=None):
        self.database_url = database_url or os.environ["DATABASE_URL"]

    def _connect(self):
        return psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row)

    @staticmethod
    def _version(connection, profile_id, consent_revision):
        profile = connection.execute("SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR SHARE",
                                     (profile_id,)).fetchone()
        if profile is None or connection.execute(
            "SELECT 1 FROM digital_human_profile_erasure_requests WHERE profile_id=%s", (profile_id,)
        ).fetchone():
            raise PGVectorStaleIndexError("Conversation no longer available.")
        consent = connection.execute("SELECT revision, policy_version, purposes FROM profile_purpose_consents WHERE profile_id=%s FOR SHARE",
                                     (profile_id,)).fetchone()
        if (not consent or consent["revision"] != consent_revision
                or consent["policy_version"] != CONSENT_POLICY_VERSION
                or not {"memory_context", "provider_processing"}.issubset(consent["purposes"])):
            raise PGVectorStaleIndexError("Conversation permissions changed.")
        row = connection.execute("SELECT generation, published_generation, operation_id FROM memory_index_generations WHERE profile_id=%s FOR SHARE",
                                 (profile_id,)).fetchone()
        if row and row["generation"] != row["published_generation"]:
            raise PGVectorStaleIndexError("Memory publication is pending.")
        return [row["generation"], row["published_generation"], str(row["operation_id"])] if row else []

    def load(self, *, profile_id, user_id, consent_revision, conversation_id=None):
        profile_id, user_id = UUID(str(profile_id)), UUID(str(user_id))
        # Older clients have no conversation identifier; continuity stays user/profile scoped.
        conversation_id = UUID(str(conversation_id)) if conversation_id else uuid5(
            NAMESPACE_URL, f"stay:memory-chat:{profile_id}:{user_id}")
        with self._connect() as connection:
            version = self._version(connection, profile_id, consent_revision)
            row = connection.execute("SELECT * FROM memory_conversation_history WHERE profile_id=%s AND user_id=%s AND conversation_id=%s",
                                     (profile_id, user_id, conversation_id)).fetchone()
        current = bool(row and row["evidence_version"] == version and row["consent_revision"] == consent_revision)
        return ConversationContext(profile_id, user_id, conversation_id, row["revision"] if row else 0,
                                   version, consent_revision, tuple(row["messages"]) if current else (), bool(row and not current))

    def require_current(self, context):
        with self._connect() as connection:
            if self._version(connection, context.profile_id, context.consent_revision) != context.evidence_version:
                raise PGVectorStaleIndexError("Memory settings changed.")

    def append(self, context, *, user_message, assistant_message):
        if not user_message.strip() or not assistant_message.strip():
            raise ValueError("Only completed conversation turns can be saved.")
        if max(len(user_message), len(assistant_message)) > 32000:
            raise ValueError("Conversation turn is too long.")
        messages = [*context.messages, "User: " + user_message, "Assistant: " + assistant_message][-12:]
        with self._connect() as connection:
            if self._version(connection, context.profile_id, context.consent_revision) != context.evidence_version:
                raise PGVectorStaleIndexError("Memory settings changed.")
            row = connection.execute("""INSERT INTO memory_conversation_history
                (profile_id,user_id,conversation_id,revision,evidence_version,consent_revision,messages)
                SELECT %s,%s,%s,1,%s,%s,%s WHERE %s=0
                ON CONFLICT (profile_id,user_id,conversation_id) DO NOTHING RETURNING revision""",
                (context.profile_id, context.user_id, context.conversation_id, Jsonb(context.evidence_version),
                 context.consent_revision, Jsonb(messages), context.revision)).fetchone() if context.revision == 0 else None
            if context.revision > 0:
                row = connection.execute("""UPDATE memory_conversation_history SET revision=revision+1,
                    evidence_version=%s,consent_revision=%s,messages=%s,updated_at=NOW()
                    WHERE profile_id=%s AND user_id=%s AND conversation_id=%s AND revision=%s RETURNING revision""",
                    (Jsonb(context.evidence_version), context.consent_revision, Jsonb(messages), context.profile_id,
                     context.user_id, context.conversation_id, context.revision)).fetchone()
            if row is None:
                raise PGVectorStaleIndexError("Another conversation turn completed. Please retry.")
            return row["revision"]


class MemoryConversationHistoryService:
    def __init__(self, repository=None):
        self.repository = repository or MemoryConversationHistoryRepository()

    def prepare(self, request, *, principal, retrieval_service):
        profile_id = UUID(str(request.profile_id))
        require_profile_access(principal=principal, profile_id=profile_id)
        consent = require_profile_purposes(profile_id, {"memory_context"})
        context = self.repository.load(profile_id=profile_id, user_id=principal.user.user_id,
            consent_revision=consent.revision, conversation_id=getattr(request, "conversation_id", None))
        memories = retrieval_service.retrieve(profile_id=str(profile_id), user_message=request.user_message,
            recent_messages=context.messages, retrieval_limit=request.retrieval_limit)
        # Style is derived from current canonical evidence by the shared prompt, never client biography.
        enriched = request.model_copy(update={"persona_context": "Use only the speaking-style evidence in the relevant saved evidence below.",
                                              "recent_messages": list(context.messages), "memories": memories})
        def authorize():
            require_profile_access(principal=principal, profile_id=profile_id)
            require_profile_purposes(profile_id, {"memory_context"}, expected_revision=context.consent_revision)
            self.repository.require_current(context)
            require_current_memory_evidence(memories, profile_id=str(profile_id))
        authorize()
        return enriched, context, authorize

    def complete(self, context, *, request, assistant_message, authorize):
        authorize()
        return self.repository.append(context, user_message=request.user_message, assistant_message=assistant_message)

    def stream_events(self, events, *, context, request, authorize):
        """Wrap the existing SSE generator; persist before acknowledging completion."""
        chunks = []
        size = 0
        try:
            for event in events:
                authorize()
                fields = dict(line.split(": ", 1) for line in event.splitlines() if ": " in line)
                kind = fields.get("event")
                data = json.loads(fields.get("data", "{}"))
                if kind == "metadata":
                    data.update(conversation_id=str(context.conversation_id), context_reset=context.reset)
                    event = f"event: metadata\ndata: {json.dumps(data)}\n\n"
                elif kind == "delta":
                    text = data.get("text", "")
                    size += len(text)
                    if size > 32000:
                        raise ValueError("Conversation response is too long.")
                    chunks.append(text)
                elif kind == "done":
                    revision = self.complete(context, request=request,
                        assistant_message="".join(chunks), authorize=authorize)
                    data.update(conversation_id=str(context.conversation_id), conversation_revision=revision,
                                context_reset=context.reset)
                    yield f"event: done\ndata: {json.dumps(data)}\n\n"
                    return
                yield event
                if kind == "error":
                    return
        finally:
            events.close()
