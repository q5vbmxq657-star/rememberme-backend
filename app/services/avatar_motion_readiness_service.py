from typing import List

from app.schemas.avatar_motion import (
    AvatarMotionReadinessRequest,
    AvatarMotionReadinessResponse,
    AvatarMotionAssetAnalysis,
)
from app.services.avatar_media_storage_service import AvatarMediaStorageService


class AvatarMotionReadinessService:
    def __init__(self):
        self.media_service = AvatarMediaStorageService()

    def assess(
        self,
        request: AvatarMotionReadinessRequest
    ) -> AvatarMotionReadinessResponse:
        media_list = self.media_service.list_profile_assets(request.profile_id)

        video_assets = [
            asset for asset in media_list.assets
            if asset.asset_type in ["video", "training_sample"]
            and asset.content_type.startswith("video/")
        ]

        analyses = [
            self._analyze_video_asset(asset)
            for asset in video_assets
        ]

        ranked = sorted(
            analyses,
            key=lambda item: item.talking_portrait_suitability_score,
            reverse=True
        )

        usable = [
            item for item in ranked
            if item.recommended_for_motion_learning
        ]

        motion_identity_score = self._average(
            [item.motion_quality_score for item in usable]
        )

        expression_learning_score = self._average(
            [item.expression_range_score for item in usable]
        )

        talking_portrait_score = self._average(
            [item.talking_portrait_suitability_score for item in usable]
        )

        return AvatarMotionReadinessResponse(
            profile_id=request.profile_id,
            motion_status=self._motion_status(
                usable_count=len(usable),
                talking_portrait_score=talking_portrait_score
            ),
            motion_identity_score=motion_identity_score,
            expression_learning_score=expression_learning_score,
            talking_portrait_readiness_score=talking_portrait_score,
            usable_motion_asset_count=len(usable),
            recommended_primary_motion_asset_id=usable[0].asset_id if usable else None,
            motion_assets=self._assign_motion_roles(ranked),
            missing_requirements=self._missing_requirements(usable, talking_portrait_score),
            next_best_actions=self._next_best_actions(usable, talking_portrait_score)
        )

    def _analyze_video_asset(self, asset) -> AvatarMotionAssetAnalysis:
        estimated_duration = self._estimated_duration(asset.size_bytes)

        motion_quality = self._motion_quality(asset.size_bytes)
        expression_range = self._expression_range(estimated_duration)
        lip_visibility = self._lip_visibility(asset.size_bytes)
        head_pose = self._head_pose_stability(estimated_duration)

        suitability = round(
            motion_quality * 0.34
            + expression_range * 0.24
            + lip_visibility * 0.24
            + head_pose * 0.18,
            3
        )

        return AvatarMotionAssetAnalysis(
            asset_id=asset.asset_id,
            title=asset.title,
            asset_type=asset.asset_type,
            content_type=asset.content_type,
            size_bytes=asset.size_bytes,
            estimated_duration_seconds=estimated_duration,
            motion_quality_score=motion_quality,
            expression_range_score=expression_range,
            lip_visibility_score=lip_visibility,
            head_pose_stability_score=head_pose,
            talking_portrait_suitability_score=suitability,
            recommended_for_motion_learning=suitability >= 0.64,
            motion_role="candidate"
        )

    def _assign_motion_roles(
        self,
        assets: List[AvatarMotionAssetAnalysis]
    ) -> List[AvatarMotionAssetAnalysis]:
        assigned = []

        for index, asset in enumerate(assets):
            if index == 0 and asset.recommended_for_motion_learning:
                role = "primary_motion_reference"
            elif asset.recommended_for_motion_learning:
                role = "supporting_motion_reference"
            else:
                role = "not_recommended"

            assigned.append(
                AvatarMotionAssetAnalysis(
                    asset_id=asset.asset_id,
                    title=asset.title,
                    asset_type=asset.asset_type,
                    content_type=asset.content_type,
                    size_bytes=asset.size_bytes,
                    estimated_duration_seconds=asset.estimated_duration_seconds,
                    motion_quality_score=asset.motion_quality_score,
                    expression_range_score=asset.expression_range_score,
                    lip_visibility_score=asset.lip_visibility_score,
                    head_pose_stability_score=asset.head_pose_stability_score,
                    talking_portrait_suitability_score=asset.talking_portrait_suitability_score,
                    recommended_for_motion_learning=asset.recommended_for_motion_learning,
                    motion_role=role
                )
            )

        return assigned

    def _estimated_duration(self, size_bytes: int) -> float:
        seconds = size_bytes / 850_000
        return round(max(2.0, min(seconds, 90.0)), 1)

    def _motion_quality(self, size_bytes: int) -> float:
        if size_bytes >= 30_000_000:
            return 0.93

        if size_bytes >= 12_000_000:
            return 0.86

        if size_bytes >= 5_000_000:
            return 0.78

        if size_bytes >= 1_500_000:
            return 0.68

        return 0.52

    def _expression_range(self, duration_seconds: float) -> float:
        if duration_seconds >= 45:
            return 0.90

        if duration_seconds >= 20:
            return 0.82

        if duration_seconds >= 8:
            return 0.70

        if duration_seconds >= 5:
            return 0.56

        return 0.48

    def _lip_visibility(self, size_bytes: int) -> float:
        if size_bytes >= 12_000_000:
            return 0.86

        if size_bytes >= 5_000_000:
            return 0.76

        if size_bytes >= 1_500_000:
            return 0.64

        return 0.46

    def _head_pose_stability(self, duration_seconds: float) -> float:
        if duration_seconds >= 20:
            return 0.82

        if duration_seconds >= 8:
            return 0.72

        if duration_seconds >= 5:
            return 0.64

        return 0.58

    def _average(self, values: List[float]) -> float:
        if not values:
            return 0.0

        return round(sum(values) / len(values), 3)

    def _motion_status(
        self,
        usable_count: int,
        talking_portrait_score: float
    ) -> str:
        if usable_count >= 2:
            return "motion_pack_ready"

        if usable_count == 1:
            return "controlled_motion_reference_ready"

        return "motion_data_missing"

    def _missing_requirements(
        self,
        usable: List[AvatarMotionAssetAnalysis],
        talking_portrait_score: float
    ) -> List[str]:
        missing = []

        if not usable:
            missing.append("At least one clear video clip is required.")
            missing.append("A second short video improves expression and motion stability.")
            missing.append("Higher quality video improves talking portrait readiness.")
            return missing

        if len(usable) < 2:
            missing.append("A second short video improves expression and motion stability.")

        if talking_portrait_score < 0.82:
            missing.append("Higher quality or longer video improves talking portrait readiness.")

        return missing

    def _next_best_actions(
        self,
        usable: List[AvatarMotionAssetAnalysis],
        talking_portrait_score: float
    ) -> List[str]:
        if not usable:
            return [
                "Upload a short frontal video with clear face visibility.",
                "Record 20–45 seconds with natural speech and stable lighting.",
                "Avoid heavy head movement, blur or background noise."
            ]

        actions = []

        if len(usable) < 2:
            actions.append("Add one more short video to improve motion consistency.")

        if talking_portrait_score < 0.82:
            actions.append("Upload a longer 20–45 second talking video for stronger lip-sync readiness.")

        if not actions:
            actions.append("Motion reference pack is ready for controlled talking portrait generation.")

        return actions
