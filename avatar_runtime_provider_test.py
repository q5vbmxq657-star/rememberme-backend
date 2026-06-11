from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

from app.schemas.avatar_runtime import (
    AvatarRuntimeProvider,
)

from app.services.avatar_runtime_livekit_token_service import (
    AvatarLiveKitConfigurationError,
    AvatarRuntimeLiveKitTokenService,
)

from app.services.avatar_runtime_provider_registry import (
    AvatarRuntimeProviderRegistry,
)


def test_local_provider_is_always_ready() -> None:
    registry = AvatarRuntimeProviderRegistry()

    readiness = registry.readiness(
        AvatarRuntimeProvider.LOCAL
    )

    assert readiness.enabled is True
    assert readiness.configured is True
    assert readiness.runtime_available is True
    assert readiness.selectable is True


def test_remote_providers_fail_closed() -> None:
    environment = {
        "AVATAR_RUNTIME_ENABLE_TAVUS": "false",
        "TAVUS_API_KEY": "",
        "TAVUS_REPLICA_ID": "",
        "TAVUS_PERSONA_ID": "",
        "LIVEKIT_URL": "",
        "LIVEKIT_API_KEY": "",
        "LIVEKIT_API_SECRET": "",
    }

    with patch.dict(
        os.environ,
        environment,
        clear=False,
    ):
        registry = AvatarRuntimeProviderRegistry()

        readiness = registry.readiness(
            AvatarRuntimeProvider.TAVUS
        )

        assert readiness.selectable is False
        assert readiness.enabled is False


def test_local_fallback_selection() -> None:
    environment = {
        "AVATAR_RUNTIME_ENABLE_TAVUS": "false",
        "AVATAR_RUNTIME_ENABLE_BEYOND_PRESENCE": "false",
        "AVATAR_RUNTIME_ENABLE_HEYGEN": "false",
        "AVATAR_RUNTIME_ENABLE_SIMLI": "false",
    }

    with patch.dict(
        os.environ,
        environment,
        clear=False,
    ):
        registry = AvatarRuntimeProviderRegistry()

        (
            selected,
            fallback,
            diagnostics,
        ) = registry.select_provider(
            [
                AvatarRuntimeProvider.BEYOND_PRESENCE,
                AvatarRuntimeProvider.HEYGEN_LIVE_AVATAR,
                AvatarRuntimeProvider.SIMLI,
            ],
            allow_tavus_fallback=False,
            allow_local_fallback=True,
        )

        assert selected == AvatarRuntimeProvider.LOCAL

        assert (
            AvatarRuntimeProvider.LOCAL
            not in fallback
        )

        assert diagnostics


def test_livekit_service_rejects_missing_configuration() -> None:
    environment = {
        "LIVEKIT_URL": "",
        "LIVEKIT_API_KEY": "",
        "LIVEKIT_API_SECRET": "",
    }

    with patch.dict(
        os.environ,
        environment,
        clear=False,
    ):
        service = AvatarRuntimeLiveKitTokenService()

        assert service.is_configured is False

        try:
            service.create_client_token(
                session_id="test-session",
                profile_id=uuid4(),
                display_name="Test",
            )

        except AvatarLiveKitConfigurationError:
            pass

        else:
            raise AssertionError(
                "Missing LiveKit credentials were accepted."
            )


def test_livekit_token_generation() -> None:
    service = AvatarRuntimeLiveKitTokenService(
        server_url="wss://example.livekit.cloud",
        api_key="test-api-key",
        api_secret=(
            "test-api-secret-that-is-long-enough-for-jwt-signing"
        ),
        token_ttl_seconds=900,
    )

    client_token = service.create_client_token(
        session_id="test-session",
        profile_id=uuid4(),
        display_name="Test Profile",
    )

    avatar_token = service.create_avatar_token(
        session_id="test-session",
        publishing_for_identity="rememberme-bridge",
    )

    assert client_token.token
    assert client_token.room_name
    assert client_token.participant_identity
    assert avatar_token.token
    assert avatar_token.avatar_identity

    assert (
        avatar_token.publishing_for_identity
        == "rememberme-bridge"
    )


def main() -> None:
    tests = [
        test_local_provider_is_always_ready,
        test_remote_providers_fail_closed,
        test_local_fallback_selection,
        test_livekit_service_rejects_missing_configuration,
        test_livekit_token_generation,
    ]

    for test in tests:
        test()
        print(f"✓ {test.__name__}")


if __name__ == "__main__":
    main()
