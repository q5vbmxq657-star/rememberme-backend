from __future__ import annotations

import asyncio
import os
import threading

from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Dict, Optional
from uuid import uuid4

from fastapi import UploadFile

from app.schemas.avatar_runtime import (
    AvatarRuntimeOperationResponse,
    AvatarRuntimeProvider,
    AvatarRuntimeSessionCreateRequest,
    AvatarRuntimeSessionResponse,
    AvatarRuntimeSpeechMetadata,
    AvatarRuntimeSpeechResponse,
    AvatarRuntimeTransport,
)
from app.services.avatar_runtime_livekit_token_service import (
    AvatarRuntimeLiveKitTokenService,
)
from app.services.avatar_runtime_provider_registry import (
    AvatarRuntimeProviderRegistry,
)
from app.services.avatar_runtime_tavus_adapter import (
    AvatarRuntimeTavusAdapter,
    TavusRuntimeAudioError,
    TavusRuntimeConnectionError,
    TavusRuntimeError,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileNotFoundError,
    DigitalHumanProfileRepository,
    DigitalHumanProfileRepositoryError,
)


class AvatarRuntimeServiceError(Exception):
    status_code = 500

    def __init__(
        self,
        message: str,
    ) -> None:
        super().__init__(message)
        self.message = message


class AvatarRuntimeSessionNotFoundError(
    AvatarRuntimeServiceError
):
    status_code = 404


class AvatarRuntimeValidationError(
    AvatarRuntimeServiceError
):
    status_code = 422


class AvatarRuntimeProviderUnavailableError(
    AvatarRuntimeServiceError
):
    status_code = 503


class AvatarRuntimeConflictError(
    AvatarRuntimeServiceError
):
    status_code = 409


@dataclass(frozen=True)
class StoredRuntimeSession:
    session: AvatarRuntimeSessionResponse
    interrupted_at: Optional[datetime] = None


