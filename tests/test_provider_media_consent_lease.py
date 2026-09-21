import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

import app.services.avatar_media_storage_service as storage_module
from app.services.avatar_media_storage_service import AvatarMediaStorageService


@pytest.fixture
def lease(monkeypatch):
    service = AvatarMediaStorageService.__new__(AvatarMediaStorageService)
    service.signing_secret = "test-only-signing-secret"
    service.public_base_url = "https://stay.example"
    metadata = SimpleNamespace(
        asset_id="photo-asset", profile_id=str(uuid4()), content_type="image/jpeg"
    )
    service.get_metadata = Mock(return_value=metadata)
    guard = Mock(return_value=SimpleNamespace(revision=7))
    monkeypatch.setattr(storage_module, "require_profile_purposes", guard)
    result = service.sign_provider_training_url(
        asset_id=metadata.asset_id, profile_id=metadata.profile_id
    )
    query = parse_qs(urlsplit(result.signed_url).query)
    arguments = dict(
        asset_id=metadata.asset_id, expires=int(query["expires"][0]),
        signature=query["signature"][0],
        consent_revision=int(query["consent_revision"][0]), purpose=query["purpose"][0],
    )
    return service, metadata, guard, arguments


def test_provider_lease_rechecks_exact_grant_on_download(lease):
    service, metadata, guard, arguments = lease
    assert service.verify_download_signature(**arguments) is metadata
    guard.assert_called_with(
        UUID(metadata.profile_id), {"photo_likeness"}, expected_revision=7
    )


def test_revoked_provider_lease_cannot_download(lease):
    service, _, guard, arguments = lease
    guard.side_effect = HTTPException(status_code=403)
    with pytest.raises(HTTPException):
        service.verify_download_signature(**arguments)


@pytest.mark.parametrize("change", [
    {"consent_revision": None, "purpose": None},
    {"consent_revision": 8}, {"purpose": "video_motion"},
    {"consent_revision": None},
])
def test_provider_lease_cannot_strip_or_modify_authorization(lease, change):
    service, _, _, arguments = lease
    with pytest.raises(RuntimeError):
        service.verify_download_signature(**(arguments | change))


def test_preview_link_does_not_require_training_consent(lease):
    service, metadata, guard, _ = lease
    guard.reset_mock()
    expires = int(time.time()) + 100
    signature = service._signature(metadata.asset_id, expires)
    assert service.verify_download_signature(metadata.asset_id, expires, signature) is metadata
    guard.assert_not_called()
