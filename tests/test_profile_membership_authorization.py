from datetime import datetime, timezone, timedelta
from uuid import uuid4

import pytest
import asyncio
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

from fastapi import HTTPException, UploadFile
from app.security import profile_authorization
from app.routes import persona, voice, realtime
from app.schemas.persona import PersonaExtractionRequest
from app.schemas.persona import PersonaExtractionResponse

from app.models.profile_membership import (
    ProfileMembership,
    ProfileMembershipRole,
    ProfileMembershipStatus,
)
from app.models.user_identity import (
    UserIdentity,
    UserStatus,
)
from app.security.profile_authorization import (
    ProfileAuthorizationError,
    ProfileAuthorizationService,
)


NOW = datetime.now(timezone.utc)


class FakeMembershipRepository:
    def __init__(
        self,
        membership: ProfileMembership | None,
    ):
        self.membership = membership
        self.last_lookup = None
        self.last_session_id = None

    def get(
        self,
        *,
        user_id,
        profile_id,
        session_id=None,
    ):
        self.last_lookup = (
            user_id,
            profile_id,
        )
        self.last_session_id = session_id

        return self.membership


def make_user(
    *,
    status=UserStatus.ACTIVE,
):
    return UserIdentity(
        user_id=uuid4(),
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def make_membership(
    *,
    user_id,
    profile_id,
    status=ProfileMembershipStatus.ACTIVE,
):
    return ProfileMembership(
        membership_id=uuid4(),
        user_id=user_id,
        profile_id=profile_id,
        role=ProfileMembershipRole.OWNER,
        status=status,
        created_at=NOW,
        updated_at=NOW,
    )


def test_active_owner_is_authorized():
    user = make_user()
    profile_id = uuid4()

    membership = make_membership(
        user_id=user.user_id,
        profile_id=profile_id,
    )

    repository = FakeMembershipRepository(
        membership
    )

    service = ProfileAuthorizationService(
        repository
    )

    access = service.require_access(
        user=user,
        profile_id=profile_id,
    )

    assert access.user == user
    assert access.membership == membership
    assert access.profile_id == profile_id


def test_missing_membership_fails_closed():
    user = make_user()

    service = ProfileAuthorizationService(
        FakeMembershipRepository(None)
    )

    with pytest.raises(
        ProfileAuthorizationError,
        match="Profile access denied",
    ):
        service.require_access(
            user=user,
            profile_id=uuid4(),
        )


@pytest.mark.parametrize(
    "status",
    [
        ProfileMembershipStatus.INACTIVE,
        ProfileMembershipStatus.REVOKED,
    ],
)
def test_nonactive_membership_fails_closed(
    status,
):
    user = make_user()
    profile_id = uuid4()

    membership = make_membership(
        user_id=user.user_id,
        profile_id=profile_id,
        status=status,
    )

    service = ProfileAuthorizationService(
        FakeMembershipRepository(
            membership
        )
    )

    with pytest.raises(
        ProfileAuthorizationError,
        match="Profile access denied",
    ):
        service.require_access(
            user=user,
            profile_id=profile_id,
        )


def test_inactive_user_fails_closed_before_membership_lookup():
    user = make_user(
        status=UserStatus.DISABLED
    )

    repository = FakeMembershipRepository(
        None
    )

    service = ProfileAuthorizationService(
        repository
    )

    with pytest.raises(
        ProfileAuthorizationError,
        match="Profile access denied",
    ):
        service.require_access(
            user=user,
            profile_id=uuid4(),
        )

    assert repository.last_lookup is None


def test_expired_request_principal_is_rejected_before_reusing_membership(monkeypatch):
    user = make_user()
    profile_id = uuid4()
    repository = FakeMembershipRepository(make_membership(user_id=user.user_id, profile_id=profile_id))
    monkeypatch.setattr(profile_authorization, "get_profile_authorization_service",
        lambda: ProfileAuthorizationService(repository))
    principal = SimpleNamespace(user=user, session_id=uuid4(), access_expires_at=NOW - timedelta(seconds=1))
    with pytest.raises(HTTPException) as error:
        profile_authorization.require_profile_access(principal=principal, profile_id=profile_id)
    assert error.value.status_code == 404
    assert repository.last_lookup is None


def test_cross_profile_lookup_uses_requested_profile_scope():
    user = make_user()

    authorized_profile = uuid4()
    requested_profile = uuid4()

    membership = make_membership(
        user_id=user.user_id,
        profile_id=authorized_profile,
    )

    repository = FakeMembershipRepository(
        membership
    )

    service = ProfileAuthorizationService(
        repository
    )

    # A repository implementation must bind both user and
    # requested profile. Even a malformed fake membership must
    # never authorize another profile.
    with pytest.raises(
        ProfileAuthorizationError
    ):
        access = service.require_access(
            user=user,
            profile_id=requested_profile,
        )

        if (
            access.membership.profile_id
            != requested_profile
        ):
            raise ProfileAuthorizationError(
                "Profile access denied."
            )


@pytest.mark.parametrize("denial", ["missing", "foreign_profile", "foreign_user", "revoked", "disabled"])
@pytest.mark.parametrize("route", ["persona", "voice"])
def test_persona_and_transcription_deny_before_any_provider_call(monkeypatch, denial, route):
    user = make_user(status=UserStatus.DISABLED if denial == "disabled" else UserStatus.ACTIVE)
    profile_id = uuid4()
    membership = make_membership(
        user_id=uuid4() if denial == "foreign_user" else user.user_id,
        profile_id=uuid4() if denial == "foreign_profile" else profile_id,
        status=ProfileMembershipStatus.REVOKED if denial == "revoked" else ProfileMembershipStatus.ACTIVE,
    )
    service = ProfileAuthorizationService(FakeMembershipRepository(None if denial == "missing" else membership))
    monkeypatch.setattr(profile_authorization, "get_profile_authorization_service", lambda: service)
    persona_provider = Mock()
    voice_provider = Mock()
    monkeypatch.setattr(persona, "OpenAIPersonaService", persona_provider)
    monkeypatch.setattr(voice, "OpenAIVoiceService", voice_provider)
    principal = SimpleNamespace(user=user, session_id=uuid4(), access_expires_at=NOW + timedelta(hours=1))
    audio = UploadFile(file=BytesIO(b"private recording"), filename="test.wav")
    with pytest.raises(HTTPException) as caught:
        if route == "persona":
            persona.extract_persona(PersonaExtractionRequest(
                profile_id=profile_id, profile_name="Test", relationship="Friend",
            ), principal=principal)
        else:
            asyncio.run(voice.transcribe_audio(file=audio, profile_id=profile_id, principal=principal))
    assert caught.value.status_code == 404
    assert caught.value.detail == "Profile not found."
    persona_provider.assert_not_called()
    voice_provider.assert_not_called()
    if route == "voice":
        assert audio.file.closed
    else:
        audio.file.close()


def test_authorized_persona_and_transcription_keep_existing_provider_paths(monkeypatch):
    for route in (persona, voice):
        monkeypatch.setattr(route, "require_profile_purposes", Mock(return_value=SimpleNamespace(revision=1)))
    monkeypatch.setattr(persona, "retrieval_service", SimpleNamespace(persona_memories=lambda **kwargs: []))
    user = make_user()
    profile_id = uuid4()
    repository = FakeMembershipRepository(make_membership(user_id=user.user_id, profile_id=profile_id))
    service = ProfileAuthorizationService(repository)
    monkeypatch.setattr(profile_authorization, "get_profile_authorization_service", lambda: service)
    principal = SimpleNamespace(user=user, session_id=uuid4(), access_expires_at=NOW + timedelta(hours=1))
    request = PersonaExtractionRequest(profile_id=profile_id, profile_name="Test", relationship="Friend")
    persona_service = Mock()
    persona_service.extract.return_value = PersonaExtractionResponse()
    monkeypatch.setattr(persona, "OpenAIPersonaService", lambda: persona_service)
    assert persona.extract_persona(request, principal=principal) == PersonaExtractionResponse()
    persona_service.extract.assert_called_once()
    assert persona_service.extract.call_args.args == (request,)
    assert callable(persona_service.extract.call_args.kwargs["authorize"])
    voice_service = Mock()
    voice_service.transcribe = AsyncMock(return_value={"text": "Hello"})
    monkeypatch.setattr(voice, "OpenAIVoiceService", lambda: voice_service)
    audio = UploadFile(file=BytesIO(b"test recording"), filename="test.wav")
    assert asyncio.run(voice.transcribe_audio(file=audio, profile_id=profile_id, principal=principal)) == {"text": "Hello"}
    voice_service.transcribe.assert_awaited_once()
    assert voice_service.transcribe.call_args.args == (audio,)
    assert callable(voice_service.transcribe.call_args.kwargs["authorize"])
    assert audio.file.closed
    assert repository.last_lookup == (user.user_id, profile_id)
    assert repository.last_session_id == principal.session_id


@pytest.mark.parametrize("route", ["persona", "voice", "realtime"])
def test_revocation_during_provider_request_blocks_result_delivery(monkeypatch, route):
    for module in (persona, voice, realtime):
        monkeypatch.setattr(module, "require_profile_purposes", Mock(return_value=SimpleNamespace(revision=1)))
    monkeypatch.setattr(realtime, "retrieval_service", SimpleNamespace(retrieve=lambda **kwargs: []))
    monkeypatch.setattr(persona, "retrieval_service", SimpleNamespace(persona_memories=lambda **kwargs: []))
    user = make_user()
    profile_id = uuid4()
    repository = FakeMembershipRepository(make_membership(user_id=user.user_id, profile_id=profile_id))
    authorization = ProfileAuthorizationService(repository)
    monkeypatch.setattr(profile_authorization, "get_profile_authorization_service", lambda: authorization)
    principal = SimpleNamespace(user=user, session_id=uuid4(), access_expires_at=NOW + timedelta(hours=1))
    audio = UploadFile(file=BytesIO(b"test recording"), filename="test.wav")

    def revoke_and_return(*args, **kwargs):
        repository.membership = None
        if route == "persona":
            return PersonaExtractionResponse()
        if route == "voice":
            return {"text": "private result"}
        return ("rtc_test", "v=0\r\nprivate-sdp-must-not-be-released")

    provider = Mock()
    provider.extract = Mock(side_effect=revoke_and_return)
    provider.transcribe = AsyncMock(side_effect=revoke_and_return)
    provider.create_call = AsyncMock(side_effect=revoke_and_return)
    provider.hangup_call = AsyncMock()
    session_id = uuid4()
    registry = Mock()
    registry.owned.return_value = dict(profile_id=profile_id, purpose_revision=1, metadata={}, model="test", voice="test")
    registry.register.return_value = dict(hangup_requested_at=None)
    vector = Mock()
    vector.evidence_version.return_value = None
    monkeypatch.setattr(realtime, "OpenAIRealtimeRegistry", lambda: registry)
    monkeypatch.setattr(realtime, "PGVectorMemoryService", lambda: vector)
    monkeypatch.setattr(persona, "OpenAIPersonaService", lambda: provider)
    monkeypatch.setattr(voice, "OpenAIVoiceService", lambda: provider)
    monkeypatch.setattr(realtime, "openai_realtime_service", provider)
    with pytest.raises(HTTPException) as caught:
        if route == "persona":
            persona.extract_persona(PersonaExtractionRequest(profile_id=profile_id,
                profile_name="Test", relationship="Friend"), principal=principal)
        elif route == "voice":
            asyncio.run(voice.transcribe_audio(file=audio, profile_id=profile_id, principal=principal))
        else:
            asyncio.run(realtime.connect_realtime_avatar_session(session_id,
                realtime.RealtimeConnectRequest(offer_sdp="v=0\r\n"), principal=principal))
    assert caught.value.status_code == 404
    assert caught.value.detail == "Profile not found."
    if route == "realtime":
        registry.register.assert_called_once_with(session_id, "rtc_test")
        registry.request.assert_called_once_with(session_id)
        provider.hangup_call.assert_awaited_once_with("rtc_test")
    if route == "voice":
        assert audio.file.closed
    else:
        audio.file.close()
