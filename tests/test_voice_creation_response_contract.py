import httpx
import pytest

from app.services.elevenlabs_voice_service import (
    ElevenLabsVoiceProviderError,
    ElevenLabsVoiceService,
)


@pytest.mark.parametrize("payload", [
    None, [], "ready", {}, {"voice_id": "voice"},
    {"voice_id": 123, "requires_verification": False},
    {"voice_id": " ", "requires_verification": False},
    {"voice_id": "voice", "requires_verification": "false"},
    {"voice_id": "voice", "requires_verification": 0},
    {"voice_id": "voice", "requires_verification": None},
])
def test_incomplete_provider_response_cannot_activate_voice(payload):
    with pytest.raises(ElevenLabsVoiceProviderError):
        ElevenLabsVoiceService._clone_response(httpx.Response(200, json=payload))


@pytest.mark.parametrize("requires_verification", [True, False])
def test_verification_state_is_explicit(requires_verification):
    response = httpx.Response(200, json={
        "voice_id": " voice-new ",
        "requires_verification": requires_verification,
    })
    assert ElevenLabsVoiceService._clone_response(response) == (
        "voice-new", requires_verification,
    )


def test_malformed_json_is_a_provider_error():
    with pytest.raises(ElevenLabsVoiceProviderError):
        ElevenLabsVoiceService._clone_response(httpx.Response(200, content=b"not json"))


@pytest.mark.parametrize("status", [301, 302, 307, 401, 422, 503])
def test_redirect_is_not_provider_success(status):
    service = object.__new__(ElevenLabsVoiceService)
    with pytest.raises(ElevenLabsVoiceProviderError):
        service._raise_provider_error(httpx.Response(status, json={}), operation="Voice cloning")
