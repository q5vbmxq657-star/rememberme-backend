from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import jwt
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
APPLE_TOKEN_URL = "https://appleid.apple.com/auth/token"
APPLE_ALGORITHM = "RS256"
APPLE_CLIENT_SECRET_ALGORITHM = "ES256"

DEFAULT_JWKS_CACHE_TTL_SECONDS = 3600.0
DEFAULT_HTTP_TIMEOUT_SECONDS = 5.0


class AppleIdentityConfigurationError(RuntimeError):
    pass


class AppleIdentityVerificationError(RuntimeError):
    pass


class AppleIdentityProviderUnavailableError(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedAppleToken:
    subject: str
    email: str | None


@dataclass(frozen=True)
class VerifiedAppleIdentity:
    subject: str
    email: str | None
    refresh_token: str


JwksLoader = Callable[
    [],
    Awaitable[dict[str, Any]],
]

TokenResponseLoader = Callable[
    [str, str],
    Awaitable[dict[str, Any]],
]


class AppleRefreshTokenCipher:
    _AAD = b"stay-apple-refresh-v1"

    def __init__(
        self,
        *,
        key: bytes | None = None,
    ) -> None:
        self._key = (
            key
            if key is not None
            else self._key_from_environment()
        )

        if len(self._key) != 32:
            raise AppleIdentityConfigurationError(
                "Apple credential encryption is not configured safely."
            )

        self._cipher = AESGCM(self._key)

    def encrypt(
        self,
        value: str,
    ) -> bytes:
        token = value.strip()

        if not token:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        nonce = secrets.token_bytes(12)

        return nonce + self._cipher.encrypt(
            nonce,
            token.encode("utf-8"),
            self._AAD,
        )

    def decrypt(
        self,
        payload: bytes,
    ) -> str:
        if len(payload) <= 12:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        try:
            plaintext = self._cipher.decrypt(
                payload[:12],
                payload[12:],
                self._AAD,
            )

            return plaintext.decode("utf-8")

        except (
            InvalidTag,
            UnicodeDecodeError,
            ValueError,
        ) as error:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            ) from error

    @staticmethod
    def _key_from_environment() -> bytes:
        encoded = os.getenv(
            "STAY_AUTH_DATA_ENCRYPTION_KEY",
            "",
        ).strip()

        if not encoded:
            raise AppleIdentityConfigurationError(
                "Apple credential encryption is not configured."
            )

        try:
            return base64.b64decode(
                encoded,
                altchars=b"-_",
                validate=True,
            )

        except (ValueError, binascii.Error) as error:
            raise AppleIdentityConfigurationError(
                "Apple credential encryption is invalid."
            ) from error


