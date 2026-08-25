from __future__ import annotations

import asyncio
import hashlib
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import pytest

import app.services.avatar_media_storage_service as storage_module
import app.services.avatar_provider_service as provider_module
from app.services.avatar_media_storage_service import (
    AvatarMediaStorageService,
)
from app.services.avatar_provider_service import (
    AvatarProviderService,
)


class FakeProviderStatusResponse:
    status_code = 200

    def json(
        self,
    ) -> dict[str, Any]:
        return {
            "video_id": "video-runtime-proof",
            "status": "ready",
            "download_url": (
                "https://media.tavus-runtime.test/"
                "generated-preview.mp4"
            ),
            "stream_url": (
                "https://stream.tavus-runtime.test/"
                "generated-preview"
            ),
            "hosted_url": (
                "https://hosted.tavus-runtime.test/"
                "generated-preview"
            ),
        }


class FakeMediaStreamResponse:
    status_code = 200

    def __init__(
        self,
        payload: bytes,
    ) -> None:
        self.payload = payload
        self.headers = {
            "content-type": "video/mp4",
            "content-length": str(
                len(payload)
            ),
        }

    async def __aenter__(
        self,
    ) -> "FakeMediaStreamResponse":
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> None:
        del exc_type
        del exc
        del traceback

    async def aiter_bytes(
        self,
        *,
        chunk_size: int,
    ):
        step = max(
            1,
            chunk_size // 7,
        )

        for cursor in range(
            0,
            len(self.payload),
            step,
        ):
            yield self.payload[
                cursor:cursor + step
            ]


class FakeHTTPClient:
    provider_poll_count = 0
    media_download_count = 0
    media_payload = b""

    def __init__(
        self,
        *args,
        **kwargs,
    ) -> None:
        del args
        del kwargs

    async def __aenter__(
        self,
    ) -> "FakeHTTPClient":
        return self

    async def __aexit__(
        self,
        exc_type,
        exc,
        traceback,
    ) -> None:
        del exc_type
        del exc
        del traceback

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str],
    ) -> FakeProviderStatusResponse:
        assert (
            url
            == "https://tavusapi.com/v2/videos/"
            "video-runtime-proof"
        )

        assert (
            headers["x-api-key"]
            == "runtime-proof-api-key"
        )

        type(self).provider_poll_count += 1

        return FakeProviderStatusResponse()

    def stream(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
    ) -> FakeMediaStreamResponse:
        assert method == "GET"

        assert (
            url
            == "https://media.tavus-runtime.test/"
            "generated-preview.mp4"
        )

        assert (
            "video/mp4"
            in headers["Accept"]
        )

        type(self).media_download_count += 1

        return FakeMediaStreamResponse(
            type(self).media_payload
        )


class FakeProfileRepository:
    def __init__(
        self,
        *,
        job_id: UUID,
        profile_id: UUID,
        package_record_id: UUID,
    ) -> None:
        self.job: dict[str, Any] = {
            "job_id": job_id,
            "profile_id": profile_id,
            "training_version": 4,
            "current_training_version": 4,
            "package_record_id": package_record_id,
            "provider": "tavus",
            "replica_id": "replica-runtime-proof",
            "current_replica_id": (
                "replica-runtime-proof"
            ),
            "current_avatar_status": "ready",
            "provider_video_id": (
                "video-runtime-proof"
            ),
            "status": "generating",
            "generated_asset_id": None,
            "media_sha256": None,
            "media_content_type": None,
            "media_size_bytes": None,
        }

        self.updates: list[
            dict[str, Any]
        ] = []

    def get_generated_preview_job_by_external_id(
        self,
        *,
        provider: str,
        external_job_id: str,
    ) -> dict[str, Any]:
        assert provider == "tavus"

        assert external_job_id in {
            "tavus:video:video-runtime-proof",
            "video-runtime-proof",
        }

        return dict(
            self.job
        )

    def update_generated_preview_job(
        self,
        **values: Any,
    ) -> dict[str, Any]:
        assert (
            values["job_id"]
            == self.job["job_id"]
        )

        self.updates.append(
            dict(values)
        )

        for key, value in values.items():
            if key == "job_id":
                continue

            if value is not None:
                self.job[key] = value

        return dict(
            self.job
        )


class FakeEvidenceRepository:
    def __init__(
        self,
    ) -> None:
        self.calls: list[
            dict[str, Any]
        ] = []

    def upsert_uploaded_asset(
        self,
        **values: Any,
    ) -> SimpleNamespace:
        self.calls.append(
            dict(values)
        )

        return SimpleNamespace(
            asset_id=values["asset_id"]
        )


