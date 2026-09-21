from __future__ import annotations

import asyncio
import pytest
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import Mock
from fastapi import HTTPException
from app.schemas.profile_consent import PurposeConsentSnapshot, CONSENT_POLICY_VERSION

import app.services.avatar_provider_service as provider_module
from app.services.avatar_provider_service import AvatarProviderService


@pytest.fixture
def valid_tavus_consent(monkeypatch):
    def grant(profile_id, purposes, *, expected_revision=None):
        assert purposes in ({'photo_likeness'}, {'video_motion', 'voice_synthesis'})
        assert expected_revision in (None, 11)
        return PurposeConsentSnapshot(profile_id=profile_id, revision=11,
            policy_version=CONSENT_POLICY_VERSION,
            purposes=sorted(purposes | {'provider_processing'}))
    guard = Mock(side_effect=grant)
    monkeypatch.setattr(provider_module, 'require_profile_purposes', guard)
    return guard


class FakeResponse:
    status_code = 200
    text = ""

    def json(self):
        return {
            "face_id": "r_face_contract",
            "status": "started",
        }


class FakeHTTPClient:
    last_url = None
    last_payload = None

    def __init__(self, *args, **kwargs):
        del args, kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    async def post(self, url, *, headers, json):
        assert headers["x-api-key"] == "contract-key"
        type(self).last_url = url
        type(self).last_payload = json
        return FakeResponse()


class FakeRepository:
    def __init__(self):
        self.job_id = uuid4()
        self.updates = []
        self.avatar_updates = []

    def ensure(self, profile_id):
        return {"profile_id": profile_id}

    def get_avatar_training_request(self, profile_id, idempotency_key):
        return None

    def create_training_job(self, **kwargs):
        self.created = kwargs
        return {
            "job_id": self.job_id,
            "was_created": True,
        }

    def update_training_job(self, job_id, **kwargs):
        self.updates.append((job_id, kwargs))
        return {}

    def claim_avatar_submission(self, job_id, profile_id):
        assert job_id == self.job_id
        assert profile_id == self.created["profile_id"]
        self.updates.append((job_id, {"status": "submitted"}))
        return True

    def set_avatar_training(self, profile_id, **kwargs):
        self.avatar_updates.append((profile_id, kwargs))
        return {}


class ExistingTrainingRepository(FakeRepository):
    def create_training_job(self, **kwargs):
        self.created = kwargs
        return {
            "job_id": self.job_id,
            "was_created": False,
            "provider_job_id": None,
        }


class FakeMediaStorage:
    def __init__(self, profile_id="00000000-0000-0000-0000-000000000123"):
        self.profile_id = profile_id

    def get_metadata(self, asset_id):
        assert asset_id in {"media-asset-id", "motion-asset-id"}
        kind = "video" if asset_id == "motion-asset-id" else "image"
        return SimpleNamespace(profile_id=self.profile_id, asset_type=kind,
                               content_type=f"{kind}/{'mp4' if kind == 'video' else 'jpeg'}")

    def sign_provider_training_url(self, *, asset_id, profile_id):
        self.get_metadata(asset_id)
        assert profile_id == self.profile_id
        return SimpleNamespace(
            signed_url="https://stay.example/provider-lease.mp4" if asset_id == "motion-asset-id"
                else "https://stay.example/provider-lease.jpg"
        )


def test_ios_package_prefers_uploaded_motion_video_for_video_avatar_training():
    service = AvatarProviderService()
    service._media_storage_service = FakeMediaStorage()
    service._validate_training_asset = Mock()

    source = service._extract_tavus_training_source(
        {
            "identityPhotos": [
                {
                    "kind": "identityPhoto",
                    "remoteAssetID": "media-asset-id",
                    "remoteURL": "https://stay.example/expired.jpg",
                }
            ],
            "motionVideos": [
                {
                    "kind": "motionVideo",
                    "remoteAssetID": "motion-asset-id",
                    "remoteURL": "https://stay.example/motion.mov",
                }
            ],
        },
        profile_id="00000000-0000-0000-0000-000000000123",
    )

    assert source == (
        "train_video_url",
        "https://stay.example/provider-lease.mp4",
    )
    service._validate_training_asset.assert_called_once_with(
        service._media_storage_service.get_metadata("motion-asset-id"), "video")


def test_ios_package_uses_signed_identity_photo_when_motion_is_absent():
    service = AvatarProviderService()
    service._media_storage_service = FakeMediaStorage()
    service._validate_training_asset = Mock()

    source = service._extract_tavus_training_source(
        {
            "identityPhotos": [
                {
                    "kind": "identityPhoto",
                    "remoteAssetID": "media-asset-id",
                }
            ],
        },
        profile_id="00000000-0000-0000-0000-000000000123",
    )

    assert source == (
        "train_image_url",
        "https://stay.example/provider-lease.jpg",
    )
    service._validate_training_asset.assert_called_once_with(
        service._media_storage_service.get_metadata("media-asset-id"), "image")


