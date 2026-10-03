import asyncio
import base64
import io
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
import wave
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest
from app.services.self_hosted_voice_client import SelfHostedVoiceClient, SelfHostedVoiceUnavailableError
from voice_runtime.server import create_app
from voice_runtime.engine import ChatterboxEngine


TOKEN = "private-test-token-" * 3
REVISION = "a" * 64


@pytest.mark.parametrize("language,expected", [("de-DE", "de"), ("EN_us", "en"),
    ("fr", "fr"), ("zh-Hans", "zh"), ("nb-NO", "no"), ("nn", "no")])
def test_model_language_contract_normalizes_supported_locales(language, expected):
    data = {**payload(), "language": language}
    assert SelfHostedVoiceRequest.model_validate_json(json.dumps(data)).language == expected


def audio_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x10\x01" * (16000 * 3))
    return output.getvalue()


def payload():
    return dict(request_id=str(uuid4()), profile_id=str(uuid4()),
        voice_version=str(uuid4()), consent_revision=1, model_revision=REVISION,
        text="Hello Anna.", language="en",
        reference_wav_base64=base64.b64encode(audio_bytes()).decode())


class Engine:
    revision = REVISION

    def __init__(self):
        self.requests = []

    def synthesize(self, request):
        self.requests.append(request)
        return audio_bytes()


def test_runtime_refuses_missing_credentials():
    with pytest.raises(RuntimeError):
        create_app(token="")


def test_runtime_requires_auth_and_echoes_exact_binding():
    engine = Engine()
    with TestClient(create_app(engine_factory=lambda: engine, token=TOKEN)) as client:
        data = payload()
        assert client.get("/health/ready").status_code == 401
        assert client.post("/v1/synthesize", json=data).status_code == 401
        headers = {"Authorization": f"Bearer {TOKEN}"}
        assert client.get("/health/ready", headers=headers).json() == {
            "status": "ready", "contract_version": 1, "model_revision": REVISION}
        result = client.post("/v1/synthesize", json=data, headers=headers)
        assert result.status_code == 200
        assert result.headers["X-STAY-Profile-ID"] == data["profile_id"]
        assert result.headers["X-STAY-Voice-Version"] == data["voice_version"]
        assert result.content == audio_bytes()
        assert len(engine.requests) == 1


@pytest.mark.parametrize("change", [
    {"text": "  "}, {"text": "a" * 601}, {"consent_revision": 0},
    {"model_revision": "b" * 64}, {"language": "unknown"},
    {"reference_wav_base64": "not audio"}, {"extra": "not permitted"},
])
def test_invalid_requests_never_reach_model(change):
    engine = Engine()
    with TestClient(create_app(engine_factory=lambda: engine, token=TOKEN)) as client:
        result = client.post("/v1/synthesize", json={**payload(), **change},
            headers={"Authorization": f"Bearer {TOKEN}"})
        assert result.status_code == 422
        assert not engine.requests
        assert "reference_wav_base64" not in result.text


def test_model_errors_are_redacted():
    class Failing(Engine):
        def synthesize(self, request):
            raise RuntimeError("private voice reference and text")
    with TestClient(create_app(engine_factory=Failing, token=TOKEN)) as client:
        result = client.post("/v1/synthesize", json=payload(),
            headers={"Authorization": f"Bearer {TOKEN}"})
        assert result.status_code == 503
        assert "private voice" not in result.text


def test_busy_worker_rejects_parallel_inference_and_recovers():
    entered = threading.Event()
    release = threading.Event()

    class Blocking(Engine):
        def synthesize(self, request):
            entered.set()
            if not release.wait(5):
                raise TimeoutError("Test worker was not released")
            return super().synthesize(request)

    engine = Blocking()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(engine_factory=lambda: engine, token=TOKEN)) as client:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(client.post, "/v1/synthesize", json=payload(), headers=headers)
            try:
                assert entered.wait(3)
                readiness = client.get("/health/ready", headers=headers)
                assert readiness.status_code == 503
                assert readiness.headers["Retry-After"] == "1"
                busy = client.post("/v1/synthesize", json=payload(), headers=headers)
                assert busy.status_code == 503
                assert busy.headers["Retry-After"] == "1"
            finally:
                release.set()
            assert pending.result(timeout=3).status_code == 200
        assert client.get("/health/ready", headers=headers).status_code == 200
        assert client.post("/v1/synthesize", json=payload(), headers=headers).status_code == 200
        assert len(engine.requests) == 2


def test_oversized_upload_releases_capacity():
    engine = Engine()
    headers = {"Authorization": f"Bearer {TOKEN}"}
    with TestClient(create_app(engine_factory=lambda: engine, token=TOKEN)) as client:
        result = client.post("/v1/synthesize", content=b"x" * 1_350_001, headers=headers)
        assert result.status_code == 413
        assert not engine.requests
        assert client.post("/v1/synthesize", json=payload(), headers=headers).status_code == 200