def _valid_mp4_payload() -> bytes:
    return (
        b"\x00\x00\x00\x18"
        b"ftyp"
        b"isom"
        b"\x00\x00\x02\x00"
        b"isom"
        b"iso2"
        b"\x00\x00\x00\x10"
        b"free"
        b"STAY-RUNTIME"
        b"\x00\x00\x00\x18"
        b"mdat"
        b"generated-preview-proof"
    )


def _configure_network_mocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        provider_module.httpx,
        "AsyncClient",
        FakeHTTPClient,
    )

    monkeypatch.setattr(
        storage_module.httpx,
        "AsyncClient",
        FakeHTTPClient,
    )

    monkeypatch.setattr(
        storage_module.socket,
        "getaddrinfo",
        lambda host, port, *, type: [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (
                    "93.184.216.34",
                    port,
                ),
            )
        ],
    )


def _storage(
    tmp_path: Path,
) -> AvatarMediaStorageService:
    return AvatarMediaStorageService(
        storage_root=tmp_path,
        signing_secret=(
            "runtime-proof-signing-secret-"
            "with-sufficient-length"
        ),
        public_base_url=(
            "https://stay.runtime.test"
        ),
        environment="test",
    )


def _asset_id_from_signed_url(
    signed_url: str,
) -> str:
    path_parts = [
        part
        for part in urlsplit(
            signed_url
        ).path.split("/")
        if part
    ]

    assert path_parts[-4:-1] == [
        "avatar-media",
        "public",
        "assets",
    ]

    asset_id = path_parts[-1]

    UUID(
        asset_id
    )

    return asset_id


def test_generated_preview_binary_end_to_end_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def scenario() -> None:
        job_id = uuid4()
        profile_id = uuid4()
        package_record_id = uuid4()

        media_payload = (
            _valid_mp4_payload()
        )

        expected_sha256 = (
            hashlib.sha256(
                media_payload
            ).hexdigest()
        )

        FakeHTTPClient.provider_poll_count = 0
        FakeHTTPClient.media_download_count = 0
        FakeHTTPClient.media_payload = (
            media_payload
        )

        monkeypatch.setenv(
            "TAVUS_API_KEY",
            "runtime-proof-api-key",
        )

        _configure_network_mocks(
            monkeypatch
        )

        media_storage = _storage(
            tmp_path
        )

        expected_asset_id = str(
            media_storage._resolve_asset_id(
                profile_id=str(
                    profile_id
                ),
                upload_id=str(
                    job_id
                ),
            )
        )

        profile_repository = (
            FakeProfileRepository(
                job_id=job_id,
                profile_id=profile_id,
                package_record_id=(
                    package_record_id
                ),
            )
        )

        evidence_repository = (
            FakeEvidenceRepository()
        )

        service = AvatarProviderService()

        service._profile_repository = (
            profile_repository
        )

        service._media_storage_service = (
            media_storage
        )

        service._evidence_repository = (
            evidence_repository
        )

        first_state = await (
            service
            .fetch_tavus_video_status(
                "tavus:video:"
                "video-runtime-proof"
            )
        )

        assert first_state.status == "ready"
        assert first_state.error_message is None

        assert (
            first_state.external_avatar_id
            == "replica-runtime-proof"
        )

        assert first_state.preview_url is not None

        returned_asset_id = (
            _asset_id_from_signed_url(
                first_state.preview_url
            )
        )

        assert (
            returned_asset_id
            == expected_asset_id
        )

        assert first_state.preview_url.startswith(
            "https://stay.runtime.test/"
            "v1/avatar-media/public/assets/"
            f"{expected_asset_id}"
            "?expires="
        )

        assert (
            "signature="
            in first_state.preview_url
        )

        assert (
            "media.tavus-runtime.test"
            not in first_state.preview_url
        )

        assert (
            "hosted.tavus-runtime.test"
            not in first_state.preview_url
        )

        assert (
            FakeHTTPClient.provider_poll_count
            == 1
        )

        assert (
            FakeHTTPClient.media_download_count
            == 1
        )

        metadata = (
            media_storage.get_metadata(
                expected_asset_id
            )
        )

        assert (
            metadata.asset_id
            == expected_asset_id
        )

        assert (
            metadata.profile_id
            == str(profile_id)
        )

        assert (
            metadata.asset_type
            == "generated_preview"
        )

        assert (
            metadata.content_type
            == "video/mp4"
        )

        assert (
            metadata.size_bytes
            == len(media_payload)
        )

        stored_path = Path(
            metadata.storage_path
        )

        assert stored_path.exists()

        assert (
            stored_path.read_bytes()
            == media_payload
        )

        assert (
            hashlib.sha256(
                stored_path.read_bytes()
            ).hexdigest()
            == expected_sha256
        )

        assert len(
            evidence_repository.calls
        ) == 1

        evidence_call = (
            evidence_repository.calls[0]
        )

        assert (
            evidence_call["asset_id"]
            == UUID(expected_asset_id)
        )

        assert (
            evidence_call["profile_id"]
            == profile_id
        )

        assert (
            evidence_call["asset_type"]
            == "generated_preview"
        )

        assert (
            evidence_call["evidence_kind"]
            == "generated_preview"
        )

        assert (
            evidence_call["analysis_version"]
            == "generated-preview-evidence-v1"
        )

        analysis_metadata = (
            evidence_call[
                "analysis_metadata"
            ]
        )

        assert (
            analysis_metadata[
                "biometric_evaluation"
            ]
            == "not_performed"
        )

        assert (
            analysis_metadata[
                "identity_verification"
            ]
            == "not_performed"
        )

        assert (
            analysis_metadata[
                "quality_evaluation"
            ]
            == "not_performed"
        )

        source_metadata = (
            evidence_call[
                "source_metadata"
            ]
        )

        assert (
            source_metadata[
                "media_sha256"
            ]
            == expected_sha256
        )

        assert (
            source_metadata["provider"]
            == "tavus"
        )

        assert (
            source_metadata[
                "provider_video_id"
            ]
            == "video-runtime-proof"
        )

        assert (
            source_metadata[
                "generated_preview_job_id"
            ]
            == str(job_id)
        )

        ready_updates = [
            update
            for update
            in profile_repository.updates
            if update.get("status")
            == "ready"
        ]

        assert len(
            ready_updates
        ) == 1

        ready_update = (
            ready_updates[0]
        )

        assert (
            ready_update[
                "generated_asset_id"
            ]
            == UUID(expected_asset_id)
        )

        assert (
            ready_update[
                "media_sha256"
            ]
            == expected_sha256
        )

        assert (
            ready_update[
                "media_content_type"
            ]
            == "video/mp4"
        )

        assert (
            ready_update[
                "media_size_bytes"
            ]
            == len(media_payload)
        )

        assert (
            profile_repository.job[
                "status"
            ]
            == "ready"
        )

        assert (
            profile_repository.job[
                "generated_asset_id"
            ]
            == UUID(expected_asset_id)
        )

        second_state = await (
            service
            .fetch_tavus_video_status(
                "tavus:video:"
                "video-runtime-proof"
            )
        )

        assert second_state.status == "ready"
        assert second_state.error_message is None
        assert second_state.preview_url is not None

        second_asset_id = (
            _asset_id_from_signed_url(
                second_state.preview_url
            )
        )

        assert (
            second_asset_id
            == expected_asset_id
        )

        assert second_state.preview_url.startswith(
            "https://stay.runtime.test/"
            "v1/avatar-media/public/assets/"
            f"{expected_asset_id}"
            "?expires="
        )

        assert (
            FakeHTTPClient.provider_poll_count
            == 1
        )

        assert (
            FakeHTTPClient.media_download_count
            == 1
        )

        assert len(
            evidence_repository.calls
        ) == 1

        assert (
            stored_path.read_bytes()
            == media_payload
        )

    asyncio.run(
        scenario()
    )


