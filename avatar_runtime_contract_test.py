from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from uuid import uuid4

runtime_root = tempfile.mkdtemp(prefix="rememberme-avatar-runtime-test-")
os.environ["AVATAR_RUNTIME_STORAGE_ROOT"] = runtime_root

from fastapi.testclient import TestClient

from app.main import app


def assert_contract() -> None:
    client = TestClient(app)

    profile_id = str(uuid4())

    create_payload = {
        "profile_id": profile_id,
        "display_name": "Contract Test Person",
        "preferred_providers": [
            "beyond_presence",
            "heygen_liveavatar",
            "simli",
            "local",
        ],
        "preferred_transport": "livekit",
        "fallback_enabled": True,
        "allow_tavus_fallback": False,
        "allow_local_fallback": True,
        "requires_custom_identity": True,
        "requires_external_voice_audio": True,
        "maximum_accepted_latency_ms": 1500,
    }

    create_response = client.post(
        "/v1/avatar-runtime/sessions",
        json=create_payload,
    )

    assert create_response.status_code == 201, create_response.text

    session = create_response.json()

    assert session["profile_id"] == profile_id
    assert session["provider"] == "local"
    assert session["transport"] == "local_avatar"
    assert session["livekit"] is None
    assert session["preview_video_url"] is None
    assert session["metadata"]["audio_ownership"] == "ios_voice_dna_pipeline"

    session_id = session["session_id"]
    request_id = str(uuid4())

    metadata = {
        "request_id": request_id,
        "session_id": session_id,
        "profile_id": profile_id,
        "text": "This is the exact synthesized VoiceDNA response.",
        "voice_synthesis_mode": "personalized_delivery",
        "voice_provider": None,
        "runtime_voice_id": None,
        "allow_provider_fallback": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    fake_audio = (
        b"ID3"
        + bytes(range(64))
        + b"rememberme-avatar-contract-audio"
    )

    speech_response = client.post(
        f"/v1/avatar-runtime/sessions/{session_id}/speech",
        data={
            "metadata": json.dumps(metadata),
        },
        files={
            "audio": (
                "voice.mp3",
                fake_audio,
                "audio/mpeg",
            )
        },
    )

    assert speech_response.status_code == 200, speech_response.text

    speech = speech_response.json()

    assert speech["request_id"] == request_id
    assert speech["session_id"] == session_id
    assert speech["resolved_provider"] == "local"
    assert speech["transport"] == "local_avatar"
    assert speech["livekit"] is None
    assert speech["video_url"] is None
    assert speech["fallback_used"] is True
    assert isinstance(speech["latency_ms"], int)

    interrupt_response = client.post(
        f"/v1/avatar-runtime/sessions/{session_id}/interrupt"
    )

    assert interrupt_response.status_code == 200, interrupt_response.text
    assert interrupt_response.json()["status"] == "interrupted"

    delete_response = client.delete(
        f"/v1/avatar-runtime/sessions/{session_id}"
    )

    assert delete_response.status_code == 204, delete_response.text

    missing_response = client.post(
        f"/v1/avatar-runtime/sessions/{session_id}/interrupt"
    )

    assert missing_response.status_code == 404, missing_response.text

    print("✓ Session creation contract")
    print("✓ Local provider fallback contract")
    print("✓ Multipart VoiceDNA audio contract")
    print("✓ Request/session/profile correlation")
    print("✓ Interrupt contract")
    print("✓ Delete contract")
    print("✓ Missing-session protection")


if __name__ == "__main__":
    assert_contract()