class AppleIdentityVerifier:
    def __init__(
        self,
        *,
        audience: str | None = None,
        team_id: str | None = None,
        key_id: str | None = None,
        private_key: str | None = None,
        issuer: str = APPLE_ISSUER,
        jwks_url: str = APPLE_JWKS_URL,
        token_url: str = APPLE_TOKEN_URL,
        jwks_cache_ttl_seconds: float = DEFAULT_JWKS_CACHE_TTL_SECONDS,
        http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        jwks_loader: JwksLoader | None = None,
        token_response_loader: TokenResponseLoader | None = None,
    ) -> None:
        resolved_audience = (
            audience
            if audience is not None
            else os.getenv(
                "APPLE_SIGN_IN_CLIENT_ID"
            )
        )

        self.audience = (
            resolved_audience or ""
        ).strip()

        self.issuer = issuer.strip()
        self.jwks_url = jwks_url.strip()
        self.token_url = token_url.strip()
        self.team_id = (
            team_id
            if team_id is not None
            else os.getenv(
                "APPLE_SIGN_IN_TEAM_ID",
                "",
            )
        ).strip()
        self.key_id = (
            key_id
            if key_id is not None
            else os.getenv(
                "APPLE_SIGN_IN_KEY_ID",
                "",
            )
        ).strip()
        self.private_key = (
            private_key
            if private_key is not None
            else os.getenv(
                "APPLE_SIGN_IN_PRIVATE_KEY",
                "",
            )
        ).strip().replace("\\n", "\n")

        if not self.audience:
            raise AppleIdentityConfigurationError(
                "Apple identity audience is not configured."
            )

        if self.issuer != APPLE_ISSUER:
            raise AppleIdentityConfigurationError(
                "Apple identity issuer is invalid."
            )

        if not self.jwks_url:
            raise AppleIdentityConfigurationError(
                "Apple JWKS URL is invalid."
            )

        if not self.token_url:
            raise AppleIdentityConfigurationError(
                "Apple token URL is invalid."
            )

        if jwks_cache_ttl_seconds <= 0:
            raise AppleIdentityConfigurationError(
                "Apple JWKS cache TTL must be positive."
            )

        if http_timeout_seconds <= 0:
            raise AppleIdentityConfigurationError(
                "Apple HTTP timeout must be positive."
            )

        self.jwks_cache_ttl_seconds = float(
            jwks_cache_ttl_seconds
        )

        self.http_timeout_seconds = float(
            http_timeout_seconds
        )

        self._jwks_loader = (
            jwks_loader
            or self._load_remote_jwks
        )

        self._token_response_loader = (
            token_response_loader
            or self._load_remote_token_response
        )

        self._jwks_cache: dict[
            str,
            dict[str, Any],
        ] = {}

        self._jwks_cached_at = 0.0
        self._jwks_lock = asyncio.Lock()

    async def exchange_and_verify(
        self,
        identity_token: str,
        *,
        authorization_code: str,
        nonce: str,
    ) -> VerifiedAppleIdentity:
        code = authorization_code.strip()

        if not code:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        device_identity = await self.verify(
            identity_token,
            nonce=nonce,
        )

        payload = await self._safe_load_token_response(
            code,
            self._client_secret(),
        )

        server_identity_token = payload.get(
            "id_token"
        )
        refresh_token = payload.get(
            "refresh_token"
        )

        if (
            not isinstance(server_identity_token, str)
            or not server_identity_token.strip()
            or not isinstance(refresh_token, str)
            or not refresh_token.strip()
        ):
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        server_identity = await self.verify(
            server_identity_token,
            nonce=nonce,
        )

        if not hmac.compare_digest(
            device_identity.subject,
            server_identity.subject,
        ):
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        return VerifiedAppleIdentity(
            subject=device_identity.subject,
            email=(
                device_identity.email
                or server_identity.email
            ),
            refresh_token=refresh_token.strip(),
        )

    async def verify(
        self,
        token: str,
        *,
        nonce: str,
    ) -> VerifiedAppleToken:
        credential = token.strip()
        raw_nonce = nonce.strip()

        if not credential or not raw_nonce:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        try:
            header = jwt.get_unverified_header(
                credential
            )

        except jwt.PyJWTError as error:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            ) from error

        if header.get("alg") != APPLE_ALGORITHM:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        key_id = header.get("kid")

        if (
            not isinstance(key_id, str)
            or not key_id.strip()
        ):
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        jwk = await self._resolve_jwk(
            key_id.strip()
        )

        try:
            signing_key = jwt.PyJWK.from_dict(
                jwk,
                algorithm=APPLE_ALGORITHM,
            ).key

            claims = jwt.decode(
                credential,
                key=signing_key,
                algorithms=[APPLE_ALGORITHM],
                audience=self.audience,
                issuer=self.issuer,
                options={
                    "require": [
                        "iss",
                        "aud",
                        "exp",
                        "sub",
                    ],
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                },
            )

        except (
            jwt.PyJWTError,
            ValueError,
            TypeError,
        ) as error:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            ) from error

        subject = claims.get("sub")

        nonce_claim = claims.get("nonce")
        expected_nonce = hashlib.sha256(
            raw_nonce.encode("utf-8")
        ).hexdigest()

        if (
            not isinstance(nonce_claim, str)
            or not hmac.compare_digest(
                nonce_claim,
                expected_nonce,
            )
        ):
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        if (
            not isinstance(subject, str)
            or not subject.strip()
        ):
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        email_value = claims.get("email")

        email = (
            email_value.strip()
            if isinstance(email_value, str)
            and email_value.strip()
            else None
        )

        return VerifiedAppleToken(
            subject=subject.strip(),
            email=email,
        )

    def _client_secret(
        self,
    ) -> str:
        if (
            not self.team_id
            or not self.key_id
            or not self.private_key
        ):
            raise AppleIdentityConfigurationError(
                "Apple authorization-code validation is not configured."
            )

        now = datetime.now(
            timezone.utc
        )

        try:
            return jwt.encode(
                {
                    "iss": self.team_id,
                    "iat": now,
                    "exp": now + timedelta(
                        minutes=5
                    ),
                    "aud": APPLE_ISSUER,
                    "sub": self.audience,
                },
                self.private_key,
                algorithm=(
                    APPLE_CLIENT_SECRET_ALGORITHM
                ),
                headers={
                    "kid": self.key_id
                },
            )

        except (jwt.PyJWTError, ValueError) as error:
            raise AppleIdentityConfigurationError(
                "Apple authorization-code validation is not configured."
            ) from error

    async def _safe_load_token_response(
        self,
        authorization_code: str,
        client_secret: str,
    ) -> dict[str, Any]:
        try:
            payload = await self._token_response_loader(
                authorization_code,
                client_secret,
            )

        except (
            AppleIdentityConfigurationError,
            AppleIdentityProviderUnavailableError,
            AppleIdentityVerificationError,
        ):
            raise

        except Exception as error:
            raise AppleIdentityProviderUnavailableError(
                "Apple identity provider is unavailable."
            ) from error

        if not isinstance(payload, dict):
            raise AppleIdentityProviderUnavailableError(
                "Apple identity provider is unavailable."
            )

        return payload

    async def _load_remote_token_response(
        self,
        authorization_code: str,
        client_secret: str,
    ) -> dict[str, Any]:
        try:
            timeout = httpx.Timeout(
                self.http_timeout_seconds
            )

            async with httpx.AsyncClient(
                timeout=timeout
            ) as client:
                response = await client.post(
                    self.token_url,
                    data={
                        "client_id": self.audience,
                        "client_secret": client_secret,
                        "code": authorization_code,
                        "grant_type": (
                            "authorization_code"
                        ),
                    },
                    headers={
                        "Accept": "application/json"
                    },
                )

        except httpx.RequestError as error:
            raise AppleIdentityProviderUnavailableError(
                "Apple identity provider is unavailable."
            ) from error

        if response.status_code != 200:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        try:
            payload = response.json()

        except ValueError as error:
            raise AppleIdentityProviderUnavailableError(
                "Apple identity provider is unavailable."
            ) from error

        if not isinstance(payload, dict):
            raise AppleIdentityProviderUnavailableError(
                "Apple identity provider is unavailable."
            )

        return payload

    async def revoke_refresh_token(
        self,
        refresh_token: str,
    ) -> None:
        token = refresh_token.strip()
        if not token:
            raise AppleIdentityVerificationError(
                "Apple revocation credential is invalid."
            )

        client_secret = self._client_secret()
        try:
            timeout = httpx.Timeout(self.http_timeout_seconds)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    "https://appleid.apple.com/auth/revoke",
                    data={
                        "client_id": self.audience,
                        "client_secret": client_secret,
                        "token": token,
                        "token_type_hint": "refresh_token",
                    },
                    headers={"Accept": "application/json"},
                )
        except httpx.RequestError as error:
            raise AppleIdentityProviderUnavailableError(
                "Apple credential revocation is unavailable."
            ) from error

        if response.status_code != 200:
            raise AppleIdentityProviderUnavailableError(
                "Apple credential revocation could not be verified."
            )

    async def _resolve_jwk(
        self,
        key_id: str,
    ) -> dict[str, Any]:
        cached = await self._get_cached_keys(
            force_refresh=False
        )

        key = cached.get(key_id)

        if key is not None:
            return key

        refreshed = await self._get_cached_keys(
            force_refresh=True
        )

        key = refreshed.get(key_id)

        if key is None:
            raise AppleIdentityVerificationError(
                "Apple identity credential is invalid."
            )

        return key

    async def _get_cached_keys(
        self,
        *,
        force_refresh: bool,
    ) -> dict[str, dict[str, Any]]:
        now = time.monotonic()

        cache_fresh = (
            bool(self._jwks_cache)
            and (
                now - self._jwks_cached_at
                < self.jwks_cache_ttl_seconds
            )
        )

        if cache_fresh and not force_refresh:
            return dict(self._jwks_cache)

        async with self._jwks_lock:
            now = time.monotonic()

            cache_fresh = (
                bool(self._jwks_cache)
                and (
                    now - self._jwks_cached_at
                    < self.jwks_cache_ttl_seconds
                )
            )

            if cache_fresh and not force_refresh:
                return dict(self._jwks_cache)

            document = await self._safe_load_jwks()
            parsed = self._parse_jwks(
                document
            )

            self._jwks_cache = parsed
            self._jwks_cached_at = (
                time.monotonic()
            )

            return dict(parsed)

    async def _safe_load_jwks(
        self,
    ) -> dict[str, Any]:
        try:
            document = await self._jwks_loader()

        except AppleIdentityVerificationError:
            raise

        except Exception as error:
            raise AppleIdentityVerificationError(
                "Apple identity credential could not be verified."
            ) from error

        if not isinstance(document, dict):
            raise AppleIdentityVerificationError(
                "Apple identity credential could not be verified."
            )

        return document

    async def _load_remote_jwks(
        self,
    ) -> dict[str, Any]:
        try:
            timeout = httpx.Timeout(
                self.http_timeout_seconds
            )

            async with httpx.AsyncClient(
                timeout=timeout
            ) as client:
                response = await client.get(
                    self.jwks_url
                )

                response.raise_for_status()
                payload = response.json()

        except (
            httpx.HTTPError,
            ValueError,
        ) as error:
            raise AppleIdentityVerificationError(
                "Apple identity credential could not be verified."
            ) from error

        if not isinstance(payload, dict):
            raise AppleIdentityVerificationError(
                "Apple identity credential could not be verified."
            )

        return payload

    @staticmethod
    def _parse_jwks(
        payload: dict[str, Any],
    ) -> dict[str, dict[str, Any]]:
        keys = payload.get("keys")

        if not isinstance(keys, list):
            raise AppleIdentityVerificationError(
                "Apple identity credential could not be verified."
            )

        parsed: dict[
            str,
            dict[str, Any],
        ] = {}

        for key in keys:
            if not isinstance(key, dict):
                continue

            key_id = key.get("kid")
            key_type = key.get("kty")
            algorithm = key.get("alg")

            if (
                not isinstance(key_id, str)
                or not key_id.strip()
            ):
                continue

            if key_type != "RSA":
                continue

            if (
                algorithm is not None
                and algorithm != APPLE_ALGORITHM
            ):
                continue

            parsed[key_id.strip()] = key

        if not parsed:
            raise AppleIdentityVerificationError(
                "Apple identity credential could not be verified."
            )

        return parsed
