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
    PodcastInvitationSummary,
    PodcastInvitationStatus,
    PodcastMemoryImport,
    PodcastPublicPrompt,
    PodcastPublicMetadata,
    PodcastResponseRecord,
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
    PROCESSING_LEASE = timedelta(minutes=15)
    SESSION_PROMPT_COUNT = 3

    PROMPT_LIBRARY: dict[str, dict[str, list[tuple[str, str]]]] = {
        "de": {
            "childhood": [
                ("childhood_home", "Wie sah der Ort aus, an dem du als Kind am liebsten warst?"),
                ("childhood_person", "Wer hat deine Kindheit besonders geprägt, und wie war diese Person?"),
                ("childhood_moment", "Welche kleine Geschichte aus deiner Kindheit erzählst du bis heute gern?"),
            ],
            "relationships": [
                ("relationships_meeting", "An welche erste Begegnung mit einem wichtigen Menschen erinnerst du dich?"),
                ("relationships_closeness", "Woran hat man bei dir gemerkt, dass dir jemand wichtig war?"),
                ("relationships_ritual", "Welches gemeinsame Ritual möchtest du unbedingt bewahren?"),
            ],
            "everyday_life": [
                ("everyday_morning", "Wie begann ein ganz normaler guter Tag für dich?"),
                ("everyday_habit", "Welche kleine Gewohnheit gehörte unverwechselbar zu dir?"),
                ("everyday_home", "Welche Geräusche, Gerüche oder Dinge machten dein Zuhause zu deinem Zuhause?"),
            ],
            "wisdom": [
                ("wisdom_lesson", "Welche Erfahrung hat deine Sicht auf das Leben verändert?"),
                ("wisdom_values", "Was war dir im Umgang mit anderen Menschen besonders wichtig?"),
                ("wisdom_message", "Welchen Gedanken möchtest du deiner Familie für später mitgeben?"),
            ],
            "life_story": [
                ("story_beginning", "Wo beginnt die Geschichte, die du heute erzählen möchtest?"),
                ("story_turning_point", "Welcher Moment hat diese Zeit für dich besonders geprägt?"),
                ("story_detail", "Welches kleine Detail darf in dieser Geschichte niemals verloren gehen?"),
            ],
        },
        "en": {
            "childhood": [
                ("childhood_home", "What place did you love most as a child, and what was it like?"),
                ("childhood_person", "Who shaped your childhood, and what were they like?"),
                ("childhood_moment", "Which small childhood story do you still enjoy telling?"),
            ],
            "relationships": [
                ("relationships_meeting", "What do you remember about first meeting someone important to you?"),
                ("relationships_closeness", "How could people tell that someone mattered to you?"),
                ("relationships_ritual", "Which shared ritual would you most want your family to remember?"),
            ],
            "everyday_life": [
                ("everyday_morning", "How did an ordinary good day begin for you?"),
                ("everyday_habit", "Which small habit felt unmistakably like you?"),
                ("everyday_home", "Which sounds, smells, or objects made home feel like home?"),
            ],
            "wisdom": [
                ("wisdom_lesson", "Which experience changed how you see life?"),
                ("wisdom_values", "What mattered most to you in the way people treat one another?"),
                ("wisdom_message", "Which thought would you like your family to carry with them?"),
            ],
            "life_story": [
                ("story_beginning", "Where does the story you want to tell today begin?"),
                ("story_turning_point", "Which moment shaped that time most for you?"),
                ("story_detail", "Which small detail from this story should never be lost?"),
            ],
        },
    }

    def __init__(
        self,
        *,
        repository: PodcastRepository | None = None,
        media: AvatarMediaStorageService | None = None,
        openai_client: OpenAI | None = None,
    ) -> None:
        self.repository = repository or PodcastRepository()
        self.media = media or AvatarMediaStorageService()
        self._openai_client = openai_client
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

        theme = self._normalized_theme(request.theme)
        prompts = self._session_prompts(theme=theme, locale=request.locale, opening_prompt=request.prompt)
        prompt_audio_results = await asyncio.gather(
            *(asyncio.to_thread(self._synthesize_prompt, prompt["question"]) for prompt in prompts),
            return_exceptions=True,
        )
        stored_prompt_assets = []
        try:
            for index, (prompt, audio_result) in enumerate(zip(prompts, prompt_audio_results, strict=True)):
                if isinstance(audio_result, BaseException):
                    continue
                audio_upload = UploadFile(
                    file=io.BytesIO(audio_result),
                    filename=f"podcast-prompt-{invitation_id}-{index}.mp3",
                )
                stored = await self.media.upload(
                    profile_id=str(request.profile_id),
                    asset_type="audio",
                    title="Private story interview question",
                    file=audio_upload,
                    base_url=backend_base_url,
                    upload_id=f"podcast-prompt-{invitation_id}-{index}",
                )
                stored_prompt_assets.append(stored)
                prompt["audio_asset_id"] = stored.asset_id
        except Exception:
            for stored in stored_prompt_assets:
                self.media.delete_asset(stored.asset_id)
            raise

        try:
            record = self.repository.create(
                invitation_id=invitation_id,
                profile_id=request.profile_id,
                created_by_user_id=user_id,
                token_digest=self.token_digest(raw_token),
                requester_name=request.requester_name.strip(),
                subject_name=request.subject_name.strip(),
                prompt=prompts[0]["question"],
                theme=theme,
                prompt_sequence=prompts,
                memory_id=memory_id,
                expires_at=expires_at,
                prompt_audio_asset_id=(
                    UUID(str(prompts[0]["audio_asset_id"]))
                    if prompts[0].get("audio_asset_id")
                    else None
                ),
            )
        except Exception:
            for stored in stored_prompt_assets:
                self.media.delete_asset(stored.asset_id)
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
        prompts: list[PodcastPublicPrompt] = []
        for index, prompt in enumerate(record.prompt_sequence):
            asset_id = str(prompt.get("audio_asset_id") or "").strip()
            audio_url = None
            if asset_id:
                audio_url = self.media.sign_download_url(
                    asset_id=asset_id,
                    base_url=backend_base_url,
                    expires_in_seconds=900,
                ).signed_url
            prompts.append(PodcastPublicPrompt(
                prompt_id=str(prompt.get("prompt_id") or f"legacy_{index}"),
                category=str(prompt.get("category") or record.theme),
                question=str(prompt.get("question") or record.prompt),
                audio_url=audio_url,
            ))
        if not prompts:
            legacy_audio_url = None
            if record.prompt_audio_asset_id:
                legacy_audio_url = self.media.sign_download_url(
                    asset_id=str(record.prompt_audio_asset_id),
                    base_url=backend_base_url,
                    expires_in_seconds=900,
                ).signed_url
            prompts = [PodcastPublicPrompt(
                prompt_id="legacy_prompt",
                category=record.theme,
                question=record.prompt,
                audio_url=legacy_audio_url,
            )]
        return PodcastPublicMetadata(
            invitation_id=record.invitation_id,
            requester_name=record.requester_name,
            subject_name=record.subject_name,
            prompt=record.prompt,
            prompt_audio_url=prompts[0].audio_url,
            theme=record.theme,
            prompts=prompts,
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
        return await self.complete_session(
            token=token,
            files=[file],
            speaker_confirmed_subject=False,
            voice_training_consent_granted=False,
            backend_base_url=backend_base_url,
        )

    async def complete_session(
        self,
        *,
        token: str,
        files: list[UploadFile],
        speaker_confirmed_subject: bool,
        voice_training_consent_granted: bool,
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

        if not files or len(files) > self.SESSION_PROMPT_COUNT:
            raise PodcastServiceError("Please record between one and three answers.", code="invalid_turn_count")
        if voice_training_consent_granted and not speaker_confirmed_subject:
            raise PodcastServiceError(
                "Voice permission can only be given by the person whose voice was recorded.",
                code="voice_identity_confirmation_required",
            )

        prompts = record.prompt_sequence or [{
            "prompt_id": "legacy_prompt",
            "category": record.theme,
            "question": record.prompt,
        }]
        if len(files) > len(prompts):
            raise PodcastServiceError("This interview contains too many answers.", code="invalid_turn_count")

        try:
            record = self.repository.claim_recording_upload(
                invitation_id=record.invitation_id
            )
        except Exception as error:
            raise PodcastServiceError(
                "This interview is already receiving an answer. Please wait a moment.",
                code="processing_in_progress",
            ) from error

        stored_responses = []
        responses: list[PodcastResponseRecord] = []
        try:
            for index, upload in enumerate(files):
                stored_responses.append(await self.media.upload(
                    profile_id=str(record.profile_id),
                    asset_type="audio",
                    title=f"Story interview answer {index + 1} from {record.subject_name}",
                    file=upload,
                    base_url=backend_base_url,
                    upload_id=f"podcast-response-{record.invitation_id}-{index}",
                ))
            self.repository.mark_status(
                invitation_id=record.invitation_id,
                expected_statuses=(PodcastInvitationStatus.recording,),
                status=PodcastInvitationStatus.uploaded,
                response_audio_asset_id=UUID(stored_responses[0].asset_id),
            )
            self.repository.mark_status(
                invitation_id=record.invitation_id,
                expected_statuses=(PodcastInvitationStatus.uploaded,),
                status=PodcastInvitationStatus.processing,
            )
            ingestions = await asyncio.gather(*(
                asyncio.to_thread(
                    MemoryIngestionService().ingest,
                    MemoryIngestionRequest(
                        profile_id=str(record.profile_id),
                        asset_id=stored.asset_id,
                        asset_type="voice",
                        title=f"{record.subject_name}'s story: {prompts[index]['category']}",
                        user_context=(
                            f"Private story interview question for {record.subject_name}: "
                            f"{prompts[index]['question']}"
                        ),
                    ),
                )
                for index, stored in enumerate(stored_responses)
            ))
            now = datetime.now(timezone.utc)
            for index, (stored, ingestion) in enumerate(zip(stored_responses, ingestions, strict=True)):
                transcript = (ingestion.transcript or "").strip()
                if len(transcript) < 2:
                    raise PodcastServiceError(
                        "We could not hear enough of one answer. Please record it again.",
                        code="empty_transcript",
                    )
                response_memory_id = record.memory_id if index == 0 else uuid4()
                responses.append(PodcastResponseRecord(
                    response_id=uuid4(),
                    invitation_id=record.invitation_id,
                    turn_index=index,
                    prompt_id=str(prompts[index].get("prompt_id") or f"turn_{index}"),
                    category=str(prompts[index].get("category") or record.theme),
                    question=str(prompts[index].get("question") or record.prompt),
                    audio_asset_id=UUID(stored.asset_id),
                    memory_id=response_memory_id,
                    transcript=transcript,
                    summary=ingestion.summary,
                    memory_payload=ingestion.model_dump(mode="json"),
                    created_at=now,
                ))

            vector_service = PGVectorMemoryService()
            for response, ingestion in zip(responses, ingestions, strict=True):
                await asyncio.to_thread(
                    vector_service.upsert_external_memory,
                    memory_id=str(response.memory_id),
                    profile_id=str(record.profile_id),
                    title=ingestion.title,
                    summary=ingestion.summary,
                    original_text=response.transcript,
                    memory_type="voiceMemory",
                    emotional_tags=ingestion.emotional_tags,
                    confidence_score=ingestion.confidence_score,
                )
            completed = self.repository.complete_session(
                invitation_id=record.invitation_id,
                responses=responses,
                speaker_confirmed_subject=speaker_confirmed_subject,
                voice_training_consent_granted=voice_training_consent_granted,
            )
            return self._completed_response(
                completed,
                memory_ids=[response.memory_id for response in responses],
            )
        except PodcastServiceError:
            self._mark_retryable(record.invitation_id, "empty_transcript")
            self._rollback_session_assets(
                record=record,
                stored_responses=stored_responses,
                responses=responses,
            )
            raise
        except Exception as error:
            self._mark_retryable(record.invitation_id, "processing_failed")
            self._rollback_session_assets(
                record=record,
                stored_responses=stored_responses,
                responses=responses,
            )
            raise PodcastServiceError(
                "Your recording is still on this device. Please try sending it again.",
                code="processing_failed",
            ) from error

    def list_imports(self, *, profile_id: UUID, backend_base_url: str) -> list[PodcastMemoryImport]:
        imports: list[PodcastMemoryImport] = []
        session_rows = self.repository.list_completed_responses(profile_id=profile_id)
        voice_source_by_invitation: dict[UUID, PodcastResponseRecord] = {}
        for invitation, response in session_rows:
            if (
                invitation.speaker_confirmed_subject
                and invitation.voice_training_consent_granted
                and invitation.voice_training_used_at is None
            ):
                selected_response = voice_source_by_invitation.get(invitation.invitation_id)
                if selected_response is None:
                    voice_source_by_invitation[invitation.invitation_id] = response
                    continue
                if (len(response.transcript), -response.turn_index) > (
                    len(selected_response.transcript), -selected_response.turn_index
                ):
                    voice_source_by_invitation[invitation.invitation_id] = response
        for record, response in session_rows:
            audio_url = self.media.sign_download_url(
                asset_id=str(response.audio_asset_id),
                base_url=backend_base_url,
                expires_in_seconds=900,
            ).signed_url
            audio_metadata = self.media.get_metadata(str(response.audio_asset_id))
            payload = response.memory_payload
            imports.append(PodcastMemoryImport(
                invitation_id=record.invitation_id,
                memory_id=response.memory_id,
                profile_id=record.profile_id,
                title=str(payload.get("title") or f"Story: {response.category}"),
                original_text=response.transcript,
                summary=response.summary,
                avatar_memory_text=str(payload.get("avatar_memory_text") or response.summary),
                emotional_tags=list(payload.get("emotional_tags") or []),
                extracted_topics=self._topics_with_category(payload, response.category),
                confidence_score=float(payload.get("confidence_score") or 0.7),
                audio_url=audio_url,
                audio_content_type=audio_metadata.content_type,
                created_at=response.created_at,
                category=response.category,
                prompt=response.question,
                voice_training_eligible=(
                    voice_source_by_invitation.get(record.invitation_id) == response
                ),
                voice_training_used_at=record.voice_training_used_at,
            ))

        session_invitation_ids = {record.invitation_id for record, _ in session_rows}
        for record in self.repository.list_completed(profile_id=profile_id):
            if record.invitation_id in session_invitation_ids:
                continue
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
                    category=record.theme,
                    prompt=record.prompt,
                    voice_training_eligible=False,
                    voice_training_used_at=record.voice_training_used_at,
                )
            )
        return imports

    def list_invitations(self, *, profile_id: UUID) -> list[PodcastInvitationSummary]:
        return [
            PodcastInvitationSummary(
                invitation_id=record.invitation_id,
                profile_id=record.profile_id,
                subject_name=record.subject_name,
                theme=record.theme,
                status=record.status,
                answer_count=answer_count,
                created_at=record.created_at,
                completed_at=record.completed_at,
            )
            for record, answer_count in self.repository.list_recent(profile_id=profile_id)
        ]

    def mark_voice_training_used(self, *, invitation_id: UUID, profile_id: UUID) -> bool:
        return self.repository.mark_voice_training_used(
            invitation_id=invitation_id,
            profile_id=profile_id,
        )

    def _active_record(self, token: str) -> PodcastInvitationRecord:
        clean = token.strip()
        if len(clean) < 32 or len(clean) > 128:
            raise PodcastInvitationNotFound("Invitation not found.")
        record = self.repository.get_by_token_digest(self.token_digest(clean))
        if record.expires_at <= datetime.now(timezone.utc):
            raise PodcastServiceError("This private interview has expired.", code="invitation_expired")
        if (
            record.status
            in {
                PodcastInvitationStatus.recording,
                PodcastInvitationStatus.uploaded,
                PodcastInvitationStatus.processing,
            }
            and record.updated_at
            <= datetime.now(timezone.utc) - self.PROCESSING_LEASE
        ):
            try:
                record = self.repository.mark_status(
                    invitation_id=record.invitation_id,
                    expected_statuses=(record.status,),
                    status=PodcastInvitationStatus.retryable_failed,
                    safe_error_code="processing_lease_expired",
                )
            except Exception:
                # A concurrent worker may have completed or advanced the
                # invitation after it was read. Its authoritative state wins.
                record = self.repository.get_by_token_digest(self.token_digest(clean))
        return record

    def _mark_retryable(self, invitation_id: UUID, code: str) -> None:
        try:
            self.repository.mark_status(
                invitation_id=invitation_id,
                expected_statuses=(
                    PodcastInvitationStatus.recording,
                    PodcastInvitationStatus.uploaded,
                    PodcastInvitationStatus.processing,
                ),
                status=PodcastInvitationStatus.retryable_failed,
                safe_error_code=code,
            )
        except Exception:
            pass

    def _rollback_session_assets(
        self,
        *,
        record: PodcastInvitationRecord,
        stored_responses: list,
        responses: list[PodcastResponseRecord],
    ) -> None:
        self._delete_assets(stored.asset_id for stored in stored_responses)
        if responses:
            try:
                PGVectorMemoryService().delete_external_memories(
                    profile_id=str(record.profile_id),
                    memory_ids=[str(response.memory_id) for response in responses],
                )
            except Exception:
                pass

    def _delete_assets(self, asset_ids) -> None:
        for asset_id in asset_ids:
            try:
                self.media.delete_asset(str(asset_id))
            except Exception:
                pass

    def _synthesize_prompt(self, prompt: str) -> bytes:
        response = self._resolved_openai_client().audio.speech.create(
            model=self.tts_model,
            voice=self.tts_voice,
            input=prompt,
            response_format="mp3",
        )
        return response.read()

    def _resolved_openai_client(self) -> OpenAI:
        if self._openai_client is None:
            self._openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
        return self._openai_client

    def _session_prompts(
        self,
        *,
        theme: str,
        locale: str,
        opening_prompt: str | None,
    ) -> list[dict[str, str]]:
        language = "de" if locale.lower().startswith("de") else "en"
        prompt_source = self.PROMPT_LIBRARY[language].get(theme) or self.PROMPT_LIBRARY[language]["life_story"]
        prompts = [
            {"prompt_id": prompt_id, "category": theme, "question": question}
            for prompt_id, question in prompt_source[:self.SESSION_PROMPT_COUNT]
        ]
        clean_opening = (opening_prompt or "").strip()
        if clean_opening:
            prompts[0] = {
                "prompt_id": f"custom_{theme}",
                "category": theme,
                "question": clean_opening,
            }
        return prompts

    def _normalized_theme(self, value: str) -> str:
        normalized = value.strip().lower().replace("-", "_").replace(" ", "_")
        allowed = set(self.PROMPT_LIBRARY["en"].keys())
        return normalized if normalized in allowed else "life_story"

    @staticmethod
    def _topics_with_category(payload: dict, category: str) -> list[str]:
        values = [str(value) for value in (payload.get("extracted_topics") or []) if str(value).strip()]
        if category not in values:
            values.insert(0, category)
        return values

    @staticmethod
    def token_digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _completed_response(
        record: PodcastInvitationRecord,
        *,
        memory_ids: list[UUID] | None = None,
    ) -> PodcastUploadResponse:
        resolved_memory_ids = memory_ids or [record.memory_id]
        return PodcastUploadResponse(
            invitation_id=record.invitation_id,
            status=PodcastInvitationStatus.completed,
            memory_id=record.memory_id,
            memory_ids=resolved_memory_ids,
            message=f"Vielen Dank! Deine Geschichte wurde sicher für {record.subject_name} gespeichert.",
        )
