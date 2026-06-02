import os
import uuid
from dataclasses import dataclass
from typing import Optional, Dict, Any

import httpx


@dataclass
class AvatarProviderJobState:
    external_job_id: str
    external_avatar_id: Optional[str]
    status: str
    preview_url: Optional[str]
    error_message: Optional[str] = None


class AvatarProviderService:
    def __init__(self) -> None:
        self._jobs: Dict[str, AvatarProviderJobState] = {}

    async def submit(
        self,
        provider: str,
        profile_id: str,
        package_record_id: str,
        package: Dict[str, Any],
    ) -> AvatarProviderJobState:
        normalized_provider = provider.strip()

        if normalized_provider in {"local", "localPreview"}:
            state = AvatarProviderJobState(
                external_job_id=f"local-preview-{uuid.uuid4()}",
                external_avatar_id=f"local-avatar-{profile_id}",
                status="ready",
                preview_url=f"local-preview://{profile_id}",
            )
            self._jobs[state.external_job_id] = state
            return state

        if normalized_provider == "tavus":
            return await self._submit_tavus_replica(
                profile_id=profile_id,
                package_record_id=package_record_id,
                package=package,
            )

        state = AvatarProviderJobState(
            external_job_id=f"{normalized_provider}-{uuid.uuid4()}",
            external_avatar_id=None,
            status="failed",
            preview_url=None,
            error_message=f"{normalized_provider} provider is not connected yet.",
        )
        self._jobs[state.external_job_id] = state
        return state

    async def status(
        self,
        external_job_id: str,
    ) -> AvatarProviderJobState:
        existing = self._jobs.get(external_job_id)

        if existing is None:
            return AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="Provider job was not found.",
            )

        if external_job_id.startswith("tavus-video:"):
            return await self.fetch_tavus_video_status(external_job_id)

        if external_job_id.startswith("tavus:"):
            return await self._fetch_tavus_status(existing)

        return existing

    async def _submit_tavus_replica(
        self,
        profile_id: str,
        package_record_id: str,
        package: Dict[str, Any],
    ) -> AvatarProviderJobState:
        api_key = os.getenv("TAVUS_API_KEY")

        if not api_key:
            state = AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="TAVUS_API_KEY is not configured.",
            )
            self._jobs[state.external_job_id] = state
            return state

        train_video_url = self._extract_tavus_training_video_url(package)

        if not train_video_url:
            state = AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="No Tavus training video URL found in avatar package. The package must contain a public HTTPS training video URL.",
            )
            self._jobs[state.external_job_id] = state
            return state

        payload: Dict[str, Any] = {
            "replica_name": f"RememberMe-{profile_id[:8]}",
            "train_video_url": train_video_url,
        }

        callback_url = os.getenv("TAVUS_CALLBACK_URL")
        if callback_url:
            payload["callback_url"] = callback_url

        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://tavusapi.com/v2/replicas",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                },
                json=payload,
            )

        if response.status_code >= 400:
            normalized_error = self._normalize_tavus_error(
                status_code=response.status_code,
                response_text=response.text,
            )

            state = AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=normalized_error,
            )
            self._jobs[state.external_job_id] = state
            return state

        data = response.json()
        replica_id = data.get("replica_id") or data.get("id")

        if not replica_id:
            state = AvatarProviderJobState(
                external_job_id=f"tavus:{uuid.uuid4()}",
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="Tavus response did not include replica_id.",
            )
            self._jobs[state.external_job_id] = state
            return state

        state = AvatarProviderJobState(
            external_job_id=f"tavus:{replica_id}",
            external_avatar_id=replica_id,
            status="training",
            preview_url=None,
        )
        self._jobs[state.external_job_id] = state
        return state

    async def _fetch_tavus_status(
        self,
        existing: AvatarProviderJobState,
    ) -> AvatarProviderJobState:
        api_key = os.getenv("TAVUS_API_KEY")

        if not api_key:
            existing.status = "failed"
            existing.error_message = "TAVUS_API_KEY is not configured."
            return existing

        replica_id = existing.external_avatar_id

        if not replica_id:
            existing.status = "failed"
            existing.error_message = "Missing Tavus replica id."
            return existing

        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(
                f"https://tavusapi.com/v2/replicas/{replica_id}",
                headers={"x-api-key": api_key},
            )

        if response.status_code >= 400:
            existing.status = "failed"
            existing.error_message = f"Tavus status failed: {response.text}"
            return existing

        data = response.json()
        tavus_status = str(data.get("status", "")).lower()
        error_message = data.get("error_message") or data.get("error")

        if tavus_status in {"ready", "completed", "complete"}:
            existing.status = "ready"
            existing.preview_url = f"tavus-replica://{replica_id}"
            existing.error_message = None
        elif tavus_status in {"failed", "error"}:
            existing.status = "failed"
            existing.error_message = error_message or "Tavus replica training failed."
        else:
            existing.status = "training"
            existing.error_message = None

        return existing


    async def create_tavus_video(
        self,
        replica_id: str,
        script: str,
    ) -> AvatarProviderJobState:
        api_key = os.getenv("TAVUS_API_KEY")

        if not api_key:
            return AvatarProviderJobState(
                external_job_id=f"tavus-video:{uuid.uuid4()}",
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message="TAVUS_API_KEY is not configured.",
            )

        payload = {
            "replica_id": replica_id,
            "script": script,
        }

        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                "https://tavusapi.com/v2/videos",
                headers={
                    "Content-Type": "application/json",
                    "x-api-key": api_key,
                },
                json=payload,
            )

        if response.status_code >= 400:
            return AvatarProviderJobState(
                external_job_id=f"tavus-video:{uuid.uuid4()}",
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message=f"Tavus video generation failed: {response.text}",
            )

        data = response.json()
        video_id = data.get("video_id") or data.get("id")

        if not video_id:
            return AvatarProviderJobState(
                external_job_id=f"tavus-video:{uuid.uuid4()}",
                external_avatar_id=replica_id,
                status="failed",
                preview_url=None,
                error_message="Tavus video response did not include video_id.",
            )

        hosted_url = data.get("hosted_url")
        download_url = data.get("download_url")
        stream_url = data.get("stream_url")

        state = AvatarProviderJobState(
            external_job_id=f"tavus-video:{video_id}",
            external_avatar_id=replica_id,
            status="generatingPreview",
            preview_url=hosted_url or stream_url or download_url,
        )
        self._jobs[state.external_job_id] = state
        return state

    async def fetch_tavus_video_status(
        self,
        external_job_id: str,
    ) -> AvatarProviderJobState:
        api_key = os.getenv("TAVUS_API_KEY")

        if not api_key:
            return AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message="TAVUS_API_KEY is not configured.",
            )

        video_id = external_job_id.replace("tavus-video:", "")

        async with httpx.AsyncClient(timeout=45) as client:
            response = await client.get(
                f"https://tavusapi.com/v2/videos/{video_id}",
                headers={"x-api-key": api_key},
            )

        if response.status_code >= 400:
            return AvatarProviderJobState(
                external_job_id=external_job_id,
                external_avatar_id=None,
                status="failed",
                preview_url=None,
                error_message=f"Tavus video status failed: {response.text}",
            )

        data = response.json()
        tavus_status = str(data.get("status", "")).lower()

        hosted_url = data.get("hosted_url")
        download_url = data.get("download_url")
        stream_url = data.get("stream_url")
        preview_url = hosted_url or stream_url or download_url

        replica_id = data.get("replica_id")

        if tavus_status in {"ready", "completed", "complete"}:
            status = "ready"
        elif tavus_status in {"failed", "error"}:
            status = "failed"
        else:
            status = "generatingPreview"

        state = AvatarProviderJobState(
            external_job_id=external_job_id,
            external_avatar_id=replica_id,
            status=status,
            preview_url=preview_url,
            error_message=data.get("status_details") if status == "failed" else None,
        )

        self._jobs[external_job_id] = state
        return state


    def _normalize_tavus_error(
        self,
        status_code: int,
        response_text: str,
    ) -> str:
        lower = response_text.lower()

        if status_code == 402 or "payment required" in lower:
            return (
                "Tavus requires billing or API access for replica creation. "
                "Please activate billing or credits in the Tavus dashboard."
            )

        if status_code == 401 or "unauthorized" in lower:
            return (
                "Tavus API authentication failed. "
                "Please verify TAVUS_API_KEY in the backend .env file."
            )

        if status_code == 403 or "forbidden" in lower:
            return (
                "Tavus API access is forbidden for this account. "
                "Please verify that Replica/Phoenix access is enabled."
            )

        if "video" in lower and ("url" in lower or "invalid" in lower):
            return (
                "Tavus could not access the training video. "
                "Use a public HTTPS video URL that Tavus can download."
            )

        return f"Tavus submit failed: {response_text}"


    def _extract_tavus_training_video_url(
        self,
        package: Dict[str, Any],
    ) -> Optional[str]:
        direct_keys = [
            "train_video_url",
            "trainVideoURL",
            "motionVideoURL",
            "primaryMotionVideoURL",
            "videoURL",
            "remoteVideoURL",
        ]

        for key in direct_keys:
            value = package.get(key)
            if isinstance(value, str) and value.startswith("https://"):
                return value

        assets = package.get("assets")
        if isinstance(assets, list):
            for asset in assets:
                if not isinstance(asset, dict):
                    continue

                kind = str(asset.get("type") or asset.get("kind") or asset.get("assetType") or "").lower()
                url = (
                    asset.get("remoteURL")
                    or asset.get("remoteUrl")
                    or asset.get("url")
                    or asset.get("downloadURL")
                    or asset.get("downloadUrl")
                )

                if "video" in kind and isinstance(url, str) and url.startswith("https://"):
                    return url

        return None


avatar_provider_service = AvatarProviderService()
