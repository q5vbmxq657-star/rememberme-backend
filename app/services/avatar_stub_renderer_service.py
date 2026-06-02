import os
import json
import uuid
from pathlib import Path
from datetime import datetime, timezone

from app.schemas.avatar_stub_renderer import (
    AvatarStubRenderRequest,
    AvatarStubRenderResponse,
    AvatarStubRenderStatusResponse,
)
from app.schemas.avatar_renderer_handoff import AvatarRendererHandoffRequest
from app.services.avatar_renderer_handoff_service import AvatarRendererHandoffService
from app.services.avatar_generation_job_service import AvatarGenerationJobService


class AvatarStubRendererService:
    def __init__(self):
        self.output_root = Path(
            os.getenv("AVATAR_RENDER_OUTPUT_ROOT", "./storage/generated-avatar")
        ).resolve()

        self.output_root.mkdir(parents=True, exist_ok=True)

        self.job_service = AvatarGenerationJobService()
        self.handoff_service = AvatarRendererHandoffService()

    def render(
        self,
        request: AvatarStubRenderRequest,
        base_url: str
    ) -> AvatarStubRenderResponse:
        handoff = self.handoff_service.build_handoff(
            request=AvatarRendererHandoffRequest(
                job_id=request.job_id,
                renderer_provider=request.renderer_provider,
                expires_in_seconds=900
            ),
            base_url=base_url
        )

        output_asset_id = str(uuid.uuid4())
        output_filename = f"{output_asset_id}.json"
        output_path = self.output_root / output_filename

        payload = {
            "output_asset_id": output_asset_id,
            "job_id": handoff.job_id,
            "profile_id": handoff.profile_id,
            "output_type": "generated_avatar_preview_stub",
            "avatar_mode": handoff.avatar_mode,
            "renderer": handoff.renderer,
            "renderer_provider": handoff.renderer_provider,
            "voice_strategy": handoff.voice_strategy,
            "lip_sync_strategy": handoff.lip_sync_strategy,
            "input_assets": [asset.model_dump() for asset in handoff.input_assets],
            "safety_constraints": handoff.safety_constraints,
            "created_at": datetime.now(timezone.utc).isoformat()
        }

        with output_path.open("w", encoding="utf-8") as output:
            json.dump(payload, output, indent=2)

        self._mark_job_rendered(
            job_id=handoff.job_id,
            output_asset_id=output_asset_id
        )

        preview_url = f"{base_url.rstrip('/')}/v1/avatar-stub-renderer/outputs/{output_asset_id}"

        return AvatarStubRenderResponse(
            job_id=handoff.job_id,
            profile_id=handoff.profile_id,
            render_status="completed",
            output_asset_id=output_asset_id,
            output_type="generated_avatar_preview_stub",
            preview_video_url=preview_url,
            message="Internal stub renderer completed. Output contract is ready for iOS preview integration."
        )

    def get_render_status(
        self,
        job_id: str,
        base_url: str
    ) -> AvatarStubRenderStatusResponse:
        job = self.job_service.get_job(job_id)

        output_asset_id = job.preview_video_url

        if not output_asset_id:
            return AvatarStubRenderStatusResponse(
                job_id=job_id,
                render_status="not_rendered",
                output_asset_id=None,
                preview_video_url=None,
                message="No renderer output exists for this job yet."
            )

        clean_output_id = output_asset_id.rsplit("/", 1)[-1].replace(".mp4", "")

        return AvatarStubRenderStatusResponse(
            job_id=job_id,
            render_status="completed",
            output_asset_id=clean_output_id,
            preview_video_url=f"{base_url.rstrip('/')}/v1/avatar-stub-renderer/outputs/{clean_output_id}",
            message="Renderer output is available."
        )

    def read_output(
        self,
        output_asset_id: str
    ) -> dict:
        output_path = self.output_root / f"{output_asset_id}.json"

        if not output_path.exists():
            raise RuntimeError("Renderer output not found.")

        with output_path.open("r", encoding="utf-8") as input_file:
            return json.load(input_file)

    def _mark_job_rendered(
        self,
        job_id: str,
        output_asset_id: str
    ) -> None:
        job_file = Path("./storage/avatar-jobs").resolve() / f"{job_id}.json"

        if not job_file.exists():
            return

        with job_file.open("r", encoding="utf-8") as input_file:
            data = json.load(input_file)

        data["status"] = "completed"
        data["progress"] = 1.0
        data["current_stage"] = "avatar_generation_completed"
        data["preview_video_url"] = f"/storage/generated-avatar/{output_asset_id}.mp4"
        data["output_asset_id"] = output_asset_id
        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        with job_file.open("w", encoding="utf-8") as output:
            json.dump(data, output, indent=2)
