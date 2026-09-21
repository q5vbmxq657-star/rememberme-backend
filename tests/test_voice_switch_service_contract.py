import asyncio
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest

from app.services.elevenlabs_voice_service import (
    ElevenLabsVoiceProviderError, ElevenLabsVoiceService, VoiceCloneSample,
)
from test_elevenlabs_voice_fallback_contract import valid_voice_consent
from test_elevenlabs_voice_training_contract import (
    ReplacementProviderClient, replacement_service, submit_sample,
)


def test_noise_processing_changes_training_identity():
    service = object.__new__(ElevenLabsVoiceService)
    args = dict(profile_id=uuid4(), idempotency_key="same-selection",
                samples=[VoiceCloneSample("voice.m4a", "audio/m4a", b"voice data")])
    assert service._request_hash(**args) != service._request_hash(**args, remove_background_noise=True)


def test_unconfirmed_creation_retains_external_identity_without_activation(monkeypatch, valid_voice_consent):
    repository, service = replacement_service(monkeypatch)

    async def incomplete(self, url, **kwargs):
        return httpx.Response(200, json={"voice_id": "known-but-unverified"})

    monkeypatch.setattr(ReplacementProviderClient, "post", incomplete)
    with pytest.raises(ElevenLabsVoiceProviderError):
        submit_sample(service, asset_id="new", audio=b"clear voice sample")
    job = next(iter(repository.jobs.values()))
    assert job["provider_job_id"] == "known-but-unverified"
    assert job["status"] == "submitted"
    assert repository.profile.voice_id is None
    assert service.status_for_profile(repository.profile.profile_id)["pending_training"]["status"] == "submitted"


def test_failed_replacement_status_separates_active_voice_and_request(monkeypatch, valid_voice_consent):
    repository, service = replacement_service(monkeypatch)
    active = submit_sample(service, asset_id="first", audio=b"first voice sample")

    async def rejected(self, url, **kwargs):
        return httpx.Response(422, json={"detail": {"status": "invalid_audio"}})

    monkeypatch.setattr(ReplacementProviderClient, "post", rejected)
    with pytest.raises(ElevenLabsVoiceProviderError):
        submit_sample(service, asset_id="second", audio=b"second voice sample")
    status = service.status_for_profile(repository.profile.profile_id)
    assert status["voice_ready"] is True
    assert status["active_voice_version"] == str(active.job_id)
    assert status["pending_training"]["status"] == "failed"
    assert repository.profile.voice_id == active.voice_id
    assert "voice_id" not in status


def test_repeated_submitted_request_with_known_id_never_reposts(monkeypatch, valid_voice_consent):
    repository, service = replacement_service(monkeypatch)
    post = Mock(return_value=httpx.Response(200, json={"voice_id": "known-unconfirmed"}))

    async def incomplete(self, url, **kwargs):
        return post()

    monkeypatch.setattr(ReplacementProviderClient, "post", incomplete)
    with pytest.raises(ElevenLabsVoiceProviderError):
        submit_sample(service, asset_id="new", audio=b"clear voice sample")
    result = submit_sample(service, asset_id="new", audio=b"clear voice sample")
    assert result.status == "submitted"
    assert post.call_count == 1


@pytest.mark.parametrize("code", [302, 408, 409, 500, 503])
def test_ambiguous_provider_response_remains_unconfirmed(monkeypatch, valid_voice_consent, code):
    repository, service = replacement_service(monkeypatch)

    async def ambiguous(self, url, **kwargs):
        return httpx.Response(code, json={})

    monkeypatch.setattr(ReplacementProviderClient, "post", ambiguous)
    with pytest.raises(ElevenLabsVoiceProviderError):
        submit_sample(service, asset_id="new", audio=b"clear voice sample")
    assert next(iter(repository.jobs.values()))["status"] == "submitted"


def test_repository_work_runs_outside_async_event_loop(monkeypatch, valid_voice_consent):
    repository, service = replacement_service(monkeypatch)
    for method in ("ensure", "create_training_job", "begin_voice_training", "apply_voice_training_result"):
        original = getattr(repository, method)

        def checked(*args, _original=original, **kwargs):
            with pytest.raises(RuntimeError):
                asyncio.get_running_loop()
            return _original(*args, **kwargs)

        monkeypatch.setattr(repository, method, checked)
    assert submit_sample(service, asset_id="new", audio=b"clear voice sample").status == "ready"


def test_unsubmitted_persisted_request_can_resume_via_exclusive_claim(monkeypatch, valid_voice_consent):
    repository, service = replacement_service(monkeypatch)
    create = repository.create_training_job

    def recovered(**kwargs):
        return {**create(**kwargs), "was_created": False}

    monkeypatch.setattr(repository, "create_training_job", recovered)
    assert submit_sample(service, asset_id="new", audio=b"clear voice sample").status == "ready"
    assert len(ReplacementProviderClient.samples) == 1
