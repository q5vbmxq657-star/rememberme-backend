import asyncio
import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
from io import BytesIO
from uuid import uuid4

import httpx
import psycopg
import pytest
from fastapi import HTTPException

import app.routes.elevenlabs_voice as routes
from app.services.avatar_media_analysis_service import AvatarMediaAnalysisUnavailableError
from app.services.digital_human_profile_repository import DigitalHumanProfileRepositoryError, StaleVoiceTrainingError
from test_voice_clone_upload_limits import configure, submit, upload


def test_tts_version_is_forwarded_and_confirmed_without_exposing_provider_id(monkeypatch):
    profile = uuid4()
    version = str(uuid4())
    service = SimpleNamespace(synthesize_for_profile=AsyncMock(return_value=SimpleNamespace(
        audio_stream=BytesIO(b"audio"), voice_mode="personalized")))
    authorization = Mock()
    monkeypatch.setattr(routes, "require_profile_access", authorization)
    monkeypatch.setattr(routes, "ElevenLabsVoiceService", lambda: service)
    response = asyncio.run(routes.synthesize_profile_voice(
        routes.ProfileVoiceTTSRequest(profile_id=profile, text="Hello", voice_version=version), principal=object()))
    assert response.headers["x-stay-voice-version"] == version
    assert response.headers["x-stay-voice-mode"] == "personalized"
    assert response.headers["cache-control"] == "no-store"
    assert "voice_id" not in response.headers
    assert authorization.call_count == 2
    service.synthesize_for_profile.assert_awaited_once_with(profile_id=profile, text="Hello", delivery=None, voice_version=version)


@pytest.mark.parametrize("error", [httpx.ConnectError("private"), psycopg.OperationalError("private"),
    DigitalHumanProfileRepositoryError("private"), TimeoutError("private")])
def test_tts_dependency_failures_are_sanitized(monkeypatch, error):
    service = SimpleNamespace(synthesize_for_profile=AsyncMock(side_effect=error))
    monkeypatch.setattr(routes, "require_profile_access", Mock())
    monkeypatch.setattr(routes, "ElevenLabsVoiceService", lambda: service)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes.synthesize_profile_voice(routes.ProfileVoiceTTSRequest(profile_id=uuid4(), text="Hello"), principal=object()))
    assert caught.value.status_code == 503
    assert "private" not in caught.value.detail


def test_tts_late_profile_revocation_blocks_audio_delivery(monkeypatch):
    service = SimpleNamespace(synthesize_for_profile=AsyncMock(return_value=SimpleNamespace(
        audio_stream=BytesIO(b"audio"), voice_mode="personalized")))
    monkeypatch.setattr(routes, "require_profile_access", Mock(side_effect=[None, HTTPException(404, "Profile not found")]))
    monkeypatch.setattr(routes, "ElevenLabsVoiceService", lambda: service)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routes.synthesize_profile_voice(routes.ProfileVoiceTTSRequest(profile_id=uuid4(), text="Hello"), principal=object()))
    assert caught.value.status_code == 404


@pytest.mark.parametrize("error", [httpx.ConnectError("private transport"),
    psycopg.OperationalError("private database"), DigitalHumanProfileRepositoryError("private repository"),
    routes.ElevenLabsVoiceError("private service"), TimeoutError("private timeout")])
@pytest.mark.parametrize("endpoint", ["clone", "status"])
def test_dependency_failures_are_safe_retryable_503(monkeypatch, error, endpoint):
    profile, service = configure(monkeypatch)
    files = [upload(b"1234")]
    service.clone_voice.side_effect = error
    service.status_for_profile = Mock(side_effect=error)
    with pytest.raises(HTTPException) as caught:
        if endpoint == "clone":
            submit(profile, files)
        else:
            asyncio.run(routes.profile_voice_status(profile, principal=object()))
    assert caught.value.status_code == 503
    assert "private" not in caught.value.detail
    if endpoint == "clone":
        files[0].close.assert_awaited_once()


@pytest.mark.parametrize("message,expected", [
    ("Voice cloning requires verified consent.", "Confirm permission"),
    ("At least one voice sample is required.", "Add at least one"),
    ("A voice sample is too small.", "too short"),
    ("Unsupported voice sample type: private/custom", "format is not supported"),
    ("private provider diagnostic", "cannot be used"),
    ("The selected file contains no audio track.", "no audio track"),
    ("The selected recording could not be decoded.", "Export it again"),
    ("The selected recording contains no readable audio.", "no readable audio"),
    ("Use a clear recording of at least three seconds without clipping.", "at least three seconds"),
])
def test_clone_validation_is_actionable_without_raw_diagnostics(monkeypatch, message, expected):
    profile, service = configure(monkeypatch)
    service.clone_voice.side_effect = routes.ElevenLabsVoiceValidationError(message)
    with pytest.raises(HTTPException) as caught:
        submit(profile, [upload(b"1234")])
    assert caught.value.status_code == 422
    assert expected in caught.value.detail
    assert "private" not in caught.value.detail


