from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from livekit import api


class AvatarLiveKitConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AvatarLiveKitClientToken:
    token: str
    room_name: str
    participant_identity: str
    expires_at: datetime


@dataclass(frozen=True)
class AvatarLiveKitBridgeToken:
    token: str
    room_name: str
    participant_identity: str
    expires_at: datetime


@dataclass(frozen=True)
class AvatarLiveKitAvatarToken:
    token: str
    room_name: str
    avatar_identity: str
    publishing_for_identity: str
    expires_at: datetime


class AvatarRuntimeLiveKitTokenService:
    """
    Generates restricted LiveKit room tokens.

    LiveKit API credentials remain exclusively on the backend. The iOS client
    receives only a room-scoped participant token.
    """

    publish_on_behalf_attribute = "lk.publish_on_behalf"

    def __init__(
        self,
        *,
        server_url: Optional[str] = None,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        token_ttl_seconds: Optional[int] = None,
    ) -> None:
        self.server_url = self._clean(
            server_url or os.getenv("LIVEKIT_URL")
        )
        self.api_key = self._clean(
            api_key or os.getenv("LIVEKIT_API_KEY")
        )
        self.api_secret = self._clean(
            api_secret or os.getenv("LIVEKIT_API_SECRET")
        )

        configured_ttl = (
            token_ttl_seconds
            if token_ttl_seconds is not None
            else int(
                os.getenv(
                    "AVATAR_RUNTIME_LIVEKIT_TOKEN_TTL_SECONDS",
                    "3600",
                )
            )
        )

        self.token_ttl_seconds = max(
            300,
            min(configured_ttl, 24 * 60 * 60),
        )

    @property
    def is_configured(self) -> bool:
        return all(
            (
                self.server_url,
                self.api_key,
                self.api_secret,
            )
        )

    def create_client_token(
        self,
        *,
        session_id: str,
        profile_id: UUID,
        display_name: str,
        room_name: Optional[str] = None,
    ) -> AvatarLiveKitClientToken:
        self._require_configuration()

        resolved_room_name = (
            self._clean(room_name)
            or self.room_name_for_session(session_id)
        )
        participant_identity = f"rememberme-ios-{profile_id}"
        clean_display_name = (
            self._clean(display_name)
            or "RememberMeAI"
        )
        expires_at = self._expiration_date()

        token = (
            api.AccessToken(
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
            .with_identity(participant_identity)
            .with_name(clean_display_name)
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=resolved_room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_ttl(
                timedelta(seconds=self.token_ttl_seconds)
            )
            .to_jwt()
        )

        return AvatarLiveKitClientToken(
            token=token,
            room_name=resolved_room_name,
            participant_identity=participant_identity,
            expires_at=expires_at,
        )

    def create_bridge_token(
        self,
        *,
        session_id: str,
        room_name: Optional[str] = None,
    ) -> AvatarLiveKitBridgeToken:
        self._require_configuration()

        resolved_room_name = (
            self._clean(room_name)
            or self.room_name_for_session(session_id)
        )
        participant_identity = self.bridge_identity_for_session(
            session_id
        )
        expires_at = self._expiration_date()

        token = (
            api.AccessToken(
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
            .with_kind("agent")
            .with_identity(participant_identity)
            .with_name("RememberMeAI VoiceDNA Bridge")
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=resolved_room_name,
                    can_publish=False,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_ttl(
                timedelta(seconds=self.token_ttl_seconds)
            )
            .to_jwt()
        )

        return AvatarLiveKitBridgeToken(
            token=token,
            room_name=resolved_room_name,
            participant_identity=participant_identity,
            expires_at=expires_at,
        )

    def create_avatar_token(
        self,
        *,
        session_id: str,
        publishing_for_identity: str,
        avatar_identity: Optional[str] = None,
        room_name: Optional[str] = None,
    ) -> AvatarLiveKitAvatarToken:
        self._require_configuration()

        resolved_room_name = (
            self._clean(room_name)
            or self.room_name_for_session(session_id)
        )
        resolved_avatar_identity = (
            self._clean(avatar_identity)
            or self.avatar_identity_for_session(session_id)
        )
        clean_publishing_identity = self._clean(
            publishing_for_identity
        )

        if clean_publishing_identity is None:
            raise ValueError(
                "publishing_for_identity must not be empty"
            )

        expires_at = self._expiration_date()

        token = (
            api.AccessToken(
                api_key=self.api_key,
                api_secret=self.api_secret,
            )
            .with_kind("agent")
            .with_identity(resolved_avatar_identity)
            .with_name("RememberMeAI Tavus Avatar")
            .with_grants(
                api.VideoGrants(
                    room_join=True,
                    room=resolved_room_name,
                    can_publish=True,
                    can_subscribe=True,
                    can_publish_data=True,
                )
            )
            .with_attributes(
                {
                    self.publish_on_behalf_attribute:
                        clean_publishing_identity
                }
            )
            .with_ttl(
                timedelta(seconds=self.token_ttl_seconds)
            )
            .to_jwt()
        )

        return AvatarLiveKitAvatarToken(
            token=token,
            room_name=resolved_room_name,
            avatar_identity=resolved_avatar_identity,
            publishing_for_identity=clean_publishing_identity,
            expires_at=expires_at,
        )

    @staticmethod
    def room_name_for_session(session_id: str) -> str:
        clean_session_id = (
            session_id
            .strip()
            .replace("/", "-")
            .replace(" ", "-")
        )

        if not clean_session_id:
            raise ValueError(
                "session_id must not be empty"
            )

        return f"rememberme-avatar-{clean_session_id}"

    @staticmethod
    def avatar_identity_for_session(session_id: str) -> str:
        clean_session_id = (
            session_id
            .strip()
            .replace("/", "-")
            .replace(" ", "-")
        )

        if not clean_session_id:
            raise ValueError(
                "session_id must not be empty"
            )

        return f"rememberme-tavus-{clean_session_id}"

    @staticmethod
    def bridge_identity_for_session(session_id: str) -> str:
        clean_session_id = (
            session_id
            .strip()
            .replace("/", "-")
            .replace(" ", "-")
        )

        if not clean_session_id:
            raise ValueError(
                "session_id must not be empty"
            )

        return f"rememberme-voice-bridge-{clean_session_id}"

    def _require_configuration(self) -> None:
        missing = []

        if self.server_url is None:
            missing.append("LIVEKIT_URL")

        if self.api_key is None:
            missing.append("LIVEKIT_API_KEY")

        if self.api_secret is None:
            missing.append("LIVEKIT_API_SECRET")

        if missing:
            raise AvatarLiveKitConfigurationError(
                "Missing LiveKit configuration: "
                + ", ".join(missing)
            )

    def _expiration_date(self) -> datetime:
        return self._utc_now() + timedelta(
            seconds=self.token_ttl_seconds
        )

    @staticmethod
    def _clean(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        cleaned = value.strip()
        return cleaned or None

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)
