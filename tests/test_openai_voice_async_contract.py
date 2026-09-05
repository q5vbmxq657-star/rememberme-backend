import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.routes.voice as voice_routes
import app.services.openai_voice_service as voice_module
from app.services.openai_voice_service import OpenAIVoiceService, VoiceRecordingTooLargeError


class Upload:
    filename = "sample.webm"

    def __init__(self, content=b"recorded audio"):
        self.read = AsyncMock(return_value=content)
        self.close = AsyncMock()


class FakeClient:
    def __init__(self, create):
        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=create))
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.closed = True


def test_transcription_yields_and_does_not_force_a_language(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()

        async def transcribe(**kwargs):
            started.set()
            await release.wait()
            return SimpleNamespace(text="Hello")

        create = AsyncMock(side_effect=transcribe)
        client = FakeClient(create)
        factory = Mock(return_value=client)
        monkeypatch.setattr(voice_module, "AsyncOpenAI", factory)
        upload = Upload()
        service = OpenAIVoiceService()
        task = asyncio.create_task(service.transcribe(upload))
        await asyncio.wait_for(started.wait(), timeout=1)
        assert not task.done()
        release.set()
        result = await task
        assert result["text"] == "Hello"
        assert "language" not in create.call_args.kwargs
        assert create.call_args.kwargs["file"] == ("recording.webm", b"recorded audio")
        upload.read.assert_awaited_once_with(service.MAX_RECORDING_BYTES + 1)
        assert factory.call_args.kwargs["timeout"] == 45.0
        assert factory.call_args.kwargs["max_retries"] == 1
        assert client.closed

    asyncio.run(scenario())


def test_oversize_recording_is_rejected_before_provider_call(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(OpenAIVoiceService, "MAX_RECORDING_BYTES", 4)
    factory = Mock()
    monkeypatch.setattr(voice_module, "AsyncOpenAI", factory)
    with pytest.raises(VoiceRecordingTooLargeError):
        asyncio.run(OpenAIVoiceService().transcribe(Upload(b"12345")))
    factory.assert_not_called()


def test_empty_recording_preserves_empty_transcript_contract(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    factory = Mock()
    monkeypatch.setattr(voice_module, "AsyncOpenAI", factory)
    result = asyncio.run(OpenAIVoiceService().transcribe(Upload(b"")))
    assert result["text"] == ""
    assert result["diagnostic"]["reason"] == "empty_upload"
    factory.assert_not_called()


def test_cancellation_closes_provider_client(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")

    async def scenario():
        started = asyncio.Event()

        async def transcribe(**kwargs):
            started.set()
            await asyncio.Event().wait()

        client = FakeClient(AsyncMock(side_effect=transcribe))
        monkeypatch.setattr(voice_module, "AsyncOpenAI", Mock(return_value=client))
        task = asyncio.create_task(OpenAIVoiceService().transcribe(Upload()))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.closed

    asyncio.run(scenario())


def test_upload_limit_returns_413_and_closes_file(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setattr(OpenAIVoiceService, "MAX_RECORDING_BYTES", 4)
    upload = Upload(b"12345")
    with pytest.raises(voice_routes.HTTPException) as caught:
        asyncio.run(voice_routes.transcribe_audio(upload))
    assert caught.value.status_code == 413
    upload.close.assert_awaited_once()


def test_provider_error_closes_client_and_returns_safe_503(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    client = FakeClient(AsyncMock(side_effect=RuntimeError("private provider detail")))
    monkeypatch.setattr(voice_module, "AsyncOpenAI", Mock(return_value=client))
    upload = Upload()
    with pytest.raises(voice_routes.HTTPException) as caught:
        asyncio.run(voice_routes.transcribe_audio(upload))
    assert caught.value.status_code == 503
    assert "private provider detail" not in caught.value.detail
    assert client.closed
    upload.close.assert_awaited_once()
