import os
import json
import hmac
import uuid
import time
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List

from fastapi import UploadFile

from app.schemas.avatar_media import (
    AvatarMediaUploadResponse,
    AvatarMediaMetadata,
    AvatarMediaSignResponse,
    AvatarMediaListResponse,
)


class AvatarMediaStorageService:
    def __init__(self):
        self.storage_root = Path(
            os.getenv("AVATAR_MEDIA_STORAGE_ROOT", "./storage/avatar-media")
        ).resolve()

        self.signing_secret = os.getenv(
            "AVATAR_MEDIA_SIGNING_SECRET",
            "rememberme-dev-media-secret-change-me"
        )

        self.max_file_size_bytes = int(
            os.getenv("AVATAR_MEDIA_MAX_FILE_SIZE_BYTES", "52428800")
        )

        self.allowed_asset_types = {
            "image",
            "video",
            "voice",
            "reference",
            "training_sample"
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
            "application/octet-stream"
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
            ".aac": "audio/aac"
        }

        self.storage_root.mkdir(parents=True, exist_ok=True)

    async def upload(
        self,
        profile_id: str,
        asset_type: str,
        title: str,
        file: UploadFile,
        base_url: str,
    ) -> AvatarMediaUploadResponse:
        self._validate_profile_id(profile_id)
        self._validate_asset_type(asset_type)

        normalized_content_type = self._normalize_content_type(
            filename=file.filename,
            content_type=file.content_type
        )

        self._validate_content_type(
            content_type=normalized_content_type,
            filename=file.filename
        )

        self._validate_asset_type_matches_content_type(
            asset_type=asset_type,
            content_type=normalized_content_type
        )

        asset_id = str(uuid.uuid4())
        extension = self._safe_extension(file.filename)
        filename = f"{asset_id}{extension}"

        profile_dir = self.storage_root / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)

        file_path = profile_dir / filename
        size_bytes = 0

        with file_path.open("wb") as output:
            while True:
                chunk = await file.read(1024 * 1024)

                if not chunk:
                    break

                size_bytes += len(chunk)

                if size_bytes > self.max_file_size_bytes:
                    self._delete_file_if_exists(file_path)
                    raise RuntimeError("Uploaded media exceeds maximum file size.")

                output.write(chunk)

        metadata = AvatarMediaMetadata(
            asset_id=asset_id,
            profile_id=profile_id,
            asset_type=asset_type,
            title=title.strip() or "Untitled media",
            filename=filename,
            content_type=normalized_content_type,
            size_bytes=size_bytes,
            storage_path=str(file_path),
            created_at=datetime.now(timezone.utc).isoformat()
        )

        self._write_metadata(metadata)

        expires = 900
        signed_url = self.sign_download_url(
            asset_id=asset_id,
            base_url=base_url,
            expires_in_seconds=expires
        ).signed_url

        return AvatarMediaUploadResponse(
            asset_id=asset_id,
            profile_id=profile_id,
            asset_type=asset_type,
            title=metadata.title,
            content_type=metadata.content_type,
            size_bytes=size_bytes,
            signed_url=signed_url,
            expires_in_seconds=expires
        )

    def sign_download_url(
        self,
        asset_id: str,
        base_url: str,
        expires_in_seconds: int = 900
    ) -> AvatarMediaSignResponse:
        metadata = self.get_metadata(asset_id)

        safe_expires = max(60, min(expires_in_seconds, 3600))
        expires_at = int(time.time()) + safe_expires
        signature = self._signature(asset_id=asset_id, expires_at=expires_at)

        signed_url = (
            f"{base_url.rstrip('/')}/v1/avatar-media/assets/{asset_id}"
            f"?expires={expires_at}&signature={signature}"
        )

        return AvatarMediaSignResponse(
            asset_id=metadata.asset_id,
            signed_url=signed_url,
            expires_in_seconds=expires_at - int(time.time())
        )

    def verify_download_signature(
        self,
        asset_id: str,
        expires: int,
        signature: str
    ) -> AvatarMediaMetadata:
        if expires < int(time.time()):
            raise RuntimeError("Signed media URL has expired.")

        expected = self._signature(asset_id=asset_id, expires_at=expires)

        if not hmac.compare_digest(expected, signature):
            raise RuntimeError("Invalid signed media URL.")

        return self.get_metadata(asset_id)

    def get_metadata(self, asset_id: str) -> AvatarMediaMetadata:
        for metadata_file in self.storage_root.glob("*/metadata/*.json"):
            with metadata_file.open("r", encoding="utf-8") as input_file:
                data = json.load(input_file)

            if data.get("asset_id") == asset_id:
                return AvatarMediaMetadata(**data)

        raise RuntimeError("Media asset not found.")

    def list_profile_assets(self, profile_id: str) -> AvatarMediaListResponse:
        self._validate_profile_id(profile_id)

        metadata_dir = self.storage_root / profile_id / "metadata"
        assets: List[AvatarMediaMetadata] = []

        if metadata_dir.exists():
            for metadata_file in sorted(metadata_dir.glob("*.json")):
                with metadata_file.open("r", encoding="utf-8") as input_file:
                    assets.append(AvatarMediaMetadata(**json.load(input_file)))

        return AvatarMediaListResponse(
            profile_id=profile_id,
            assets=assets
        )

    def _write_metadata(self, metadata: AvatarMediaMetadata) -> None:
        metadata_dir = self.storage_root / metadata.profile_id / "metadata"
        metadata_dir.mkdir(parents=True, exist_ok=True)

        metadata_file = metadata_dir / f"{metadata.asset_id}.json"

        with metadata_file.open("w", encoding="utf-8") as output:
            json.dump(metadata.model_dump(), output, indent=2)

    def _signature(self, asset_id: str, expires_at: int) -> str:
        payload = f"{asset_id}:{expires_at}".encode("utf-8")
        secret = self.signing_secret.encode("utf-8")

        return hmac.new(
            secret,
            payload,
            hashlib.sha256
        ).hexdigest()

    def _normalize_content_type(
        self,
        filename: Optional[str],
        content_type: Optional[str]
    ) -> str:
        extension = self._safe_extension(filename)

        if extension in self.extension_content_type_map:
            return self.extension_content_type_map[extension]

        if content_type:
            return content_type.lower().strip()

        return "application/octet-stream"

    def _validate_profile_id(self, profile_id: str) -> None:
        if not profile_id or "/" in profile_id or ".." in profile_id:
            raise RuntimeError("Invalid profile_id.")

    def _validate_asset_type(self, asset_type: str) -> None:
        if asset_type not in self.allowed_asset_types:
            raise RuntimeError(f"Unsupported avatar media asset_type: {asset_type}")

    def _validate_content_type(
        self,
        content_type: Optional[str],
        filename: Optional[str]
    ) -> None:
        normalized = self._normalize_content_type(
            filename=filename,
            content_type=content_type
        )

        if normalized not in self.allowed_content_types:
            raise RuntimeError(f"Unsupported media content type: {normalized}")

    def _validate_asset_type_matches_content_type(
        self,
        asset_type: str,
        content_type: str
    ) -> None:
        if asset_type in ["image", "reference"] and not content_type.startswith("image/"):
            raise RuntimeError(
                f"Asset type '{asset_type}' requires image content, got {content_type}."
            )

        if asset_type in ["video", "training_sample"] and not content_type.startswith("video/"):
            raise RuntimeError(
                f"Asset type '{asset_type}' requires video content, got {content_type}."
            )

        if asset_type == "voice" and not content_type.startswith("audio/"):
            raise RuntimeError(
                f"Asset type 'voice' requires audio content, got {content_type}."
            )

    def _safe_extension(self, filename: Optional[str]) -> str:
        if not filename or "." not in filename:
            return ".bin"

        extension = "." + filename.rsplit(".", 1)[-1].lower().strip()

        if len(extension) > 12:
            return ".bin"

        if "/" in extension or "\\" in extension or ".." in extension:
            return ".bin"

        return extension

    def _delete_file_if_exists(self, file_path: Path) -> None:
        try:
            file_path.unlink(missing_ok=True)
        except TypeError:
            if file_path.exists():
                file_path.unlink()
