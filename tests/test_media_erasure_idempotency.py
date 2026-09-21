from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

import app.routes.avatar_media as route
from app.services.avatar_media_storage_service import AvatarMediaAssetNotFoundError


@pytest.mark.parametrize("failure,status", [
    (AvatarMediaAssetNotFoundError("absent"), 204),
    (OSError("storage offline"), 503),
    (RuntimeError("scan failed"), 503),
])
def test_only_confirmed_absence_is_idempotent_success(monkeypatch, failure, status):
    access = Mock()
    monkeypatch.setattr(route, "require_profile_access", access)
    storage = Mock()
    storage.get_metadata.side_effect = failure
    monkeypatch.setattr(route, "AvatarMediaStorageService", Mock(return_value=storage))
    if status == 204:
        assert route.delete_avatar_media(str(uuid4()), str(uuid4()), Mock()).status_code == status
    else:
        with pytest.raises(HTTPException) as error:
            route.delete_avatar_media(str(uuid4()), str(uuid4()), Mock())
        assert error.value.status_code == status
    access.assert_called_once()
    storage.delete_asset.assert_not_called()


def test_foreign_asset_is_not_treated_as_already_deleted(monkeypatch):
    monkeypatch.setattr(route, "require_profile_access", Mock())
    storage = Mock()
    storage.get_metadata.return_value = SimpleNamespace(profile_id=str(uuid4()))
    monkeypatch.setattr(route, "AvatarMediaStorageService", Mock(return_value=storage))
    with pytest.raises(HTTPException) as error:
        route.delete_avatar_media(str(uuid4()), str(uuid4()), Mock())
    assert error.value.status_code == 404
    storage.delete_asset.assert_not_called()


def test_authorization_failure_never_becomes_absence_success(monkeypatch):
    monkeypatch.setattr(route, "require_profile_access", Mock(side_effect=HTTPException(404)))
    factory = Mock()
    monkeypatch.setattr(route, "AvatarMediaStorageService", factory)
    with pytest.raises(HTTPException) as error:
        route.delete_avatar_media(str(uuid4()), str(uuid4()), Mock())
    assert error.value.status_code == 404
    factory.assert_not_called()
