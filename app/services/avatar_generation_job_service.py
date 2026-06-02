import json
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from app.schemas.avatar_generation_job import (
    AvatarGenerationJobRequest,
    AvatarGenerationJobResponse,
)


class AvatarGenerationJobService:

    def __init__(self):
        self.jobs_root = Path("./storage/avatar-jobs").resolve()
        self.jobs_root.mkdir(parents=True, exist_ok=True)

    def create_job(
        self,
        request: AvatarGenerationJobRequest
    ) -> AvatarGenerationJobResponse:

        job_id = str(uuid.uuid4())

        created_at = datetime.now(timezone.utc).isoformat()

        job = {
            "job_id": job_id,
            "profile_id": request.profile_id,
            "generation_mode": request.generation_mode,
            "status": "queued",
            "created_at": created_at,
            "updated_at": created_at,
            "progress": 0.0,
            "current_stage": "initializing_avatar_pipeline",
            "identity_asset_id": request.identity_asset_id,
            "motion_asset_id": request.motion_asset_id,
            "voice_enabled": request.voice_enabled,
            "persona_enabled": request.persona_enabled,
            "estimated_completion_seconds": 120
        }

        self._write_job(job_id, job)

        return self._job_response(
            job_id=job_id,
            profile_id=request.profile_id,
            status="queued",
            progress=0.0,
            current_stage="initializing_avatar_pipeline",
            generation_mode=request.generation_mode,
            identity_asset_id=request.identity_asset_id,
            motion_asset_id=request.motion_asset_id
        )

    def get_job(
        self,
        job_id: str
    ) -> AvatarGenerationJobResponse:

        job = self._read_job(job_id)

        progress = min(job["progress"] + 0.22, 1.0)

        if progress >= 1.0:
            status = "completed"
            stage = "avatar_generation_completed"
            output_url = f"/storage/generated-avatar/{job_id}.mp4"
        elif progress >= 0.80:
            status = "rendering"
            stage = "rendering_talking_portrait"
            output_url = None
        elif progress >= 0.55:
            status = "processing"
            stage = "building_motion_representation"
            output_url = None
        elif progress >= 0.30:
            status = "processing"
            stage = "conditioning_identity_and_voice"
            output_url = None
        else:
            status = "queued"
            stage = "initializing_avatar_pipeline"
            output_url = None

        job["progress"] = progress
        job["status"] = status
        job["current_stage"] = stage
        job["updated_at"] = datetime.now(timezone.utc).isoformat()

        self._write_job(job_id, job)

        return AvatarGenerationJobResponse(
            job_id=job_id,
            profile_id=job["profile_id"],
            status=status,
            progress=round(progress, 3),
            current_stage=stage,
            generation_mode=job["generation_mode"],
            identity_asset_id=job.get("identity_asset_id"),
            motion_asset_id=job.get("motion_asset_id"),
            preview_video_url=output_url
        )

    def _write_job(
        self,
        job_id: str,
        payload: dict
    ) -> None:

        job_file = self.jobs_root / f"{job_id}.json"

        with job_file.open("w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2)

    def _read_job(
        self,
        job_id: str
    ) -> dict:

        job_file = self.jobs_root / f"{job_id}.json"

        if not job_file.exists():
            raise RuntimeError("Avatar generation job not found.")

        with job_file.open("r", encoding="utf-8") as input_file:
            return json.load(input_file)

    def _job_response(
        self,
        job_id: str,
        profile_id: str,
        status: str,
        progress: float,
        current_stage: str,
        generation_mode: str,
        identity_asset_id: Optional[str],
        motion_asset_id: Optional[str],
    ) -> AvatarGenerationJobResponse:

        return AvatarGenerationJobResponse(
            job_id=job_id,
            profile_id=profile_id,
            status=status,
            progress=round(progress, 3),
            current_stage=current_stage,
            generation_mode=generation_mode,
            identity_asset_id=identity_asset_id,
            motion_asset_id=motion_asset_id,
            preview_video_url=None
        )