@pytest.mark.parametrize("requested_image_fix", [None, True, False, "true"])
def test_submit_uses_current_tavus_faces_image_contract(monkeypatch, requested_image_fix, valid_tavus_consent):
    monkeypatch.setenv("TAVUS_API_KEY", "contract-key")
    monkeypatch.delenv("TAVUS_CALLBACK_URL", raising=False)
    monkeypatch.delenv("TAVUS_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeHTTPClient)

    service = AvatarProviderService()
    repository = FakeRepository()
    service._profile_repository = repository
    # Source ownership is covered by the resolver tests; this test owns the Faces payload contract.
    service._extract_tavus_training_source = Mock(return_value=("train_image_url", "https://stay.example/training.jpg"))

    state = asyncio.run(
        service.submit(
            provider="tavus",
            profile_id=str(uuid4()),
            package_record_id=str(uuid4()),
            package={
                "tavus_training_mode": "image",
                "train_image_url": "https://stay.example/training.jpg",
                "voice_name": "james",
                "auto_fix_training_image": requested_image_fix,
            },
        )
    )

    assert FakeHTTPClient.last_url == "https://tavusapi.com/v2/faces"
    assert FakeHTTPClient.last_payload == {
        "face_name": FakeHTTPClient.last_payload["face_name"],
        "model_name": "phoenix-4",
        "train_image_url": "https://stay.example/training.jpg",
        "voice_name": "james",
        "auto_fix_training_image": False,
    }
    assert state.external_job_id == "tavus:r_face_contract"
    assert state.external_avatar_id == "r_face_contract"
    assert state.status == "training"
    assert repository.updates[-1][1]["provider_job_id"] == state.external_job_id
    assert repository.avatar_updates[-1][1]["replica_id"] == "r_face_contract"


@pytest.mark.parametrize("has_video", [False, True])
def test_iphone_uppercase_profile_uuid_can_train_its_uploaded_source(monkeypatch, has_video, valid_tavus_consent):
    monkeypatch.setenv("TAVUS_API_KEY", "contract-key")
    monkeypatch.setenv("TAVUS_IMAGE_TRAINING_VOICE_NAME", "james")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeHTTPClient)
    profile_id = str(uuid4())

    class ProfileBoundStorage:
        def get_metadata(self, asset_id):
            assert asset_id in {"photo.jpg", "video.mp4"}
            kind = "video" if asset_id == "video.mp4" else "image"
            return SimpleNamespace(profile_id=stored_profile_id, asset_type=kind,
                content_type="video/mp4" if kind == "video" else "image/jpeg")

        def sign_provider_training_url(self, *, asset_id, profile_id):
            if profile_id != stored_profile_id:
                raise RuntimeError("Training media does not belong to the requested profile.")
            return SimpleNamespace(signed_url=f"https://stay.example/{asset_id}")

    stored_profile_id = profile_id
    service = AvatarProviderService()
    service._profile_repository = FakeRepository()
    service._media_storage_service = ProfileBoundStorage()
    service._validate_training_asset = Mock()
    state = asyncio.run(service.submit(
        provider="tavus", profile_id=profile_id.upper(),
        package_record_id=str(uuid4()),
        package={
            "identityPhotos": [{"kind": "identityPhoto", "remoteAssetID": "photo.jpg"}],
            "motionVideos": [{"kind": "motionVideo", "remoteAssetID": "video.mp4"}] if has_video else [],
        },
    ))
    assert state.status == "training"
    field, filename = ("train_video_url", "video.mp4") if has_video else ("train_image_url", "photo.jpg")
    assert FakeHTTPClient.last_payload[field] == f"https://stay.example/{filename}"
    assert ("train_image_url" in FakeHTTPClient.last_payload) != ("train_video_url" in FakeHTTPClient.last_payload)
    service._validate_training_asset.assert_called_once_with(
        service._media_storage_service.get_metadata(filename), "video" if has_video else "image")


def test_training_never_uses_another_profiles_photo(monkeypatch):
    monkeypatch.setenv("TAVUS_API_KEY", "contract-key")
    FakeHTTPClient.last_url = None

    class ForeignStorage:
        def get_metadata(self, asset_id):
            assert asset_id == "foreign-photo"
            return SimpleNamespace(profile_id=str(uuid4()), asset_type="image", content_type="image/jpeg")

        def sign_provider_training_url(self, **kwargs):
            pytest.fail("Foreign media must never receive a provider lease.")

    service = AvatarProviderService()
    service._media_storage_service = ForeignStorage()
    service._profile_repository = FakeRepository()
    service._validate_training_asset = Mock()
    state = asyncio.run(service.submit(
        provider="tavus", profile_id=str(uuid4()).upper(),
        package_record_id=str(uuid4()),
        package={"identityPhotos": [{"kind": "identityPhoto", "remoteAssetID": "foreign-photo",
                                     "remoteURL": "https://stay.example/foreign.jpg"}]},
    ))
    assert state.status == "failed"
    assert FakeHTTPClient.last_url is None
    service._validate_training_asset.assert_not_called()


