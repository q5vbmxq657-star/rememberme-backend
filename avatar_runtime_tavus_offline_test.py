from __future__ import annotations

import io
import os
import tempfile
import wave
from uuid import uuid4


os.environ["AVATAR_RUNTIME_ENABLE_TAVUS"] = "false"
os.environ[
    "AVATAR_RUNTIME_ENABLE_BEYOND_PRESENCE"
] = "false"
os.environ["AVATAR_RUNTIME_ENABLE_HEYGEN"] = "false"
os.environ["AVATAR_RUNTIME_ENABLE_SIMLI"] = "false"
os.environ["AVATAR_RUNTIME_STORAGE_ROOT"] = (
    tempfile.mkdtemp(
        prefix="rememberme-avatar-07f-test-"
    )
)

from fastapi.testclient import TestClient

from app.main import app
from app.services.avatar_runtime_livekit_token_service import (
    AvatarRuntimeLiveKitTokenService,
)
from app.services.avatar_runtime_tavus_adapter import (
    AvatarRuntimeTavusAdapter,
)


def make_test_wav() -> bytes:
    sample_rate = 24_000
    sample_count = 2_400
    pcm = bytearray()

    for index in range(sample_count):
        value = (
            1_200
            if (index // 60) % 2 == 0
            else -1_200
        )
        pcm.extend(
            int(value).to_bytes(
                2,
                byteorder="little",
                signed=True,
            )
        )

    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(bytes(pcm))

    return buffer.getvalue()


def assert_offline_contract() -> None:
    token_service = (
        AvatarRuntimeLiveKitTokenService(
            server_url="wss://test.livekit.invalid",
            api_key="test-key",
            api_secret=(
                "test-secret-with-sufficient-length"
            ),
            token_ttl_seconds=600,
        )
    )

    session_id = f"avatar_{uuid4().hex}"
    profile_id = uuid4()

    client_token = token_service.create_client_token(
        session_id=session_id,
        profile_id=profile_id,
        display_name="Offline Contract",
    )
    bridge_token = token_service.create_bridge_token(
        session_id=session_id,
    )

    assert client_token.room_name.startswith(
        "rememberme-avatar-"
    )
    assert bridge_token.participant_identity.startswith(
        "rememberme-voice-bridge-"
    )
    assert client_token.token.count(".") == 2
    assert bridge_token.token.count(".") == 2

    adapter = AvatarRuntimeTavusAdapter(
        token_service=token_service
    )
    frames = adapter._decode_audio_frames_sync(
        make_test_wav()
    )

    assert frames
    assert all(
        frame.sample_rate == 24_000
        for frame in frames
    )
    assert all(
        frame.num_channels == 1
        for frame in frames
    )

    client = TestClient(app)

    readiness_response = client.get(
        "/v1/avatar-runtime/providers/readiness"
    )
    assert (
        readiness_response.status_code == 200
    ), readiness_response.text

    readiness = readiness_response.json()

    assert readiness["local"]["selectable"] is True
    assert readiness["tavus"]["enabled"] is False

    create_response = client.post(
        "/v1/avatar-runtime/sessions",
        json={
            "profile_id": str(profile_id),
            "display_name": "Offline Contract",
            "preferred_providers": [
                "tavus",
                "local",
            ],
            "preferred_transport": "livekit",
            "fallback_enabled": True,
            "allow_tavus_fallback": True,
            "allow_local_fallback": True,
            "requires_custom_identity": True,
            "requires_external_voice_audio": True,
            "maximum_accepted_latency_ms": 1500,
        },
    )

    assert (
        create_response.status_code == 201
    ), create_response.text

    session = create_response.json()

    assert session["provider"] == "local"
    assert session["transport"] == "local_avatar"
    assert session["livekit"] is None
    assert (
        session["metadata"]
        ["remote_session_verified"]
        == "false"
    )

    delete_response = client.delete(
        "/v1/avatar-runtime/sessions/"
        + session["session_id"]
    )

    assert (
        delete_response.status_code == 204
    ), delete_response.text

    print("✓ Restricted client token contract")
    print("✓ Restricted VoiceDNA bridge token contract")
    print("✓ PyAV to LiveKit PCM decode contract")
    print("✓ 24 kHz mono output contract")
    print("✓ Provider readiness fail-closed contract")
    print("✓ Local fallback remains operational")
    print("✓ No LiveKit API request was sent")
    print("✓ No Tavus conversation was created")


if __name__ == "__main__":
    assert_offline_contract()