def test_status_database_and_auth_run_off_event_loop_and_response_is_not_cached(monkeypatch):
    event_thread = threading.get_ident()
    calls = []
    def off_loop(label):
        assert threading.get_ident() != event_thread
        calls.append(label)
    def authorize(**kwargs):
        off_loop("auth")
    def status(profile):
        off_loop("status")
        return {"profile_id": str(profile), "status": "ready", "voice_ready": True}
    def factory():
        off_loop("constructor")
        return SimpleNamespace(status_for_profile=status)
    monkeypatch.setattr(routes, "require_profile_access", authorize)
    monkeypatch.setattr(routes, "ElevenLabsVoiceService", factory)
    response = asyncio.run(routes.profile_voice_status(uuid4(), principal=object()))
    assert response.headers["cache-control"] == "no-store"
    assert json.loads(response.body)["voice_ready"] is True
    assert calls == ["auth", "constructor", "status", "auth"]


@pytest.mark.parametrize("endpoint", ["clone", "status"])
def test_late_revocation_blocks_delivery(monkeypatch, endpoint):
    profile, service = configure(monkeypatch)
    permitted = True
    def authorize(**kwargs):
        if not permitted:
            raise HTTPException(404, "Profile not found.")
    async def clone(**kwargs):
        nonlocal permitted
        permitted = False
        return SimpleNamespace(status="ready")
    def status(profile):
        nonlocal permitted
        permitted = False
        return {"voice_ready": True}
    service.clone_voice.side_effect = clone
    service.status_for_profile = status
    monkeypatch.setattr(routes, "require_profile_access", authorize)
    with pytest.raises(HTTPException) as caught:
        if endpoint == "clone":
            submit(profile, [upload(b"1234")])
        else:
            asyncio.run(routes.profile_voice_status(profile, principal=object()))
    assert caught.value.status_code == 404


def test_clone_auth_is_off_loop_and_denied_uploads_close(monkeypatch):
    profile, service = configure(monkeypatch)
    event_thread = threading.get_ident()
    def denied(**kwargs):
        assert threading.get_ident() != event_thread
        raise HTTPException(403, "Permission required.")
    monkeypatch.setattr(routes, "require_profile_access", denied)
    file = upload(b"1234")
    with pytest.raises(HTTPException) as caught:
        submit(profile, [file])
    assert caught.value.status_code == 403
    file.close.assert_awaited_once()
    file.read.assert_not_awaited()
    service.clone_voice.assert_not_awaited()


@pytest.mark.parametrize("error,code,detail", [
    (StaleVoiceTrainingError("private revision"), 409, "selected voice changed"),
    (routes.ElevenLabsVoiceProviderError("private audio details", status_code=422, provider_code="invalid_audio"),
     422, "Choose another voice recording"),
    (routes.ElevenLabsVoiceProviderError("private unknown details", status_code=422, provider_code="unknown"),
     503, "temporarily unavailable"),
    (routes.ElevenLabsVoiceProviderError("private transient details", status_code=503, provider_code="invalid_audio"),
     503, "temporarily unavailable"),
])
def test_clone_distinguishes_stale_selection_and_known_provider_validation(monkeypatch, error, code, detail):
    profile, service = configure(monkeypatch)
    service.clone_voice.side_effect = error
    file = upload(b"1234")
    with pytest.raises(HTTPException) as caught:
        submit(profile, [file])
    assert caught.value.status_code == code
    assert detail in caught.value.detail
    assert "private" not in caught.value.detail
    file.close.assert_awaited_once()


def test_status_preserves_atomic_pending_and_active_voice_snapshot(monkeypatch):
    profile, service = configure(monkeypatch)
    snapshot = {"profile_id": str(profile), "voice_ready": True, "active_voice_version": "version-2",
                "pending_training": {"status": "submitted", "job_id": str(uuid4())}}
    service.status_for_profile = Mock(return_value=snapshot)
    response = asyncio.run(routes.profile_voice_status(profile, principal=object()))
    assert json.loads(response.body) == snapshot
    service.status_for_profile.assert_called_once_with(profile)
    assert response.headers["cache-control"] == "no-store"


def test_audio_analysis_outage_is_retryable_not_bad_recording(monkeypatch):
    profile, service = configure(monkeypatch)
    service.clone_voice.side_effect = AvatarMediaAnalysisUnavailableError("private temporary file path")
    file = upload(b"1234")
    with pytest.raises(HTTPException) as caught:
        submit(profile, [file])
    assert caught.value.status_code == 503
    assert caught.value.detail == "Recording checks are temporarily unavailable. Please try again shortly."
    assert "private" not in caught.value.detail
    file.close.assert_awaited_once()