class AvatarRuntimeSessionService:
    """
    Canonical Tavus/LiveKit avatar runtime gateway.

    A LiveKit descriptor is returned only after the configured Tavus worker,
    Tavus participant and Tavus video track have been verified.
    """

    _instance: Optional[
        "AvatarRuntimeSessionService"
    ] = None
    _instance_lock = threading.Lock()

    max_audio_bytes = int(
        os.getenv(
            "AVATAR_RUNTIME_MAX_AUDIO_BYTES",
            str(25 * 1024 * 1024),
        )
    )

    supported_audio_content_types = {
        "audio/mpeg",
        "audio/mp3",
        "audio/mp4",
        "audio/m4a",
        "audio/x-m4a",
        "audio/wav",
        "audio/x-wav",
        "audio/aac",
        "audio/x-caf",
        "audio/ogg",
        "audio/opus",
        "application/octet-stream",
    }

    @classmethod
    def shared(
        cls,
    ) -> "AvatarRuntimeSessionService":
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls()

            return cls._instance

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: Dict[
            str,
            StoredRuntimeSession,
        ] = {}

        self.provider_registry = (
            AvatarRuntimeProviderRegistry()
        )
        self.livekit_token_service = (
            AvatarRuntimeLiveKitTokenService()
        )
        self.tavus_adapter = (
            AvatarRuntimeTavusAdapter(
                token_service=(
                    self.livekit_token_service
                )
            )
        )
        self.profile_repository = (
            DigitalHumanProfileRepository()
        )

    async def create_session(
        self,
        request: AvatarRuntimeSessionCreateRequest,
    ) -> AvatarRuntimeSessionResponse:
        now = self._utc_now()

        try:
            digital_human_profile = (
                self.profile_repository
                .require(request.profile_id)
            )
        except DigitalHumanProfileNotFoundError:
            digital_human_profile = None
        except DigitalHumanProfileRepositoryError as error:
            raise AvatarRuntimeProviderUnavailableError(
                "Digital human profile storage is unavailable."
            ) from error

        try:
            _, diagnostics = (
                self.provider_registry.require_provider()
            )
        except RuntimeError as error:
            raise AvatarRuntimeProviderUnavailableError(
                str(error)
            ) from error

        session_id = f"avatar_{uuid4().hex}"

        if (
            digital_human_profile is None
            or not digital_human_profile.has_runtime_avatar
            or digital_human_profile.avatar_provider != "tavus"
            or not digital_human_profile.avatar_replica_id
        ):
            raise AvatarRuntimeProviderUnavailableError(
                "This profile has no production-ready Tavus face."
            )

        try:
            remote = (
                await self.tavus_adapter
                .start_session(
                    session_id=session_id,
                    profile_id=request.profile_id,
                    display_name=request.display_name,
                    face_id=(
                        digital_human_profile
                        .avatar_replica_id
                    ),
                    pal_id=(
                        digital_human_profile
                        .avatar_persona_id
                    ),
                )
            )

            session = AvatarRuntimeSessionResponse(
                session_id=session_id,
                profile_id=request.profile_id,
                provider=AvatarRuntimeProvider.TAVUS,
                transport=AvatarRuntimeTransport.LIVEKIT,
                provider_avatar_id=remote.provider_avatar_id,
                livekit=remote.descriptor,
                created_at=now,
                expires_at=remote.descriptor.expires_at,
                metadata={
                    **diagnostics,
                    **remote.metadata,
                    "provider_resolution":
                        "verified_remote_provider",
                },
            )

        except TavusRuntimeError as error:
            raise AvatarRuntimeProviderUnavailableError(
                "Tavus activation failed: "
                f"{error}"
            ) from error

        with self._lock:
            self._sessions[session_id] = (
                StoredRuntimeSession(
                    session=session
                )
            )

        try:
            self.profile_repository.mark_runtime_verified(
                request.profile_id
            )
        except DigitalHumanProfileRepositoryError:
            await self.tavus_adapter.close_session(
                session.session_id
            )

            with self._lock:
                self._sessions.pop(
                    session.session_id,
                    None,
                )

            raise AvatarRuntimeProviderUnavailableError(
                "The avatar session could not be verified persistently."
            )

        return session

    async def render_speech(
        self,
        session_id: str,
        metadata: AvatarRuntimeSpeechMetadata,
        audio: UploadFile,
    ) -> AvatarRuntimeSpeechResponse:
        started_at = perf_counter()
        stored = self._require_session(session_id)
        session = stored.session

        if metadata.session_id != session.session_id:
            raise AvatarRuntimeConflictError(
                "Speech metadata does not belong to the "
                "requested runtime session."
            )

        if metadata.profile_id != session.profile_id:
            raise AvatarRuntimeConflictError(
                "Speech metadata belongs to another profile."
            )

        self._validate_session_expiration(
            session
        )

        audio_data = await self._read_and_validate_audio(
            audio
        )

        if session.provider == AvatarRuntimeProvider.TAVUS:
            try:
                await self.tavus_adapter.stream_audio(
                    session_id=session.session_id,
                    audio_data=audio_data,
                )
            except (
                TavusRuntimeAudioError,
                TavusRuntimeConnectionError,
            ) as error:
                raise AvatarRuntimeProviderUnavailableError(
                    "Tavus VoiceDNA rendering failed: "
                    f"{error}"
                ) from error

            latency_ms = int(
                (
                    perf_counter()
                    - started_at
                )
                * 1000
            )

            return AvatarRuntimeSpeechResponse(
                request_id=metadata.request_id,
                session_id=session.session_id,
                resolved_provider=(
                    AvatarRuntimeProvider.TAVUS
                ),
                transport=(
                    AvatarRuntimeTransport.LIVEKIT
                ),
                livekit=session.livekit,
                latency_ms=latency_ms,
                created_at=self._utc_now(),
            )

        raise AvatarRuntimeProviderUnavailableError(
            f"Runtime provider '{session.provider.value}' "
            "does not have an active verified media session."
        )

    async def interrupt_session(
        self,
        session_id: str,
    ) -> AvatarRuntimeOperationResponse:
        stored = self._require_session(
            session_id
        )
        self._validate_session_expiration(
            stored.session
        )

        if (
            stored.session.provider
            == AvatarRuntimeProvider.TAVUS
        ):
            try:
                await self.tavus_adapter.interrupt_session(
                    session_id
                )
            except TavusRuntimeConnectionError as error:
                raise AvatarRuntimeProviderUnavailableError(
                    "Tavus interruption failed: "
                    f"{error}"
                ) from error

        interrupted_at = self._utc_now()

        with self._lock:
            self._sessions[session_id] = (
                StoredRuntimeSession(
                    session=stored.session,
                    interrupted_at=interrupted_at,
                )
            )

        return AvatarRuntimeOperationResponse(
            session_id=session_id,
            status="interrupted",
            updated_at=interrupted_at,
        )

    async def close_session(
        self,
        session_id: str,
    ) -> AvatarRuntimeOperationResponse:
        clean_session_id = session_id.strip()

        if not clean_session_id:
            raise AvatarRuntimeValidationError(
                "Runtime session identifier is missing."
            )

        with self._lock:
            stored = self._sessions.get(
                clean_session_id
            )

        if stored is None:
            raise AvatarRuntimeSessionNotFoundError(
                "The avatar runtime session does not exist."
            )

        if (
            stored.session.provider
            == AvatarRuntimeProvider.TAVUS
        ):
            await self.tavus_adapter.close_session(
                clean_session_id
            )

        with self._lock:
            self._sessions.pop(
                clean_session_id,
                None,
            )

        return AvatarRuntimeOperationResponse(
            session_id=clean_session_id,
            status="closed",
            updated_at=self._utc_now(),
        )

    def provider_readiness_snapshot(
        self,
    ) -> Dict[str, Dict[str, object]]:
        snapshot = (
            self.provider_registry
            .readiness_snapshot()
        )

        return {
            provider_name: {
                "provider": readiness.provider.value,
                "enabled": readiness.enabled,
                "configured": readiness.configured,
                "runtime_available": (
                    readiness.runtime_available
                ),
                "selectable": readiness.selectable,
                "missing_configuration": list(
                    readiness.missing_configuration
                ),
                "reason": readiness.reason,
                "worker_name": (
                    self.tavus_adapter.worker_name
                    if readiness.provider
                    == AvatarRuntimeProvider.TAVUS
                    else None
                ),
            }
            for provider_name, readiness
            in snapshot.items()
        }

    async def _read_and_validate_audio(
        self,
        audio: UploadFile,
    ) -> bytes:
        filename = (
            audio.filename or ""
        ).strip()

        if not filename:
            raise AvatarRuntimeValidationError(
                "The uploaded voice artifact has no filename."
            )

        content_type = (
            audio.content_type
            or "application/octet-stream"
        ).lower()

        if (
            content_type
            not in self.supported_audio_content_types
        ):
            raise AvatarRuntimeValidationError(
                "Unsupported voice artifact content type: "
                f"{content_type}"
            )

        data = await audio.read(
            self.max_audio_bytes + 1
        )

        if len(data) > self.max_audio_bytes:
            raise AvatarRuntimeValidationError(
                "The uploaded voice artifact exceeds the "
                "configured size limit."
            )

        if len(data) < 16:
            raise AvatarRuntimeValidationError(
                "The uploaded voice artifact is too small "
                "to be valid."
            )

        return data

    def _require_session(
        self,
        session_id: str,
    ) -> StoredRuntimeSession:
        clean_session_id = session_id.strip()

        if not clean_session_id:
            raise AvatarRuntimeValidationError(
                "Runtime session identifier is missing."
            )

        self._remove_expired_sessions()

        with self._lock:
            stored = self._sessions.get(
                clean_session_id
            )

        if stored is None:
            raise AvatarRuntimeSessionNotFoundError(
                "The avatar runtime session does not exist."
            )

        return stored

    def _validate_session_expiration(
        self,
        session: AvatarRuntimeSessionResponse,
    ) -> None:
        if session.expires_at is None:
            return

        if session.expires_at <= self._utc_now():
            raise AvatarRuntimeSessionNotFoundError(
                "The avatar runtime session has expired."
            )

    def _remove_expired_sessions(self) -> None:
        now = self._utc_now()

        with self._lock:
            expired_sessions = [
                stored.session
                for stored in self._sessions.values()
                if (
                    stored.session.expires_at
                    is not None
                    and stored.session.expires_at <= now
                )
            ]

            if not expired_sessions:
                return

            for session in expired_sessions:
                self._sessions.pop(
                    session.session_id,
                    None,
                )

        for session in expired_sessions:
            if (
                session.provider
                == AvatarRuntimeProvider.TAVUS
            ):
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        self.tavus_adapter.close_session(
                            session.session_id
                        )
                    )
                except RuntimeError:
                    pass

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)
