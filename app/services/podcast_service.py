from __future__ import annotations

import asyncio
import hashlib
import io
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import UploadFile
from openai import OpenAI

from app.schemas.memory_ingestion import MemoryIngestionRequest
from app.schemas.podcast import (
    PodcastInvitationCreateRequest,
    PodcastInvitationCreateResponse,
    PodcastInvitationRecord,
    PodcastInvitationStatus,
    PodcastMemoryImport,
    PodcastPublicMetadata,
    PodcastUploadResponse,
)
from app.services.avatar_media_storage_service import AvatarMediaStorageService
from app.services.memory_ingestion_service import MemoryIngestionService
from app.services.pgvector_memory_service import PGVectorMemoryService
from app.services.podcast_repository import PodcastInvitationNotFound, PodcastRepository


class PodcastServiceError(RuntimeError):
    def __init__(self, safe_message: str, *, code: str) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.code = code


class PodcastService:
    INVITATION_LIFETIME = timedelta(days=14)

    def __init__(
        self,
        *,
        repository: PodcastRepository | None = None,
        media: AvatarMediaStorageService | None = None,
        openai_client: OpenAI | None = None,
    ) -> None:
        self.repository = repository or PodcastRepository()
        self.media = media or AvatarMediaStorageService()
        self.openai_client = openai_client or OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.tts_model = os.getenv("OPENAI_PODCAST_TTS_MODEL", "tts-1-hd")
        self.tts_voice = os.getenv("OPENAI_PODCAST_TTS_VOICE", "coral")

    async def create_invitation(
        self,
        *,
        request: PodcastInvitationCreateRequest,
        user_id: UUID,
        public_web_base_url: str,
        backend_base_url: str,
    ) -> PodcastInvitationCreateResponse:
        invitation_id = uuid4()
        memory_id = uuid4()
        raw_token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + self.INVITATION_LIFETIME

        prompt_audio = await asyncio.to_thread(self._synthesize_prompt, request.prompt)
        audio_upload = UploadFile(
            file=io.BytesIO(prompt_audio),
            filename=f"podcast-prompt-{invitation_id}.mp3",
        )
        stored_audio = await self.media.upload(
            profile_id=str(request.profile_id),
            asset_type="audio",
            title="Shared memory question",
            file=audio_upload,
            base_url=backend_base_url,
            upload_id=f"podcast-prompt-{invitation_id}",
        )

        try:
            record = self.repository.create(
                invitation_id=invitation_id,
                profile_id=request.profile_id,
                created_by_user_id=user_id,
                token_digest=self.token_digest(raw_token),
                requester_name=request.requester_name.strip(),
                subject_name=request.subject_name.strip(),
                prompt=request.prompt.strip(),
                memory_id=memory_id,
                expires_at=expires_at,
                prompt_audio_asset_id=UUID(stored_audio.asset_id),
            )
        except Exception:
            self.media.delete_asset(stored_audio.asset_id)
            raise
        return PodcastInvitationCreateResponse(
            invitation_id=record.invitation_id,
            profile_id=record.profile_id,
            share_url=f"{public_web_base_url.rstrip('/')}/p/{raw_token}",
            status=record.status,
            expires_at=record.expires_at,
        )

    def public_metadata(self, *, token: str, backend_base_url: str) -> PodcastPublicMetadata:
        record = self._active_record(token)
        prompt_audio_url = None
        if record.prompt_audio_asset_id:
            prompt_audio_url = self.media.sign_download_url(
                asset_id=str(record.prompt_audio_asset_id),
                base_url=backend_base_url,
                expires_in_seconds=900,
            ).signed_url
        return PodcastPublicMetadata(
            invitation_id=record.invitation_id,
            requester_name=record.requester_name,
            subject_name=record.subject_name,
            prompt=record.prompt,
            prompt_audio_url=prompt_audio_url,
            status=record.status,
            expires_at=record.expires_at,
        )

    async def ingest_response(
        self,
        *,
        token: str,
        file: UploadFile,
        backend_base_url: str,
    ) -> PodcastUploadResponse:
        record = self._active_record(token)
        if record.status == PodcastInvitationStatus.completed:
            return self._completed_response(record)
        if record.status not in {
            PodcastInvitationStatus.pending,
            PodcastInvitationStatus.recording,
            PodcastInvitationStatus.retryable_failed,
        }:
            raise PodcastServiceError(
                "This answer is already being processed.",
                code="processing_in_progress",
            )

        stored = await self.media.upload(
            profile_id=str(record.profile_id),
            asset_type="audio",
            title=f"Answer about {record.subject_name}",
            file=file,
            base_url=backend_base_url,
            upload_id=f"podcast-response-{record.invitation_id}",
        )
        self.repository.mark_status(
            invitation_id=record.invitation_id,
            expected_statuses=(
                PodcastInvitationStatus.pending,
                PodcastInvitationStatus.recording,
                PodcastInvitationStatus.retryable_failed,
            ),
            status=PodcastInvitationStatus.uploaded,
            response_audio_asset_id=UUID(stored.asset_id),
        )
        self.repository.mark_status(
            invitation_id=record.invitation_id,
            expected_statuses=(PodcastInvitationStatus.uploaded,),
            status=PodcastInvitationStatus.processing,
        )

        try:
            ingestion = await asyncio.to_thread(
                MemoryIngestionService().ingest,
                MemoryIngestionRequest(
                    profile_id=str(record.profile_id),
                    asset_id=stored.asset_id,
                    asset_type="voice",
                    title=f"A shared answer from {record.requester_name}",
                    user_context=(
                        f"Question about {record.subject_name}: {record.prompt}"
                    ),
                ),
            )
            transcript = (ingestion.transcript or "").strip()
            if len(transcript) < 2:
                raise PodcastServiceError(
                    "We could not hear enough of the answer. Please record it again.",
                    code="empty_transcript",
                )
            payload = ingestion.model_dump(mode="json")
            completed = self.repository.complete(
                invitation_id=record.invitation_id,
                transcript=transcript,
                summary=ingestion.summary,
                memory_payload=payload,
            )
            await asyncio.to_thread(
                PGVectorMemoryService().upsert_external_memory,
                memory_id=str(completed.memory_id),
                profile_id=str(completed.profile_id),
                title=ingestion.title,
                summary=ingestion.summary,
                original_text=transcript,
                memory_type="voiceMemory",
                emotional_tags=ingestion.emotional_tags,
                confidence_score=ingestion.confidence_score,
            )
            return self._completed_response(completed)
        except PodcastServiceError:
            self._mark_retryable(record.invitation_id, "empty_transcript")
            raise
        except Exception as error:
            self._mark_retryable(record.invitation_id, "processing_failed")
            raise PodcastServiceError(
                "Your answer was saved, but processing needs another try.",
                code="processing_failed",
            ) from error

    def list_imports(self, *, profile_id: UUID, backend_base_url: str) -> list[PodcastMemoryImport]:
        imports: list[PodcastMemoryImport] = []
        for record in self.repository.list_completed(profile_id=profile_id):
            payload = record.memory_payload or {}
            if record.response_audio_asset_id is None or not record.transcript:
                continue
            audio_url = self.media.sign_download_url(
                asset_id=str(record.response_audio_asset_id),
                base_url=backend_base_url,
                expires_in_seconds=900,
            ).signed_url
            audio_metadata = self.media.get_metadata(str(record.response_audio_asset_id))
            imports.append(
                PodcastMemoryImport(
                    invitation_id=record.invitation_id,
                    memory_id=record.memory_id,
                    profile_id=record.profile_id,
                    title=str(payload.get("title") or "Shared voice memory"),
                    original_text=record.transcript,
                    summary=record.summary or record.transcript,
                    avatar_memory_text=str(
                        payload.get("avatar_memory_text") or record.summary or record.transcript
                    ),
                    emotional_tags=list(payload.get("emotional_tags") or []),
                    extracted_topics=list(payload.get("extracted_topics") or []),
                    confidence_score=float(payload.get("confidence_score") or 0.7),
                    audio_url=audio_url,
                    audio_content_type=audio_metadata.content_type,
                    created_at=record.completed_at or record.updated_at,
                )
            )
        return imports

    def _active_record(self, token: str) -> PodcastInvitationRecord:
        clean = token.strip()
        if len(clean) < 32 or len(clean) > 128:
            raise PodcastInvitationNotFound("Invitation not found.")
        record = self.repository.get_by_token_digest(self.token_digest(clean))
        if record.expires_at <= datetime.now(timezone.utc):
            raise PodcastServiceError("This question has expired.", code="invitation_expired")
        return record

    def _mark_retryable(self, invitation_id: UUID, code: str) -> None:
        try:
            self.repository.mark_status(
                invitation_id=invitation_id,
                expected_statuses=(PodcastInvitationStatus.processing,),
                status=PodcastInvitationStatus.retryable_failed,
                safe_error_code=code,
            )
        except Exception:
            pass

    def _synthesize_prompt(self, prompt: str) -> bytes:
        response = self.openai_client.audio.speech.create(
            model=self.tts_model,
            voice=self.tts_voice,
            input=prompt,
            response_format="mp3",
        )
        return response.read()

    @staticmethod
    def token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _completed_response(record: PodcastInvitationRecord) -> PodcastUploadResponse:
        return PodcastUploadResponse(
            invitation_id=record.invitation_id,
            status=PodcastInvitationStatus.completed,
            memory_id=record.memory_id,
            message=f"Vielen Dank! Deine Antwort wurde sicher für {record.subject_name} gespeichert.",
        )
