import asyncio
import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import voice_release_preflight as preflight
from app.services.self_hosted_voice_client import SelfHostedVoiceClient, SelfHostedVoiceUnavailableError


@pytest.mark.parametrize("status,payload,valid", [
    (200, {"status": "ready", "contract_version": 1, "model_revision": "a" * 64}, True),
    (503, {}, False), (302, {}, False), (200, [], False),
    (200, {"status": "ready", "contract_version": True, "model_revision": "a" * 64}, False),
    (200, {"status": "ready", "contract_version": 1, "model_revision": "b" * 64}, False),
    (200, {"padding": "x" * 5000}, False),
])
def test_readiness_requires_bounded_matching_contract(monkeypatch, status, payload, valid):
    def respond(request):
        assert request.headers["authorization"] == "Bearer " + "s" * 32
        return httpx.Response(status, json=payload)
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(respond), **kwargs))
    client = SelfHostedVoiceClient("https://voice.example", "s" * 32)
    if valid:
        asyncio.run(client.verify_readiness("a" * 64))
    else:
        with pytest.raises(SelfHostedVoiceUnavailableError):
            asyncio.run(client.verify_readiness("a" * 64))


@pytest.mark.parametrize("provider,keys,revisions,valid,expected_call", [
    ("elevenlabs", set(), set(), True, False),
    ("stay_voice", set(), set(), True, True),
    ("elevenlabs", {"active"}, {"a" * 64}, True, True),
    ("stay_voice", {"missing"}, set(), False, False),
    ("stay_voice", {"active"}, {"b" * 64}, False, False),
    ("unknown", set(), set(), False, False),
])
def test_preflight_preserves_existing_voice_requirements(monkeypatch, provider, keys, revisions, valid, expected_call):
    monkeypatch.setenv("STAY_VOICE_TRAINING_PROVIDER", provider)
    monkeypatch.setenv("STAY_VOICE_REFERENCE_KEYS", json.dumps({"active": base64.b64encode(b"k" * 32).decode()}))
    monkeypatch.setenv("STAY_VOICE_REFERENCE_ACTIVE_KEY", "active")
    monkeypatch.setenv("STAY_VOICE_MODEL_REVISION", "a" * 64)
    monkeypatch.setenv("STAY_VOICE_RUNTIME_URL", "https://voice.example")
    monkeypatch.setenv("STAY_VOICE_RUNTIME_TOKEN", "s" * 32)
    monkeypatch.setattr(preflight, "stored_requirements", lambda _: (keys, revisions))
    calls = []
    async def ready(self, revision):
        calls.append(revision)
    monkeypatch.setattr(SelfHostedVoiceClient, "verify_readiness", ready)
    if valid:
        asyncio.run(preflight.verify_voice_release(SimpleNamespace()))
    else:
        with pytest.raises(ValueError):
            asyncio.run(preflight.verify_voice_release(SimpleNamespace()))
    assert bool(calls) == expected_call
