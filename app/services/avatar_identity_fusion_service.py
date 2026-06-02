from typing import List, Dict, Any

from app.schemas.avatar_identity_fusion import (
    AvatarIdentityFusionRequest,
    AvatarIdentityFusionResponse,
    AvatarIdentityReferenceAsset,
)
from app.services.avatar_media_storage_service import AvatarMediaStorageService
from app.services.avatar_face_analysis_service import AvatarFaceAnalysisService


class AvatarIdentityFusionService:
    def __init__(self):
        self.media_service = AvatarMediaStorageService()
        self.face_service = AvatarFaceAnalysisService()

    def fuse(
        self,
        request: AvatarIdentityFusionRequest
    ) -> AvatarIdentityFusionResponse:
        media_list = self.media_service.list_profile_assets(request.profile_id)

        candidate_assets: List[AvatarIdentityReferenceAsset] = []

        for asset in media_list.assets:
            if asset.asset_type not in ["image", "video", "reference", "training_sample"]:
                continue

            if not self._is_visual_content(asset.content_type):
                continue

            analysis = self.face_service.analyze(
                content_type=asset.content_type,
                size_bytes=asset.size_bytes
            )

            candidate_assets.append(
                AvatarIdentityReferenceAsset(
                    asset_id=asset.asset_id,
                    title=asset.title,
                    asset_type=asset.asset_type,
                    content_type=asset.content_type,
                    size_bytes=asset.size_bytes,
                    quality_score=analysis["quality_score"],
                    has_face=analysis["has_face"],
                    has_frontal_face=analysis["has_frontal_face"],
                    has_clear_lighting=analysis["has_clear_lighting"],
                    emotional_presence_score=analysis["emotional_presence_score"],
                    identity_consistency_score=analysis["identity_consistency_score"],
                    recommended_for_avatar=analysis["recommended_for_avatar"],
                    reference_role="candidate"
                )
            )

        ranked_assets = sorted(
            candidate_assets,
            key=self._ranking_score,
            reverse=True
        )

        usable_assets = [
            asset for asset in ranked_assets
            if asset.quality_score >= request.min_quality_score
            and asset.has_face
            and asset.has_clear_lighting
        ]

        rejected_assets = [
            asset for asset in ranked_assets
            if asset not in usable_assets
        ]

        reference_pack = self._assign_reference_roles(usable_assets)

        primary_asset_id = reference_pack[0].asset_id if reference_pack else None

        visual_identity_score = self._visual_identity_score(reference_pack)
        identity_stability_score = self._identity_stability_score(reference_pack)

        return AvatarIdentityFusionResponse(
            profile_id=request.profile_id,
            fusion_status=self._fusion_status(reference_pack, identity_stability_score),
            primary_reference_asset_id=primary_asset_id,
            visual_identity_score=visual_identity_score,
            identity_stability_score=identity_stability_score,
            usable_reference_count=len(reference_pack),
            rejected_reference_count=len(rejected_assets),
            reference_pack=reference_pack,
            rejected_assets=rejected_assets,
            next_best_actions=self._next_best_actions(
                reference_pack=reference_pack,
                rejected_assets=rejected_assets,
                identity_stability_score=identity_stability_score
            )
        )

    def _is_visual_content(self, content_type: str) -> bool:
        return content_type.startswith("image/") or content_type.startswith("video/")

    def _ranking_score(
        self,
        asset: AvatarIdentityReferenceAsset
    ) -> float:
        score = asset.quality_score * 0.42
        score += asset.identity_consistency_score * 0.30
        score += asset.emotional_presence_score * 0.16
        score += 0.08 if asset.has_frontal_face else 0.0
        score += 0.04 if asset.has_clear_lighting else 0.0

        return round(min(score, 1.0), 4)

    def _assign_reference_roles(
        self,
        assets: List[AvatarIdentityReferenceAsset]
    ) -> List[AvatarIdentityReferenceAsset]:
        assigned: List[AvatarIdentityReferenceAsset] = []

        for index, asset in enumerate(assets[:8]):
            if index == 0:
                role = "primary_identity_reference"
            elif index <= 2:
                role = "secondary_identity_reference"
            elif asset.asset_type == "video":
                role = "motion_reference"
            else:
                role = "supporting_reference"

            assigned.append(
                AvatarIdentityReferenceAsset(
                    asset_id=asset.asset_id,
                    title=asset.title,
                    asset_type=asset.asset_type,
                    content_type=asset.content_type,
                    size_bytes=asset.size_bytes,
                    quality_score=asset.quality_score,
                    has_face=asset.has_face,
                    has_frontal_face=asset.has_frontal_face,
                    has_clear_lighting=asset.has_clear_lighting,
                    emotional_presence_score=asset.emotional_presence_score,
                    identity_consistency_score=asset.identity_consistency_score,
                    recommended_for_avatar=asset.recommended_for_avatar,
                    reference_role=role
                )
            )

        return assigned

    def _visual_identity_score(
        self,
        reference_pack: List[AvatarIdentityReferenceAsset]
    ) -> float:
        if not reference_pack:
            return 0.0

        total = sum(self._ranking_score(asset) for asset in reference_pack)
        return round(total / len(reference_pack), 3)

    def _identity_stability_score(
        self,
        reference_pack: List[AvatarIdentityReferenceAsset]
    ) -> float:
        if not reference_pack:
            return 0.0

        count_bonus = min(len(reference_pack) * 0.045, 0.18)
        frontal_bonus = 0.08 if any(asset.has_frontal_face for asset in reference_pack) else 0.0
        lighting_bonus = 0.06 if all(asset.has_clear_lighting for asset in reference_pack[:3]) else 0.0

        base = self._visual_identity_score(reference_pack)

        return round(min(base + count_bonus + frontal_bonus + lighting_bonus, 1.0), 3)

    def _fusion_status(
        self,
        reference_pack: List[AvatarIdentityReferenceAsset],
        identity_stability_score: float
    ) -> str:
        if not reference_pack:
            return "insufficient_visual_identity"

        if identity_stability_score >= 0.86 and len(reference_pack) >= 3:
            return "stable_identity_pack_ready"

        if identity_stability_score >= 0.74:
            return "controlled_identity_pack_ready"

        return "visual_identity_needs_more_data"

    def _next_best_actions(
        self,
        reference_pack: List[AvatarIdentityReferenceAsset],
        rejected_assets: List[AvatarIdentityReferenceAsset],
        identity_stability_score: float
    ) -> List[str]:
        actions = []

        if not reference_pack:
            actions.append("Upload at least one clear frontal portrait image.")
            actions.append("Use good lighting and avoid motion blur.")
            return actions

        if len(reference_pack) < 3:
            actions.append("Upload 2–3 additional clear face references from different angles.")

        if not any(asset.asset_type == "video" for asset in reference_pack):
            actions.append("Add one short video clip to improve motion and expression references.")

        if identity_stability_score < 0.86:
            actions.append("Add higher-quality frontal images to improve identity stability.")

        if rejected_assets:
            actions.append("Review rejected assets and replace low-quality references.")

        if not actions:
            actions.append("Identity reference pack is ready for controlled avatar generation.")

        return actions
