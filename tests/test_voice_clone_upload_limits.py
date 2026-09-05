import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import app.routes.elevenlabs_voice as routes


def upload(content):
    return SimpleNamespace(
        filename="sample.m4a",
        content_type="audio/mp4",
        read=AsyncMock(side_effect=lambda size: content[:size]),
        close=AsyncMock(),
    )


def configure(monkeypatch):
    profile_id = uuid4()
    service = SimpleNamespace(clone_voice=AsyncMock(return_value=SimpleNamespace(
        job_id=uuid4(), profile_id=profile_id, status="ready", requires_verification=False,
    )))
    factory = Mock(return_value=service)
    factory.max_sample_bytes = 4
    factory.max_total_bytes = 6
    factory.max_samples = 2
    monkeypatch.setattr(routes, "ElevenLabsVoiceService", factory)
    monkeypatch.setattr(routes, "require_profile_access", Mock())
    return profile_id, service


def submit(profile_id, files):
    return asyncio.run(routes.clone_profile_voice(
        profile_id=profile_id, display_name="Test", consent_verified=True,
        idempotency_key="test-key", remove_background_noise=False,
        files=files, principal=object(),
    ))


@pytest.mark.parametrize("contents", [[b"12345"], [b"1234", b"123"]])
def test_size_limits_stop_reading_before_provider_submission(monkeypatch, contents):
    profile_id, service = configure(monkeypatch)
    files = [upload(content) for content in contents]
    with pytest.raises(routes.HTTPException) as caught:
        submit(profile_id, files)
    assert caught.value.status_code == 413
    service.clone_voice.assert_not_awaited()
    for file in files:
        file.close.assert_awaited_once()


def test_count_limit_is_checked_before_loading_audio(monkeypatch):
    profile_id, service = configure(monkeypatch)
    files = [upload(b"123") for _ in range(3)]
    with pytest.raises(routes.HTTPException) as caught:
        submit(profile_id, files)
    assert caught.value.status_code == 422
    service.clone_voice.assert_not_awaited()
    for file in files:
        file.read.assert_not_awaited()
        file.close.assert_awaited_once()


def test_valid_samples_keep_profile_consent_and_idempotency_contract(monkeypatch):
    profile_id, service = configure(monkeypatch)
    files = [upload(b"1234"), upload(b"12")]
    result = submit(profile_id, files)
    assert result["voice_ready"] is True
    kwargs = service.clone_voice.call_args.kwargs
    assert kwargs["profile_id"] == profile_id
    assert kwargs["consent_verified"] is True
    assert kwargs["idempotency_key"] == "test-key"
    assert [sample.data for sample in kwargs["samples"]] == [b"1234", b"12"]
    files[0].read.assert_awaited_once_with(5)
    files[1].read.assert_awaited_once_with(3)
    for file in files:
        file.close.assert_awaited_once()
