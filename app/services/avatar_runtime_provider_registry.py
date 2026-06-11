from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

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


def _environment_flag(
    key: str,
    default: bool = False,
) -> bool:
    raw_value = _clean_environment_value(key)

    if raw_value is None:
        return default

    return raw_value.lower() in _TRUE_VALUES


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
    """
    Central source of truth for avatar runtime provider readiness.

    A remote provider is selectable only when it is explicitly enabled,
    completely configured and backed by an implemented fail-closed adapter.
    """

    _TAVUS_REQUIRED_KEYS = (
        "TAVUS_API_KEY",
        "TAVUS_REPLICA_ID",
        "TAVUS_PERSONA_ID",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )

    _BEYOND_PRESENCE_REQUIRED_KEYS = (
        "BEY_API_KEY",
        "BEY_AVATAR_ID",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )

    _HEYGEN_REQUIRED_KEYS = (
        "LIVEAVATAR_API_KEY",
        "LIVEAVATAR_AVATAR_ID",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )

    _SIMLI_REQUIRED_KEYS = (
        "SIMLI_API_KEY",
        "SIMLI_FACE_ID",
        "LIVEKIT_URL",
        "LIVEKIT_API_KEY",
        "LIVEKIT_API_SECRET",
    )

    def readiness(
        self,
        provider: AvatarRuntimeProvider,
    ) -> AvatarProviderReadiness:
        if provider == AvatarRuntimeProvider.LOCAL:
            return AvatarProviderReadiness(
                provider=provider,
                enabled=True,
                configured=True,
                runtime_available=True,
                missing_configuration=(),
                reason="Local avatar runtime is available.",
            )

        if provider == AvatarRuntimeProvider.TAVUS:
            return self._remote_readiness(
                provider=provider,
                enable_key="AVATAR_RUNTIME_ENABLE_TAVUS",
                required_keys=self._TAVUS_REQUIRED_KEYS,
                adapter_available=True,
                adapter_reason=(
                    "The production Tavus LiveKit worker and VoiceDNA "
                    "media bridge are installed."
                ),
            )

        if provider == AvatarRuntimeProvider.BEYOND_PRESENCE:
            return self._remote_readiness(
                provider=provider,
                enable_key="AVATAR_RUNTIME_ENABLE_BEYOND_PRESENCE",
                required_keys=self._BEYOND_PRESENCE_REQUIRED_KEYS,
                adapter_available=False,
                adapter_reason=(
                    "Beyond Presence has no active production runtime adapter."
                ),
            )

        if provider == AvatarRuntimeProvider.HEYGEN_LIVE_AVATAR:
            return self._remote_readiness(
                provider=provider,
                enable_key="AVATAR_RUNTIME_ENABLE_HEYGEN",
                required_keys=self._HEYGEN_REQUIRED_KEYS,
                adapter_available=False,
                adapter_reason=(
                    "HeyGen LiveAvatar has no active production runtime adapter."
                ),
            )

        if provider == AvatarRuntimeProvider.SIMLI:
            return self._remote_readiness(
                provider=provider,
                enable_key="AVATAR_RUNTIME_ENABLE_SIMLI",
                required_keys=self._SIMLI_REQUIRED_KEYS,
                adapter_available=False,
                adapter_reason=(
                    "Simli has no active production runtime adapter."
                ),
            )

        return AvatarProviderReadiness(
            provider=provider,
            enabled=False,
            configured=False,
            runtime_available=False,
            missing_configuration=(),
            reason="Unknown avatar runtime provider.",
        )

    def select_provider(
        self,
        preferred_providers: Iterable[AvatarRuntimeProvider],
        *,
        allow_tavus_fallback: bool,
        allow_local_fallback: bool,
    ) -> Tuple[
        AvatarRuntimeProvider,
        List[AvatarRuntimeProvider],
        Dict[str, str],
    ]:
        ordered_providers: List[AvatarRuntimeProvider] = []

        for provider in preferred_providers:
            if provider not in ordered_providers:
                ordered_providers.append(provider)

        if (
            allow_tavus_fallback
            and AvatarRuntimeProvider.TAVUS not in ordered_providers
        ):
            ordered_providers.append(
                AvatarRuntimeProvider.TAVUS
            )

        if (
            allow_local_fallback
            and AvatarRuntimeProvider.LOCAL not in ordered_providers
        ):
            ordered_providers.append(
                AvatarRuntimeProvider.LOCAL
            )

        diagnostics: Dict[str, str] = {}

        for provider in ordered_providers:
            readiness = self.readiness(provider)
            diagnostics[provider.value] = readiness.reason

            if readiness.selectable:
                fallback_providers = [
                    candidate
                    for candidate in ordered_providers
                    if candidate != provider
                ]

                return (
                    provider,
                    fallback_providers,
                    diagnostics,
                )

        raise RuntimeError(
            "No configured avatar runtime provider is available."
        )

    def readiness_snapshot(
        self,
    ) -> Dict[str, AvatarProviderReadiness]:
        return {
            provider.value: self.readiness(provider)
            for provider in AvatarRuntimeProvider
        }

    def _remote_readiness(
        self,
        *,
        provider: AvatarRuntimeProvider,
        enable_key: str,
        required_keys: Tuple[str, ...],
        adapter_available: bool,
        adapter_reason: str,
    ) -> AvatarProviderReadiness:
        enabled = _environment_flag(
            enable_key,
            default=False,
        )

        missing_configuration = tuple(
            key
            for key in required_keys
            if _clean_environment_value(key) is None
        )

        configured = not missing_configuration

        if not enabled:
            reason = (
                f"{provider.value} is disabled because "
                f"{enable_key}=true is not configured."
            )
        elif not configured:
            reason = (
                f"{provider.value} is missing required configuration: "
                + ", ".join(missing_configuration)
            )
        else:
            reason = adapter_reason

        return AvatarProviderReadiness(
            provider=provider,
            enabled=enabled,
            configured=configured,
            runtime_available=adapter_available,
            missing_configuration=missing_configuration,
            reason=reason,
        )
