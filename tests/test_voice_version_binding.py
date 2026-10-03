import asyncio
from dataclasses import replace
from io import BytesIO
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from test_elevenlabs_voice_fallback_contract import (
    FakeRepository, profile_with_personalized_voice, service_with, valid_voice_consent,
)
from app.services.elevenlabs_voice_service import ElevenLabsVoiceConflictError, ElevenLabsVoiceProviderError


def setup_version():
    profile = replace(profile_with_personalized_voice(), voice_training_job_id=str(uuid4()))
    repository = FakeRepository(profile)
    previous = uuid4()
    job = {"job_id": previous, "profile_id": profile.profile_id, "training_type": "voice",
        "provider": "elevenlabs", "status": "ready", "provider_job_id": "voice-A",
        "provider_payload": {"_stay_activated": True}, "request_payload": {"_stay_consent_revision": 7}}
    repository.get_training_job = Mock(return_value=job)
    service = service_with(repository)
    service.synthesize = AsyncMock(return_value=BytesIO(b"audio"))
    return profile, repository, previous, job, service


def test_running_call_retains_previously_activated_voice(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()
    result = asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version=str(previous)))
    assert result.voice_mode == "personalized"
    service.synthesize.assert_awaited_once_with(text="Hello", voice_id="voice-A", delivery=None)
    assert repository.get_training_job.call_count == 3


def test_new_call_uses_current_voice_without_historical_lookup(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()
    asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version=profile.voice_training_job_id))
    service.synthesize.assert_awaited_once_with(text="Hello", voice_id="personalized-id", delivery=None)
    repository.get_training_job.assert_not_called()


def test_generic_call_does_not_switch_when_a_clone_becomes_ready(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()
    result = asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version="generic"))
    assert result.voice_mode == "warm_default"
    service.synthesize.assert_awaited_once_with(text="Hello", voice_id="generic-id", delivery=None)


def test_generic_provider_hang_is_cancelled_for_native_fallback(valid_voice_consent, monkeypatch):
    profile, repository, previous, job, service = setup_version()
    timeout = asyncio.timeout
    deadlines = []
    cancelled = []

    def short_timeout(seconds):
        deadlines.append(seconds)
        return timeout(.01)

    async def hang(**kwargs):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)

    monkeypatch.setattr(asyncio, "timeout", short_timeout)
    service.synthesize.side_effect = hang
    with pytest.raises(ElevenLabsVoiceProviderError) as error:
        asyncio.run(service.synthesize_for_profile(
            profile_id=profile.profile_id, text="Hello", voice_version="generic"))
    assert error.value.status_code == 504
    assert deadlines == [8]
    assert cancelled == [True]
    assert valid_voice_consent.call_args.kwargs["expected_revision"] == 7


def test_generic_timeout_rechecks_revocation_before_fallback(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()

    async def timeout_after_revocation(**kwargs):
        valid_voice_consent.side_effect = PermissionError("Consent revoked")
        raise TimeoutError()

    service.synthesize.side_effect = timeout_after_revocation
    with pytest.raises(PermissionError, match="Consent revoked"):
        asyncio.run(service.synthesize_for_profile(
            profile_id=profile.profile_id, text="Hello", voice_version="generic"))


def test_generic_timeout_never_swallows_call_cancellation(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()
    service.synthesize.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.synthesize_for_profile(
            profile_id=profile.profile_id, text="Hello", voice_version="generic"))


@pytest.mark.parametrize("field,value", [("profile_id", uuid4()), ("provider", "other"),
    ("training_type", "avatar"), ("status", "deleted"), ("provider_job_id", None),
    ("provider_payload", {}), ("provider_payload", {"_stay_activated": "true"}),
    ("request_payload", {})])
def test_unactivated_deleted_or_foreign_version_cannot_be_used(valid_voice_consent, field, value):
    profile, repository, previous, job, service = setup_version()
    job[field] = value
    with pytest.raises(ElevenLabsVoiceConflictError):
        asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version=str(previous)))
    service.synthesize.assert_not_awaited()


def test_version_deleted_during_synthesis_is_not_delivered(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()
    async def generate(**kwargs):
        job["status"] = "deleted"
        return BytesIO(b"audio")
    service.synthesize.side_effect = generate
    with pytest.raises(ElevenLabsVoiceConflictError):
        asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version=str(previous)))


@pytest.mark.parametrize("code", [404, 410, 429, 503])
def test_pinned_call_never_silently_falls_back(valid_voice_consent, code):
    profile, repository, previous, job, service = setup_version()
    service.synthesize.side_effect = ElevenLabsVoiceProviderError("upstream failure", status_code=code)
    with pytest.raises(ElevenLabsVoiceProviderError):
        asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version=str(previous)))
    assert service.synthesize.await_count == 1
    assert repository.voice_updates == []


def test_deleted_current_voice_updates_status_without_substituting_audio(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()
    service.synthesize.side_effect = ElevenLabsVoiceProviderError("missing", status_code=404)
    with pytest.raises(ElevenLabsVoiceProviderError):
        asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version=profile.voice_training_job_id))
    assert service.synthesize.await_count == 1
    assert repository.voice_updates[-1][1]["status"] == "failed"


def test_activation_during_audio_generation_preserves_the_original_voice(valid_voice_consent):
    profile, repository, previous, job, service = setup_version()
    version = str(previous)
    repository.profile = replace(profile, voice_id="voice-A", voice_training_job_id=version)
    async def activate_new(**kwargs):
        assert kwargs["voice_id"] == "voice-A"
        repository.profile = profile
        return BytesIO(b"original voice")
    service.synthesize.side_effect = activate_new
    result = asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello", voice_version=version))
    assert result.audio_stream.read() == b"original voice"


@pytest.mark.parametrize("version", [None, "current"])
def test_self_hosted_voice_never_falls_back_to_elevenlabs(valid_voice_consent, version):
    profile, repository, previous, job, service = setup_version()
    repository.profile = replace(profile, voice_provider="stay_voice")
    with pytest.raises(ElevenLabsVoiceConflictError):
        asyncio.run(service.synthesize_for_profile(profile_id=profile.profile_id, text="Hello",
            voice_version=profile.voice_training_job_id if version else None))
    service.synthesize.assert_not_awaited()
