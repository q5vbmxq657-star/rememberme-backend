from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import tempfile
import time
import uuid

from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit, urlunsplit

from fastapi import UploadFile

from app.schemas.avatar_media import (
    AvatarMediaListResponse,
    AvatarMediaMetadata,
    AvatarMediaSignResponse,
    AvatarMediaStorageHealthResponse,
    AvatarMediaUploadResponse,
)


class AvatarMediaStorageConfigurationError(
    RuntimeError
):
    """Raised when media storage is configured unsafely."""


class AvatarMediaStorageService:

    development_signing_secret = (
        "rememberme-dev-media-secret-change-me"
    )

    def __init__(
        self,
        *,
        storage_root: Optional[Path | str] = None,
        signing_secret: Optional[str] = None,
        public_base_url: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> None:
        self.environment = (
            environment
            or os.getenv("APP_ENV")
            or os.getenv("ENVIRONMENT")
            or "development"
        ).strip().lower()

        configured_storage_root = (
            str(storage_root)
            if storage_root is not None
            else os.getenv(
                "AVATAR_MEDIA_STORAGE_ROOT",
                "./storage/avatar-media",
            )
        )

        self.storage_root = Path(
            configured_storage_root
        ).expanduser().resolve()

        self.storage_root_is_explicit = (
            storage_root is not None
            or bool(
                os.getenv(
                    "AVATAR_MEDIA_STORAGE_ROOT",
                    "",
                ).strip()
            )
        )

        self.signing_secret = (
            signing_secret
            if signing_secret is not None
            else os.getenv(
                "AVATAR_MEDIA_SIGNING_SECRET",
                self.development_signing_secret,
            )
        ).strip()

        configured_public_base_url = (
            public_base_url
            if public_base_url is not None
            else os.getenv(
                "PUBLIC_BASE_URL",
                "",
            )
        )

        self.public_base_url = (
            self._normalize_public_base_url(
                configured_public_base_url
            )
            if configured_public_base_url
            else None
        )

        self.max_file_size_bytes = int(
            os.getenv(
                "AVATAR_MEDIA_MAX_FILE_SIZE_BYTES",
                "52428800",
            )
        )

        self.allowed_asset_types = {
            "image",
            "video",
            "voice",
            "audio",
            "reference",
            "training_sample",
            "generated_preview",
            "trained_replica",
        }

        self.allowed_content_types = {
            "image/jpeg",
            "image/jpg",
            "image/png",
            "image/webp",
            "image/heic",
            "image/heif",
            "video/mp4",
            "video/quicktime",
            "video/x-m4v",
            "audio/mpeg",
            "audio/mp3",
            "audio/mp4",
            "audio/m4a",
            "audio/wav",
            "audio/x-wav",
            "audio/aac",
            "audio/x-m4a",
            "application/octet-stream",
        }

        self.extension_content_type_map = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
            ".heic": "image/heic",
            ".heif": "image/heif",
            ".mov": "video/quicktime",
            ".mp4": "video/mp4",
            ".m4v": "video/x-m4v",
            ".mp3": "audio/mpeg",
            ".m4a": "audio/m4a",
            ".wav": "audio/wav",
            ".aac": "audio/aac",
        }

        self._validate_configuration()

        self.storage_root.mkdir(
            parents=True,
            exist_ok=True,
        )

    @property
    def is_production(self) -> bool:
        return self.environment in {
            "production",
            "prod",
        }

    async def upload(
        self,
        profile_id: str,
        asset_type: str,
        title: str,
        file: UploadFile,
        base_url: Optional[str] = None,
        upload_id: Optional[str] = None,
    ) -> AvatarMediaUploadResponse:
        self._validate_profile_id(
            profile_id
        )
        self._validate_asset_type(
            asset_type
        )

        normalized_content_type = (
            self._normalize_content_type(
                filename=file.filename,
                content_type=file.content_type,
            )
        )

        self._validate_content_type(
            content_type=normalized_content_type,
            filename=file.filename,
        )

        self._validate_asset_type_matches_content_type(
            asset_type=asset_type,
            content_type=normalized_content_type,
        )

        asset_id = self._resolve_asset_id(
            profile_id=profile_id,
            upload_id=upload_id,
        )

        extension = self._safe_extension(
            file.filename
        )
        filename = (
            f"{asset_id}{extension}"
        )

        profile_dir = (
            self.storage_root
            / profile_id
        )
        profile_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        file_path = (
            profile_dir
            / filename
        )

        size_bytes = 0

        try:
            with file_path.open(
                "wb"
            ) as output:
                while True:
                    chunk = await file.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    size_bytes += len(
                        chunk
                    )

                    if (
                        size_bytes
                        > self.max_file_size_bytes
                    ):
                        raise RuntimeError(
                            "Uploaded media exceeds "
                            "maximum file size."
                        )

                    output.write(
                        chunk
                    )

            metadata = AvatarMediaMetadata(
                asset_id=asset_id,
                profile_id=profile_id,
                asset_type=asset_type,
                title=(
                    title.strip()
                    or "Untitled media"
                ),
                filename=filename,
                content_type=(
                    normalized_content_type
                ),
                size_bytes=size_bytes,
                storage_path=str(
                    file_path
                ),
                created_at=(
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
            )

            self._write_metadata(
                metadata
            )

        except Exception:
            self._delete_file_if_exists(
                file_path
            )

            metadata_file = (
                profile_dir
                / "metadata"
                / f"{asset_id}.json"
            )

            self._delete_file_if_exists(
                metadata_file
            )

            raise

        expires = 900

        signed_url = (
            self.sign_download_url(
                asset_id=asset_id,
                base_url=base_url,
                expires_in_seconds=expires,
            )
            .signed_url
        )

        return AvatarMediaUploadResponse(
            asset_id=asset_id,
            profile_id=profile_id,
            asset_type=asset_type,
            title=metadata.title,
            content_type=(
                metadata.content_type
            ),
            size_bytes=size_bytes,
            signed_url=signed_url,
            expires_in_seconds=expires,
        )

    def sign_download_url(
        self,
        asset_id: str,
        base_url: Optional[str] = None,
        expires_in_seconds: int = 900,
    ) -> AvatarMediaSignResponse:
        metadata = self.get_metadata(
            asset_id
        )

        resolved_base_url = (
            self.resolve_external_base_url(
                request_base_url=base_url
            )
        )

        safe_expires = max(
            60,
            min(
                expires_in_seconds,
                3600,
            ),
        )

        expires_at = (
            int(
                time.time()
            )
            + safe_expires
        )

        signature = self._signature(
            asset_id=asset_id,
            expires_at=expires_at,
        )

        signed_url = (
            f"{resolved_base_url}"
            f"/v1/avatar-media/assets/"
            f"{asset_id}"
            f"?expires={expires_at}"
            f"&signature={signature}"
        )

        return AvatarMediaSignResponse(
            asset_id=metadata.asset_id,
            signed_url=signed_url,
            expires_in_seconds=(
                expires_at
                - int(
                    time.time()
                )
            ),
        )

    def resolve_external_base_url(
        self,
        *,
        request_base_url: Optional[str],
    ) -> str:
        if self.public_base_url:
            return self.public_base_url

        normalized_request_url = (
            self._normalize_request_base_url(
                request_base_url
            )
        )

        if self.is_production:
            raise (
                AvatarMediaStorageConfigurationError(
                    "PUBLIC_BASE_URL is required "
                    "in production."
                )
            )

        return normalized_request_url

    def verify_download_signature(
        self,
        asset_id: str,
        expires: int,
        signature: str,
    ) -> AvatarMediaMetadata:
        if expires < int(
            time.time()
        ):
            raise RuntimeError(
                "Signed media URL has expired."
            )

        expected = self._signature(
            asset_id=asset_id,
            expires_at=expires,
        )

        if not hmac.compare_digest(
            expected,
            signature,
        ):
            raise RuntimeError(
                "Invalid signed media URL."
            )

        return self.get_metadata(
            asset_id
        )

    def get_metadata(
        self,
        asset_id: str,
    ) -> AvatarMediaMetadata:
        self._validate_asset_id(
            asset_id
        )

        for metadata_file in (
            self.storage_root
            .glob(
                "*/metadata/*.json"
            )
        ):
            with metadata_file.open(
                "r",
                encoding="utf-8",
            ) as input_file:
                data = json.load(
                    input_file
                )

            if (
                data.get(
                    "asset_id"
                )
                == asset_id
            ):
                return (
                    AvatarMediaMetadata(
                        **data
                    )
                )

        raise RuntimeError(
            "Media asset not found."
        )

    def list_profile_assets(
        self,
        profile_id: str,
    ) -> AvatarMediaListResponse:
        self._validate_profile_id(
            profile_id
        )

        metadata_dir = (
            self.storage_root
            / profile_id
            / "metadata"
        )

        assets: List[
            AvatarMediaMetadata
        ] = []

        if metadata_dir.exists():
            for metadata_file in sorted(
                metadata_dir.glob(
                    "*.json"
                )
            ):
                with metadata_file.open(
                    "r",
                    encoding="utf-8",
                ) as input_file:
                    assets.append(
                        AvatarMediaMetadata(
                            **json.load(
                                input_file
                            )
                        )
                    )

        return AvatarMediaListResponse(
            profile_id=profile_id,
            assets=assets,
        )

    def storage_health(
        self,
    ) -> AvatarMediaStorageHealthResponse:
        exists = (
            self.storage_root.exists()
        )
        is_directory = (
            self.storage_root.is_dir()
        )

        writable = False
        error_message: Optional[str] = None

        try:
            self.storage_root.mkdir(
                parents=True,
                exist_ok=True,
            )

            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=self.storage_root,
                prefix=".rememberme-health-",
                delete=False,
            ) as temporary_file:
                temporary_path = Path(
                    temporary_file.name
                )
                temporary_file.write(
                    b"rememberme-storage-health"
                )
                temporary_file.flush()
                os.fsync(
                    temporary_file.fileno()
                )

            temporary_path.unlink(
                missing_ok=True
            )

            exists = True
            is_directory = True
            writable = True

        except Exception as error:
            error_message = str(
                error
            )

        public_url_configured = bool(
            self.public_base_url
        )

        secure_public_url = (
            bool(
                self.public_base_url
                and self.public_base_url
                .startswith(
                    "https://"
                )
            )
        )

        signing_secret_secure = (
            bool(
                self.signing_secret
            )
            and self.signing_secret
            != self.development_signing_secret
            and len(
                self.signing_secret
            )
            >= 32
        )

        production_ready = all(
            (
                exists,
                is_directory,
                writable,
                self.storage_root_is_explicit,
                public_url_configured,
                secure_public_url,
                signing_secret_secure,
            )
        )

        return (
            AvatarMediaStorageHealthResponse(
                status=(
                    "ready"
                    if (
                        production_ready
                        or not self.is_production
                    )
                    else "degraded"
                ),
                environment=(
                    self.environment
                ),
                storage_backend=(
                    "local_private"
                ),
                storage_root=str(
                    self.storage_root
                ),
                storage_root_explicit=(
                    self.storage_root_is_explicit
                ),
                storage_exists=exists,
                storage_is_directory=(
                    is_directory
                ),
                storage_writable=writable,
                public_base_url=(
                    self.public_base_url
                ),
                public_base_url_configured=(
                    public_url_configured
                ),
                secure_public_url=(
                    secure_public_url
                ),
                signing_secret_configured=(
                    bool(
                        self.signing_secret
                    )
                ),
                signing_secret_secure=(
                    signing_secret_secure
                ),
                production_ready=(
                    production_ready
                ),
                error_message=(
                    error_message
                ),
            )
        )

    def delete_asset(
        self,
        asset_id: str,
    ) -> None:
        self._validate_asset_id(
            asset_id
        )

        metadata = None

        try:
            metadata = self.get_metadata(
                asset_id
            )
        except Exception:
            metadata = None

        if metadata is not None:
            storage_path = Path(
                metadata.storage_path
            ).expanduser().resolve()

            if not self._is_within_storage_root(
                storage_path
            ):
                raise RuntimeError(
                    "Refusing to delete media "
                    "outside storage root."
                )

            self._delete_file_if_exists(
                storage_path
            )

            metadata_file = (
                self.storage_root
                / metadata.profile_id
                / "metadata"
                / f"{asset_id}.json"
            )

            self._delete_file_if_exists(
                metadata_file
            )

            return

        for candidate in (
            self.storage_root.glob(
                f"*/{asset_id}.*"
            )
        ):
            if candidate.is_file():
                self._delete_file_if_exists(
                    candidate
                )

        for candidate in (
            self.storage_root.glob(
                f"*/metadata/{asset_id}.json"
            )
        ):
            if candidate.is_file():
                self._delete_file_if_exists(
                    candidate
                )

    def _write_metadata(
        self,
        metadata: AvatarMediaMetadata,
    ) -> None:
        metadata_dir = (
            self.storage_root
            / metadata.profile_id
            / "metadata"
        )

        metadata_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        metadata_file = (
            metadata_dir
            / f"{metadata.asset_id}.json"
        )

        temporary_file = (
            metadata_dir
            / (
                f".{metadata.asset_id}."
                f"{uuid.uuid4().hex}.tmp"
            )
        )

        try:
            with temporary_file.open(
                "w",
                encoding="utf-8",
            ) as output:
                json.dump(
                    metadata.model_dump(),
                    output,
                    indent=2,
                )
                output.flush()
                os.fsync(
                    output.fileno()
                )

            temporary_file.replace(
                metadata_file
            )

        finally:
            self._delete_file_if_exists(
                temporary_file
            )

    def _signature(
        self,
        asset_id: str,
        expires_at: int,
    ) -> str:
        payload = (
            f"{asset_id}:{expires_at}"
            .encode(
                "utf-8"
            )
        )

        secret = (
            self.signing_secret
            .encode(
                "utf-8"
            )
        )

        return hmac.new(
            secret,
            payload,
            hashlib.sha256,
        ).hexdigest()

    def _validate_configuration(
        self,
    ) -> None:
        if not self.signing_secret:
            raise (
                AvatarMediaStorageConfigurationError(
                    "AVATAR_MEDIA_SIGNING_SECRET "
                    "must not be empty."
                )
            )

        if (
            self.is_production
            and self.signing_secret
            == self.development_signing_secret
        ):
            raise (
                AvatarMediaStorageConfigurationError(
                    "Production must configure "
                    "AVATAR_MEDIA_SIGNING_SECRET."
                )
            )

        if (
            self.is_production
            and len(
                self.signing_secret
            )
            < 32
        ):
            raise (
                AvatarMediaStorageConfigurationError(
                    "Production signing secret "
                    "must contain at least "
                    "32 characters."
                )
            )

        if (
            self.is_production
            and not self.storage_root_is_explicit
        ):
            raise (
                AvatarMediaStorageConfigurationError(
                    "Production must configure "
                    "AVATAR_MEDIA_STORAGE_ROOT."
                )
            )

        if (
            self.is_production
            and not self.public_base_url
        ):
            raise (
                AvatarMediaStorageConfigurationError(
                    "Production must configure "
                    "PUBLIC_BASE_URL."
                )
            )

    def _normalize_public_base_url(
        self,
        value: str,
    ) -> str:
        normalized = value.strip()

        if not normalized:
            raise (
                AvatarMediaStorageConfigurationError(
                    "PUBLIC_BASE_URL must not "
                    "be empty."
                )
            )

        parts = urlsplit(
            normalized
        )

        if parts.scheme.lower() != "https":
            raise (
                AvatarMediaStorageConfigurationError(
                    "PUBLIC_BASE_URL must use "
                    "https://."
                )
            )

        if (
            not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise (
                AvatarMediaStorageConfigurationError(
                    "PUBLIC_BASE_URL is invalid."
                )
            )

        path = parts.path.rstrip(
            "/"
        )

        return urlunsplit(
            (
                "https",
                parts.netloc.lower(),
                path,
                "",
                "",
            )
        )

    def _normalize_request_base_url(
        self,
        value: Optional[str],
    ) -> str:
        normalized = (
            value or ""
        ).strip()

        if not normalized:
            raise (
                AvatarMediaStorageConfigurationError(
                    "Request base URL is missing."
                )
            )

        parts = urlsplit(
            normalized
        )

        scheme = parts.scheme.lower()
        hostname = (
            parts.hostname or ""
        ).lower()

        local_hosts = {
            "localhost",
            "127.0.0.1",
            "::1",
            "testserver",
        }

        if scheme not in {
            "http",
            "https",
        }:
            raise (
                AvatarMediaStorageConfigurationError(
                    "Unsupported request URL scheme."
                )
            )

        is_private_development_host = False

        try:
            host_address = ipaddress.ip_address(
                hostname
            )
            is_private_development_host = (
                not self.is_production
                and (
                    host_address.is_private
                    or host_address.is_loopback
                    or host_address.is_link_local
                )
            )
        except ValueError:
            is_private_development_host = False

        if (
            scheme == "http"
            and hostname
            not in local_hosts
            and not is_private_development_host
        ):
            raise (
                AvatarMediaStorageConfigurationError(
                    "Non-local signed URLs "
                    "must use HTTPS."
                )
            )

        if (
            not parts.netloc
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
        ):
            raise (
                AvatarMediaStorageConfigurationError(
                    "Request base URL is invalid."
                )
            )

        path = parts.path.rstrip(
            "/"
        )

        return urlunsplit(
            (
                scheme,
                parts.netloc,
                path,
                "",
                "",
            )
        )

    def _normalize_content_type(
        self,
        filename: Optional[str],
        content_type: Optional[str],
    ) -> str:
        extension = self._safe_extension(
            filename
        )

        if (
            extension
            in self.extension_content_type_map
        ):
            return (
                self.extension_content_type_map[
                    extension
                ]
            )

        if content_type:
            return (
                content_type
                .lower()
                .strip()
            )

        return "application/octet-stream"

    def _validate_profile_id(
        self,
        profile_id: str,
    ) -> None:
        try:
            uuid.UUID(
                profile_id
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as error:
            raise RuntimeError(
                "Invalid profile_id."
            ) from error

    def _validate_asset_id(
        self,
        asset_id: str,
    ) -> None:
        try:
            uuid.UUID(
                asset_id
            )
        except (
            TypeError,
            ValueError,
            AttributeError,
        ) as error:
            raise RuntimeError(
                "Invalid asset_id."
            ) from error

    def _validate_asset_type(
        self,
        asset_type: str,
    ) -> None:
        if (
            asset_type
            not in self.allowed_asset_types
        ):
            raise RuntimeError(
                "Unsupported avatar media "
                f"asset_type: {asset_type}"
            )

    def _validate_content_type(
        self,
        content_type: Optional[str],
        filename: Optional[str],
    ) -> None:
        normalized = (
            self._normalize_content_type(
                filename=filename,
                content_type=content_type,
            )
        )

        if (
            normalized
            not in self.allowed_content_types
        ):
            raise RuntimeError(
                "Unsupported media content "
                f"type: {normalized}"
            )

    def _validate_asset_type_matches_content_type(
        self,
        asset_type: str,
        content_type: str,
    ) -> None:
        if (
            asset_type
            in {
                "image",
                "reference",
            }
            and not content_type.startswith(
                "image/"
            )
        ):
            raise RuntimeError(
                f"Asset type '{asset_type}' "
                "requires image content, got "
                f"{content_type}."
            )

        if (
            asset_type
            in {
                "video",
                "training_sample",
            }
            and not content_type.startswith(
                "video/"
            )
        ):
            raise RuntimeError(
                f"Asset type '{asset_type}' "
                "requires video content, got "
                f"{content_type}."
            )

        if (
            asset_type
            in {
                "voice",
                "audio",
            }
            and not content_type.startswith(
                "audio/"
            )
        ):
            raise RuntimeError(
                f"Asset type '{asset_type}' "
                "requires audio content, got "
                f"{content_type}."
            )

    def _resolve_asset_id(
        self,
        *,
        profile_id: str,
        upload_id: Optional[str],
    ) -> str:
        normalized_upload_id = (
            upload_id.strip()
            if upload_id
            else ""
        )

        if not normalized_upload_id:
            return str(
                uuid.uuid4()
            )

        if (
            len(
                normalized_upload_id
            )
            > 200
        ):
            raise RuntimeError(
                "upload_id is too long."
            )

        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                (
                    "rememberme-avatar-media:"
                    f"{profile_id}:"
                    f"{normalized_upload_id}"
                ),
            )
        )

    def _safe_extension(
        self,
        filename: Optional[str],
    ) -> str:
        if (
            not filename
            or "."
            not in filename
        ):
            return ".bin"

        extension = (
            "."
            + filename.rsplit(
                ".",
                1,
            )[-1]
            .lower()
            .strip()
        )

        if (
            len(
                extension
            )
            > 12
        ):
            return ".bin"

        if (
            "/"
            in extension
            or "\\"
            in extension
            or ".."
            in extension
        ):
            return ".bin"

        return extension

    def _is_within_storage_root(
        self,
        path: Path,
    ) -> bool:
        try:
            path.relative_to(
                self.storage_root
            )
            return True
        except ValueError:
            return False

    def _delete_file_if_exists(
        self,
        file_path: Path,
    ) -> None:
        try:
            file_path.unlink(
                missing_ok=True
            )
        except TypeError:
            if file_path.exists():
                file_path.unlink()
