from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from app.schemas.avatar_runtime import AvatarRuntimeProvider


_TRUE_VALUES = {
    "1",
    "true",
    "yes",
    "on",
    "enabled",
}


def _clean_environment_value(
    key: str,
) -> Optional[str]:
    value = os.getenv(key)
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


@dataclass(frozen=True)
class AvatarProviderReadiness:
    provider: AvatarRuntimeProvider
    enabled: bool
    configured: bool
    runtime_available: bool
    missing_configuration: Tuple[str, ...]
    reason: str

    @property
    def selectable(self) -> bool:
        return (
            self.enabled
            and self.configured
            and self.runtime_available
        )


class AvatarRuntimeProviderRegistry:
    """Single production authority for the Tavus LiveKit runtime."""

    _REQUIRED_KEYS = (
        "TAVUS_API_KEY",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
        "DATABASE_URL",
    )

    def readiness(self) -> AvatarProviderReadiness:
        enabled_value = _clean_environment_value(
            "AVATAR_RUNTIME_ENABLE_TAVUS"
        )
        enabled = (
            enabled_value is not None
            and enabled_value.lower() in _TRUE_VALUES
        )
        missing = tuple(
            key
            for key in self._REQUIRED_KEYS
            if _clean_environment_value(key) is None
        )
        configured = not missing

        if not enabled:
            reason = (
                "Tavus runtime is disabled because "
                "AVATAR_RUNTIME_ENABLE_TAVUS=true is not configured."
            )
        elif not configured:
            reason = (
                "Tavus runtime configuration is incomplete: "
                + ", ".join(missing)
            )
        else:
            reason = (
                "The Tavus LiveKit worker, profile-bound face identity "
                "and external ElevenLabs audio bridge are configured."
            )

        return AvatarProviderReadiness(
            provider=AvatarRuntimeProvider.TAVUS,
            enabled=enabled,
            configured=configured,
            runtime_available=True,
            missing_configuration=missing,
            reason=reason,
        )

    def require_provider(
        self,
    ) -> tuple[
        AvatarRuntimeProvider,
        Dict[str, str],
    ]:
        readiness = self.readiness()
        if not readiness.selectable:
            raise RuntimeError(readiness.reason)
        return (
            AvatarRuntimeProvider.TAVUS,
            {"tavus": readiness.reason},
        )

    def readiness_snapshot(
        self,
    ) -> Dict[str, AvatarProviderReadiness]:
        readiness = self.readiness()
        return {
            readiness.provider.value: readiness
        }