def test_image_mode_never_falls_through_to_video():
    service = AvatarProviderService()

    assert service._extract_tavus_training_source(
        {
            "tavus_training_mode": "image",
            "train_video_url": "https://stay.example/video.mp4",
        }
    ) is None


def test_duplicate_submission_never_calls_tavus_twice(monkeypatch, valid_tavus_consent):
    monkeypatch.setenv("TAVUS_API_KEY", "contract-key")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeHTTPClient)
    FakeHTTPClient.last_url = None
    FakeHTTPClient.last_payload = None

    service = AvatarProviderService()
    repository = ExistingTrainingRepository()
    service._profile_repository = repository
    service._extract_tavus_training_source = Mock(return_value=("train_image_url", "https://stay.example/training.jpg"))

    state = asyncio.run(
        service.submit(
            provider="tavus",
            profile_id=str(uuid4()),
            package_record_id=str(uuid4()),
            package={
                "tavus_training_mode": "image",
                "train_image_url": "https://stay.example/training.jpg",
                "voice_name": "james",
            },
        )
    )

    assert FakeHTTPClient.last_url is None
    assert state.status == "uploading"
    assert state.error_message is None
    assert state.external_job_id == f"tavus:pending:{repository.job_id}"
    assert state.current_stage == "Confirming avatar creation"
    assert "No new upload is needed" in state.provider_detail_message


@pytest.mark.parametrize('source', ['train_image_url', 'train_video_url'])
@pytest.mark.parametrize('revoke_during_work', [False, True])
def test_tavus_denial_or_revocation_prevents_provider_disclosure(monkeypatch, valid_tavus_consent, source, revoke_during_work):
    monkeypatch.setenv('TAVUS_API_KEY', 'contract-key')
    client = Mock()
    monkeypatch.setattr(provider_module.httpx, 'AsyncClient', client)
    service = AvatarProviderService()
    repository = FakeRepository()
    service._profile_repository = repository
    service._extract_tavus_training_source = Mock(return_value=(source, 'https://stay.example/media'))
    if revoke_during_work:
        create = repository.create_training_job
        def revoke(**kwargs):
            job = create(**kwargs)
            valid_tavus_consent.side_effect = HTTPException(409, 'Permissions changed')
            return job
        repository.create_training_job = revoke
    else:
        valid_tavus_consent.side_effect = HTTPException(403, 'Permission required')
    with pytest.raises(HTTPException) as caught:
        asyncio.run(service.submit(provider='tavus', profile_id=str(uuid4()),
            package_record_id=str(uuid4()), package={source: 'https://stay.example/media', 'voice_name': 'james'}))
    assert caught.value.status_code == (409 if revoke_during_work else 403)
    client.assert_not_called()
    assert repository.avatar_updates == []


def test_video_training_without_voice_permission_rejects_before_provider_post(monkeypatch):
    import app.security.purpose_authorization as purpose_module

    monkeypatch.setenv('TAVUS_API_KEY', 'contract-key')
    profile_id = uuid4()
    consent_repository = Mock()
    consent_repository.read.return_value = PurposeConsentSnapshot(
        profile_id=profile_id, revision=11, policy_version=CONSENT_POLICY_VERSION,
        purposes=['video_motion', 'provider_processing'])
    monkeypatch.setattr(purpose_module, 'ProfileConsentRepository', Mock(return_value=consent_repository))
    # Exercise the real purpose guard: video permission alone must not authorize voice cloning.
    monkeypatch.setattr(provider_module, 'require_profile_purposes', purpose_module.require_profile_purposes)
    client = Mock()
    monkeypatch.setattr(provider_module.httpx, 'AsyncClient', client)
    service = AvatarProviderService()
    repository = FakeRepository()
    repository.create_training_job = Mock(wraps=repository.create_training_job)
    service._profile_repository = repository
    service._extract_tavus_training_source = Mock(return_value=(
        'train_video_url', 'https://stay.example/owned-video-lease.mp4'))

    with pytest.raises(HTTPException) as caught:
        asyncio.run(service.submit(provider='tavus', profile_id=str(profile_id),
            package_record_id=str(uuid4()), package={'tavus_training_mode': 'video'}))

    assert caught.value.status_code == 403
    assert caught.value.detail['code'] == 'purpose_consent_required'
    consent_repository.read.assert_called_once_with(profile_id)
    client.assert_not_called()
    repository.create_training_job.assert_not_called()
    assert repository.avatar_updates == []
