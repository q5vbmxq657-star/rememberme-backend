from __future__ import annotations

import asyncio
import json
import os
import threading

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
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
    Provider-neutral avatar runtime gateway.

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

    session_ttl_seconds = int(
        os.getenv(
            "AVATAR_RUNTIME_SESSION_TTL_SECONDS",
            str(12 * 60 * 60),
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
        storage_root = Path(
            os.getenv(
                "AVATAR_RUNTIME_STORAGE_ROOT",
                str(
                    Path.home()
                    / ".remembermeai"
                    / "avatar-runtime"
                ),
            )
        ).expanduser()

        storage_root.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.storage_root = storage_root
        self.session_store_path = (
            storage_root / "sessions.json"
        )
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

        self._load_sessions()
        self._remove_expired_sessions()

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

        allow_tavus_fallback = (
            request.fallback_enabled
            and request.allow_tavus_fallback
        )
        allow_local_fallback = (
            request.fallback_enabled
            and request.allow_local_fallback
        )

        try:
            (
                provider,
                fallback_providers,
                diagnostics,
            ) = self.provider_registry.select_provider(
                request.preferred_providers,
                allow_tavus_fallback=(
                    allow_tavus_fallback
                ),
                allow_local_fallback=(
                    allow_local_fallback
                ),
            )
        except RuntimeError as error:
            raise AvatarRuntimeProviderUnavailableError(
                str(error)
            ) from error

        session_id = f"avatar_{uuid4().hex}"

        if provider == AvatarRuntimeProvider.LOCAL:
            session = self._make_local_session(
                request=request,
                session_id=session_id,
                fallback_providers=(
                    fallback_providers
                ),
                diagnostics=diagnostics,
                now=now,
            )
        elif provider == AvatarRuntimeProvider.TAVUS:
            if (
                digital_human_profile is None
                or not digital_human_profile.has_runtime_avatar
                or digital_human_profile.avatar_provider != "tavus"
                or not digital_human_profile.avatar_replica_id
                or not digital_human_profile.avatar_persona_id
            ):
                if allow_local_fallback:
                    session = self._make_local_session(
                        request=request,
                        session_id=session_id,
                        fallback_providers=[
                            candidate
                            for candidate in fallback_providers
                            if candidate != AvatarRuntimeProvider.LOCAL
                        ],
                        diagnostics={
                            **diagnostics,
                            "profile_resolution": (
                                "No production-ready Tavus identity "
                                "is stored for this profile."
                            ),
                        },
                        now=now,
                    )
                else:
                    raise AvatarRuntimeProviderUnavailableError(
                        "This profile has no production-ready avatar."
                    )
            else:
                try:
                    remote = (
                        await self.tavus_adapter
                        .start_session(
                            session_id=session_id,
                            profile_id=request.profile_id,
                            display_name=(
                                request.display_name
                            ),
                            replica_id=(
                                digital_human_profile
                                .avatar_replica_id
                            ),
                            persona_id=(
                                digital_human_profile
                                .avatar_persona_id
                            ),
                        )
                    )

                    session = AvatarRuntimeSessionResponse(
                            session_id=session_id,
                            profile_id=request.profile_id,
                            provider=(
                                AvatarRuntimeProvider.TAVUS
                            ),
                        transport=(
                            AvatarRuntimeTransport.LIVEKIT
                        ),
                        provider_avatar_id=(
                            remote.provider_avatar_id
                        ),
                        livekit=remote.descriptor,
                        preview_video_url=None,
                        created_at=now,
                        expires_at=remote.descriptor.expires_at,
                        fallback_providers=(
                            fallback_providers
                        ),
                        metadata={
                            **remote.metadata,
                            "provider_resolution":
                                "verified_remote_provider",
                        },
                    )

                except TavusRuntimeError as error:
                    if allow_local_fallback:
                        clean_fallbacks = [
                            candidate
                            for candidate
                            in fallback_providers
                            if candidate
                            != AvatarRuntimeProvider.LOCAL
                        ]

                        session = self._make_local_session(
                            request=request,
                            session_id=session_id,
                            fallback_providers=(
                                clean_fallbacks
                            ),
                            diagnostics={
                                **diagnostics,
                                "remote_activation": (
                                    "Tavus activation failed closed. "
                                    f"{type(error).__name__}: {error}"
                                ),
                            },
                            now=now,
                        )
                    else:
                        raise (
                            AvatarRuntimeProviderUnavailableError(
                                "Tavus activation failed and "
                                "local fallback is disabled: "
                                f"{error}"
                            )
                        ) from error
        else:
            raise AvatarRuntimeProviderUnavailableError(
                f"Provider '{provider.value}' has no "
                "active production adapter."
            )

        with self._lock:
            self._sessions[session_id] = (
                StoredRuntimeSession(
                    session=session
                )
            )
            self._persist_sessions()

        if (
            session.provider
            == AvatarRuntimeProvider.TAVUS
        ):
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
                    self._persist_sessions()

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

        if session.provider == AvatarRuntimeProvider.LOCAL:
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
                    AvatarRuntimeProvider.LOCAL
                ),
                transport=(
                    AvatarRuntimeTransport.LOCAL_AVATAR
                ),
                livekit=None,
                video_url=None,
                fallback_used=True,
                latency_ms=latency_ms,
                created_at=self._utc_now(),
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
                video_url=None,
                fallback_used=False,
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
            self._persist_sessions()

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
            self._persist_sessions()

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

    def _make_local_session(
        self,
        *,
        request: AvatarRuntimeSessionCreateRequest,
        session_id: str,
        fallback_providers: list[
            AvatarRuntimeProvider
        ],
        diagnostics: Dict[str, str],
        now: datetime,
    ) -> AvatarRuntimeSessionResponse:
        clean_fallbacks = [
            provider
            for provider in fallback_providers
            if provider != AvatarRuntimeProvider.LOCAL
        ]

        metadata = {
            "runtime_version": "3",
            "session_mode": "provider_neutral",
            "audio_ownership":
                "ios_voice_dna_pipeline",
            "provider_resolution":
                "local_fallback",
            "remote_session_verified": "false",
        }

        for provider_name, message in diagnostics.items():
            metadata[
                f"provider_{provider_name}"
            ] = message[:500]

        return AvatarRuntimeSessionResponse(
            session_id=session_id,
            profile_id=request.profile_id,
            provider=AvatarRuntimeProvider.LOCAL,
            transport=(
                AvatarRuntimeTransport.LOCAL_AVATAR
            ),
            provider_avatar_id=None,
            livekit=None,
            preview_video_url=None,
            created_at=now,
            expires_at=(
                now
                + timedelta(
                    seconds=self.session_ttl_seconds
                )
            ),
            fallback_providers=clean_fallbacks,
            metadata=metadata,
        )

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

            self._persist_sessions()

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

    def _load_sessions(self) -> None:
        if not self.session_store_path.exists():
            self._sessions = {}
            return

        try:
            raw_data = json.loads(
                self.session_store_path.read_text(
                    encoding="utf-8"
                )
            )

            loaded: Dict[
                str,
                StoredRuntimeSession,
            ] = {}

            for item in raw_data.get(
                "sessions",
                [],
            ):
                session = (
                    AvatarRuntimeSessionResponse
                    .model_validate(
                        item["session"]
                    )
                )

                if (
                    session.provider
                    != AvatarRuntimeProvider.LOCAL
                ):
                    continue

                interrupted_at_raw = item.get(
                    "interrupted_at"
                )
                interrupted_at = (
                    datetime.fromisoformat(
                        interrupted_at_raw
                    )
                    if interrupted_at_raw
                    else None
                )

                loaded[session.session_id] = (
                    StoredRuntimeSession(
                        session=session,
                        interrupted_at=interrupted_at,
                    )
                )

            self._sessions = loaded

        except Exception:
            corrupted_path = (
                self.session_store_path
                .with_suffix(
                    ".corrupted-"
                    f"{uuid4().hex}.json"
                )
            )

            try:
                self.session_store_path.replace(
                    corrupted_path
                )
            except OSError:
                pass

            self._sessions = {}

    def _persist_sessions(self) -> None:
        payload = {
            "version": 3,
            "sessions": [
                {
                    "session": (
                        stored.session
                        .model_dump(
                            mode="json"
                        )
                    ),
                    "interrupted_at": (
                        stored.interrupted_at
                        .isoformat()
                        if stored.interrupted_at
                        else None
                    ),
                }
                for stored
                in self._sessions.values()
            ],
        }

        temporary_path = (
            self.session_store_path
            .with_suffix(".tmp")
        )

        temporary_path.write_text(
            json.dumps(
                payload,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

        temporary_path.replace(
            self.session_store_path
        )

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)
