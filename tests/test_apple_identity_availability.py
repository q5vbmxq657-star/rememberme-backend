import asyncio

import httpx
import pytest

from app.security.apple_identity import (
    AppleIdentityConfigurationError,
    AppleIdentityVerifier,
    AppleIdentityProviderUnavailableError,
    AppleIdentityVerificationError,
)


@pytest.mark.parametrize("error_code", ["invalid_client", "unauthorized_client", "unsupported_grant_type", "invalid_scope"])
def test_apple_client_configuration_error_is_not_user_rejection(monkeypatch, error_code):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return httpx.Response(400, json={"error": error_code})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    verifier = AppleIdentityVerifier(audience="test.app")
    with pytest.raises(AppleIdentityConfigurationError):
        asyncio.run(verifier._load_remote_token_response("code", "secret"))


@pytest.mark.parametrize("status, expected", [
    (429, AppleIdentityProviderUnavailableError),
    (500, AppleIdentityProviderUnavailableError),
    (502, AppleIdentityProviderUnavailableError),
    (503, AppleIdentityProviderUnavailableError),
    (504, AppleIdentityProviderUnavailableError),
    (400, AppleIdentityVerificationError),
    (401, AppleIdentityVerificationError),
])
def test_token_service_error_classification(monkeypatch, status, expected):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return httpx.Response(status, json={"error": "invalid_grant"})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    verifier = AppleIdentityVerifier(audience="test.app")
    with pytest.raises(expected):
        asyncio.run(verifier._load_remote_token_response("code", "secret"))


@pytest.mark.parametrize("error_code", ["temporarily_unavailable", "server_error"])
@pytest.mark.parametrize("status", [400, 401])
def test_transient_apple_error_is_not_invalid_credentials(monkeypatch, error_code, status):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            return httpx.Response(status, json={"error": error_code})

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    verifier = AppleIdentityVerifier(audience="test.app")
    with pytest.raises(AppleIdentityProviderUnavailableError):
        asyncio.run(verifier._load_remote_token_response("code", "secret"))


def test_jwks_transport_failure_preserves_unavailable_classification():
    async def loader():
        raise httpx.ConnectError("offline")

    verifier = AppleIdentityVerifier(audience="test.app", jwks_loader=loader)
    with pytest.raises(AppleIdentityProviderUnavailableError):
        asyncio.run(verifier._resolve_jwk("key"))


@pytest.mark.parametrize("document", [[], {}, {"keys": []}, {"keys": [None]}])
def test_unusable_key_document_is_provider_failure(document):
    async def loader():
        return document

    verifier = AppleIdentityVerifier(audience="test.app", jwks_loader=loader)
    with pytest.raises(AppleIdentityProviderUnavailableError):
        asyncio.run(verifier._resolve_jwk("key"))


def test_unknown_key_in_valid_document_remains_invalid_credentials():
    async def loader():
        return {"keys": [{"kid": "known", "kty": "RSA", "alg": "RS256"}]}

    verifier = AppleIdentityVerifier(audience="test.app", jwks_loader=loader)
    with pytest.raises(AppleIdentityVerificationError):
        asyncio.run(verifier._resolve_jwk("unknown"))


def test_exchange_returns_service_unavailable_without_creating_session(monkeypatch):
    from fastapi import HTTPException, Response
    from app.routes import auth
    from app.schemas.auth import AppleSessionExchangeRequest

    class UnavailableBootstrap:
        async def authenticate(self, **kwargs):
            raise AppleIdentityProviderUnavailableError("Apple unavailable")

    monkeypatch.setattr(auth, "get_apple_bootstrap_authentication_service", lambda: UnavailableBootstrap())
    monkeypatch.setattr(auth, "get_user_session_repository", lambda: pytest.fail("Must not create a session"))
    payload = AppleSessionExchangeRequest(identity_token="token", authorization_code="code", nonce="n" * 32)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(auth.exchange_apple_identity(payload, Response()))
    assert failure.value.status_code == 503
