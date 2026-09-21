import asyncio
from dataclasses import replace
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.routes.elevenlabs_voice import ProfileVoiceTTSRequest
from app.schemas.voice_delivery import VoiceDelivery
from app.services.elevenlabs_voice_service import ElevenLabsVoiceService
from test_elevenlabs_voice_fallback_contract import (
    FakeRepository, profile_with_personalized_voice, service_with, valid_voice_consent,
)


@pytest.mark.parametrize("field", ["energy", "speaking_speed", "pause_length"])
@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), float("inf")])
def test_delivery_rejects_invalid_values(field, value):
    with pytest.raises(ValidationError):
        VoiceDelivery(**{field: value})


def test_delivery_cannot_select_a_different_voice_or_profile():
    for field in ["voice_id", "profile_id", "model_id"]:
        with pytest.raises(ValidationError):
            VoiceDelivery(**{field: "other-identity"})


def test_old_clients_remain_compatible():
    request = ProfileVoiceTTSRequest(profile_id=uuid4(), text="Hello")
    assert request.delivery is None


def test_lively_voice_has_more_variation_without_identity_changes():
    service = object.__new__(ElevenLabsVoiceService)
    service.model_id = "eleven_multilingual_v2"
    calm = service._voice_settings(VoiceDelivery(energy=0, speaking_speed=0.3))
    lively = service._voice_settings(VoiceDelivery(energy=1, speaking_speed=0.7))
    assert lively["stability"] < calm["stability"]
    assert 0.3 <= lively["stability"] < calm["stability"] <= 0.65
    assert 0.9 <= calm["speed"] < lively["speed"] <= 1.1
    assert lively["similarity_boost"] == calm["similarity_boost"] == 0.82
    assert lively["style"] == calm["style"] == 0


@pytest.mark.parametrize("model", ["eleven_multilingual_v2", "eleven_flash_v2_5", "eleven_v3"])
def test_delivery_uses_supported_model_settings(model):
    service = object.__new__(ElevenLabsVoiceService)
    service.model_id = model
    settings = service._voice_settings(VoiceDelivery(energy=0.75))
    if model == "eleven_v3":
        assert set(settings) == {"stability"}
        assert settings["stability"] in {0, 0.5, 1}
    else:
        assert 0.7 <= settings["speed"] <= 1.2


def test_actual_provider_payload_preserves_clone_and_uses_delivery(monkeypatch, valid_voice_consent):
    profile = profile_with_personalized_voice()
    repository = FakeRepository(profile)
    service = service_with(repository)
    service.model_id = "eleven_multilingual_v2"
    service.api_key = "test-only"
    requests = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            requests.append((url, kwargs["json"]))
            return httpx.Response(200, content=b"audio", request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    delivery = VoiceDelivery(energy=1, speaking_speed=0.7, pause_length=0.35)
    result = asyncio.run(service.synthesize_for_profile(
        profile_id=profile.profile_id, text="Hello there!", delivery=delivery,
    ))
    url, payload = requests[0]
    assert url.endswith("/personalized-id")
    assert payload["text"] == "Hello there!"
    assert payload["voice_settings"] == service._voice_settings(delivery)
    assert result.voice_mode == "personalized"
    assert repository.voice_updates == []


def test_delivery_survives_existing_generic_fallback(valid_voice_consent):
    profile = replace(profile_with_personalized_voice(), voice_training_status="not_started")
    service = service_with(FakeRepository(profile))
    captured = []

    async def synthesize(**kwargs):
        captured.append(kwargs)
        return BytesIO(b"audio")

    service.synthesize = synthesize
    delivery = VoiceDelivery(energy=0.8)
    result = asyncio.run(service.synthesize_for_profile(
        profile_id=profile.profile_id, text="Hello", delivery=delivery,
    ))
    assert captured[0]["delivery"] == delivery
    assert captured[0]["voice_id"] == "generic-id"
    assert result.voice_mode == "warm_default"
