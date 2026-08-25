from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import app.services.avatar_provider_service as provider_module
from app.services.avatar_provider_service import AvatarProviderService


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

    def create_training_job(self, **kwargs):
        self.created = kwargs
        return {
            "job_id": self.job_id,
            "was_created": True,
        }

    def update_training_job(self, job_id, **kwargs):
        self.updates.append((job_id, kwargs))
        return {}

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
    def sign_provider_training_url(self, *, asset_id, profile_id):
        assert asset_id == "media-asset-id"
        assert profile_id == "00000000-0000-0000-0000-000000000123"
        return SimpleNamespace(
            signed_url="https://stay.example/provider-lease.jpg"
        )


def test_ios_package_prefers_uploaded_motion_video_for_video_avatar_training():
    service = AvatarProviderService()
    service._media_storage_service = FakeMediaStorage()

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
                    "remoteURL": "https://stay.example/motion.mov",
                }
            ],
        },
        profile_id="00000000-0000-0000-0000-000000000123",
    )

    assert source == (
        "train_video_url",
        "https://stay.example/motion.mov",
    )


def test_ios_package_uses_signed_identity_photo_when_motion_is_absent():
    service = AvatarProviderService()
    service._media_storage_service = FakeMediaStorage()

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


def test_submit_uses_current_tavus_faces_image_contract(monkeypatch):
    monkeypatch.setenv("TAVUS_API_KEY", "contract-key")
    monkeypatch.delenv("TAVUS_CALLBACK_URL", raising=False)
    monkeypatch.delenv("TAVUS_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeHTTPClient)

    service = AvatarProviderService()
    repository = FakeRepository()
    service._profile_repository = repository

    state = asyncio.run(
        service.submit(
            provider="tavus",
            profile_id=str(uuid4()),
            package_record_id=str(uuid4()),
            package={
                "tavus_training_mode": "image",
                "train_image_url": "https://stay.example/training.jpg",
                "voice_name": "james",
                "auto_fix_training_image": True,
            },
        )
    )

    assert FakeHTTPClient.last_url == "https://tavusapi.com/v2/faces"
    assert FakeHTTPClient.last_payload == {
        "face_name": FakeHTTPClient.last_payload["face_name"],
        "model_name": "phoenix-4",
        "train_image_url": "https://stay.example/training.jpg",
        "voice_name": "james",
        "auto_fix_training_image": True,
    }
    assert state.external_job_id == "tavus:r_face_contract"
    assert state.external_avatar_id == "r_face_contract"
    assert state.status == "training"
    assert repository.updates[-1][1]["provider_job_id"] == state.external_job_id
    assert repository.avatar_updates[-1][1]["replica_id"] == "r_face_contract"


def test_image_mode_never_falls_through_to_video():
    service = AvatarProviderService()

    assert service._extract_tavus_training_source(
        {
            "tavus_training_mode": "image",
            "train_video_url": "https://stay.example/video.mp4",
        }
    ) is None


def test_duplicate_submission_never_calls_tavus_twice(monkeypatch):
    monkeypatch.setenv("TAVUS_API_KEY", "contract-key")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", FakeHTTPClient)
    FakeHTTPClient.last_url = None
    FakeHTTPClient.last_payload = None

    service = AvatarProviderService()
    repository = ExistingTrainingRepository()
    service._profile_repository = repository

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
    assert state.status == "failed"
    assert "already being submitted" in (state.error_message or "")