@pytest.mark.parametrize("url", ["http://host", "https://user:pass@host", "https://host?token=x"])
def test_client_rejects_unsafe_configuration(url):
    with pytest.raises(ValueError):
        SelfHostedVoiceClient(url, TOKEN)


@pytest.mark.parametrize("mismatch", [False, True])
@pytest.mark.parametrize("audio_kind", ["valid", "silent", "truncated", "invalid"])
def test_client_verifies_response_binding(monkeypatch, mismatch, audio_kind):
    request = SelfHostedVoiceRequest.model_validate_json(json.dumps(payload()))
    headers = {"Content-Type": "audio/wav", "X-STAY-Request-ID": str(request.request_id),
        "X-STAY-Profile-ID": str(uuid4()) if mismatch else str(request.profile_id),
        "X-STAY-Voice-Version": str(request.voice_version), "X-STAY-Model-Revision": REVISION}
    audio = audio_bytes()
    if audio_kind == "silent":
        audio = audio[:44] + bytes(len(audio) - 44)
    elif audio_kind == "truncated":
        audio = audio[:-2]
    elif audio_kind == "invalid":
        audio = b"not a wave file"
    transport = httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, content=audio))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    client = SelfHostedVoiceClient("https://private-voice.example", TOKEN)
    if mismatch or audio_kind != "valid":
        with pytest.raises(SelfHostedVoiceUnavailableError):
            asyncio.run(client.synthesize(request))
    else:
        assert asyncio.run(client.synthesize(request)) == audio_bytes()


@pytest.mark.parametrize("cancel", [False, True])
def test_client_bounds_entire_response_and_closes_stream(monkeypatch, cancel):
    request = SelfHostedVoiceRequest.model_validate_json(json.dumps(payload()))
    entered = asyncio.Event()

    class StalledAudio(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield audio_bytes()[:44]
            entered.set()
            await asyncio.Event().wait()

        async def aclose(self):
            self.closed = True

    stream = StalledAudio()
    headers = {"Content-Type": "audio/wav", "X-STAY-Request-ID": str(request.request_id),
        "X-STAY-Profile-ID": str(request.profile_id),
        "X-STAY-Voice-Version": str(request.voice_version), "X-STAY-Model-Revision": REVISION}
    transport = httpx.MockTransport(lambda _: httpx.Response(200, headers=headers, stream=stream))
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    client = SelfHostedVoiceClient("https://private-voice.example", TOKEN)
    monkeypatch.setattr(client, "REQUEST_TIMEOUT_SECONDS", .05 if not cancel else 5)

    async def run():
        task = asyncio.create_task(client.synthesize(request))
        await asyncio.wait_for(entered.wait(), 1)
        if cancel:
            task.cancel()
        expected = asyncio.CancelledError if cancel else SelfHostedVoiceUnavailableError
        with pytest.raises(expected):
            await asyncio.wait_for(task, 1)

    asyncio.run(run())
    assert stream.closed


@pytest.mark.parametrize("failure", ["generation", "silent", "nonfinite", "empty", "encoding"])
def test_engine_failure_clears_conditioning_files_and_lock(monkeypatch, failure):
    import numpy as np

    paths = []

    class Model:
        sr = 24000
        conds = "previous reference"

        def generate(self, text, **kwargs):
            assert self.conds is None
            path = Path(kwargs["audio_prompt_path"])
            assert path.read_bytes() == audio_bytes()
            paths.append(path)
            self.conds = "current reference"
            if failure == "generation":
                raise RuntimeError("generation failed")
            samples = {"silent": [0.0], "nonfinite": [float("nan")],
                       "empty": [], "encoding": [0.1]}[failure]
            return SimpleNamespace(detach=lambda: SimpleNamespace(
                cpu=lambda: SimpleNamespace(numpy=lambda: np.array(samples))))

    def fail_encoding(*args, **kwargs):
        raise RuntimeError("encoding failed")

    monkeypatch.setitem(sys.modules, "soundfile", SimpleNamespace(write=fail_encoding))
    engine = ChatterboxEngine.__new__(ChatterboxEngine)
    engine.model = Model()
    engine.revision = REVISION
    engine.lock = threading.Lock()
    request = SelfHostedVoiceRequest.model_validate_json(json.dumps(payload()))
    for _ in range(2):
        with pytest.raises(RuntimeError):
            engine.synthesize(request)
        assert engine.model.conds is None
        assert not engine.lock.locked()
        assert all(not path.parent.exists() for path in paths)
    assert len(paths) == 2
