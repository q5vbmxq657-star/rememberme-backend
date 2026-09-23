import asyncio
from unittest.mock import Mock
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException

import app.services.elevenlabs_voice_service as module


@pytest.fixture
def verification(monkeypatch):
    profile = uuid4()
    job = {"job_id": uuid4(), "profile_id": profile, "provider": "elevenlabs",
        "training_type": "voice", "status": "verification_required", "provider_job_id": "voice-test",
        "request_payload": {"_stay_consent_revision": 3}}
    payload = {"voice_id": "voice-test", "voice_verification": {"is_verified": True, "requires_verification": False}}
    service = object.__new__(module.ElevenLabsVoiceService)
    service.api_key = "test-only"
    service.repository = Mock()
    service.repository.get_voice_status_snapshot.return_value = ({}, job)
    calls = []
    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(200, json=payload)
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    consent = Mock()
    monkeypatch.setattr(module, "require_profile_purposes", consent)
    return service, profile, job, payload, calls, consent


def test_confirmed_verification_uses_existing_atomic_activation(verification):
    service, profile, job, payload, calls, consent = verification
    asyncio.run(service.refresh_voice_verification(profile))
    assert calls == ["https://api.elevenlabs.io/v1/voices/voice-test"]
    assert consent.call_count == 2
    args = service.repository.apply_voice_training_result.call_args.kwargs
    assert args["job_id"] == job["job_id"]
    assert args["profile_id"] == profile
    assert args["status"] == "ready"
    assert set(args["provider_payload"]) == {"voice_id", "requires_verification", "verification_confirmed"}


@pytest.mark.parametrize("verification_state", [None, {}, {"is_verified": "true", "requires_verification": False},
    {"is_verified": True}, {"is_verified": True, "requires_verification": True}])
def test_no_speculative_activation(verification, verification_state):
    service, profile, job, payload, calls, consent = verification
    payload["voice_verification"] = verification_state
    asyncio.run(service.refresh_voice_verification(profile))
    service.repository.apply_voice_training_result.assert_not_called()


@pytest.mark.parametrize("control", ["BAN", "CAPTCHA", "ENTERPRISE_BAN", "future_control", {}])
def test_provider_safety_controls_are_never_bypassed(verification, control):
    service, profile, job, payload, calls, consent = verification
    payload["safety_control"] = control
    asyncio.run(service.refresh_voice_verification(profile))
    service.repository.apply_voice_training_result.assert_not_called()


def test_mismatched_provider_identity_does_not_activate(verification):
    service, profile, job, payload, calls, consent = verification
    payload["voice_id"] = "foreign-voice"
    with pytest.raises(module.ElevenLabsVoiceProviderError):
        asyncio.run(service.refresh_voice_verification(profile))
    service.repository.apply_voice_training_result.assert_not_called()


def test_revocation_after_provider_read_does_not_activate(verification):
    service, profile, job, payload, calls, consent = verification
    consent.side_effect = [None, HTTPException(409, "Permission changed")]
    with pytest.raises(HTTPException):
        asyncio.run(service.refresh_voice_verification(profile))
    service.repository.apply_voice_training_result.assert_not_called()