def test_generated_preview_asset_id_is_profile_bound(
    tmp_path: Path,
):
    service = _storage(
        tmp_path
    )

    upload_id = str(
        uuid4()
    )

    first_profile = str(
        uuid4()
    )

    second_profile = str(
        uuid4()
    )

    first_asset_id = (
        service._resolve_asset_id(
            profile_id=first_profile,
            upload_id=upload_id,
        )
    )

    repeated_first_asset_id = (
        service._resolve_asset_id(
            profile_id=first_profile,
            upload_id=upload_id,
        )
    )

    second_asset_id = (
        service._resolve_asset_id(
            profile_id=second_profile,
            upload_id=upload_id,
        )
    )

    assert (
        first_asset_id
        == repeated_first_asset_id
    )

    assert (
        first_asset_id
        != second_asset_id
    )


def test_generated_preview_binary_runtime_rejects_non_mp4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    async def scenario() -> None:
        FakeHTTPClient.provider_poll_count = 0
        FakeHTTPClient.media_download_count = 0
        FakeHTTPClient.media_payload = (
            b"this-is-not-an-mp4"
        )

        _configure_network_mocks(
            monkeypatch
        )

        service = _storage(
            tmp_path
        )

        with pytest.raises(
            RuntimeError,
            match="valid MP4 signature",
        ):
            await (
                service
                .ingest_remote_generated_preview(
                    profile_id=str(
                        uuid4()
                    ),
                    title=(
                        "Invalid preview"
                    ),
                    source_url=(
                        "https://media."
                        "tavus-runtime.test/"
                        "generated-preview.mp4"
                    ),
                    upload_id=str(
                        uuid4()
                    ),
                )
            )

        assert not list(
            tmp_path.rglob(
                "*.mp4"
            )
        )

        assert (
            FakeHTTPClient.media_download_count
            == 1
        )

    asyncio.run(
        scenario()
    )
