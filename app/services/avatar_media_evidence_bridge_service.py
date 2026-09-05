from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID

from app.models.avatar_evidence_asset import (
    AvatarEvidenceAsset,
)
from app.schemas.avatar_media import (
    AvatarMediaMetadata,
)
from app.services.avatar_evidence_repository import (
    AvatarEvidenceNotFoundError,
    AvatarEvidenceRepository,
    AvatarEvidenceRepositoryError,
)


class AvatarMediaEvidenceBridgeError(
    RuntimeError
):
    pass


@dataclass(frozen=True)
class AvatarMediaEvidenceAnalysis:
    quality_score: float = 0.0

    has_face: bool = False
    has_frontal_face: bool = False
    has_clear_lighting: bool = False
    has_voice: bool = False

    voice_usable: bool = False
    motion_usable: bool = False

    emotional_presence_score: float = 0.0
    identity_consistency_score: float = 0.0

    motion_quality_score: float = 0.0
    expression_range_score: float = 0.0
    lip_visibility_score: float = 0.0
    head_pose_stability_score: float = 0.0

    recommended_for_avatar: bool = False

    rejection_code: Optional[str] = None
    rejection_reason: Optional[str] = None

    analysis_version: str = (
        "upload-registration-v1"
    )

    analysis_metadata: Optional[
        Dict[str, Any]
    ] = None


class AvatarMediaEvidenceBridgeService:
    """
    Canonical bridge between private media storage and persistent
    avatar evidence.

    Media storage remains responsible for bytes and signed delivery.
    AvatarEvidenceRepository remains the sole authority for selection,
    primary status and readiness-relevant evidence.
    """

    def __init__(
        self,
        repository: Optional[
            AvatarEvidenceRepository
        ] = None,
    ) -> None:
        self.repository = (
            repository
            or AvatarEvidenceRepository()
        )

    def persist_uploaded_media(
        self,
        *,
        metadata: AvatarMediaMetadata,
        analysis: Optional[
            AvatarMediaEvidenceAnalysis
        ] = None,
    ) -> AvatarEvidenceAsset:
        resolved_analysis = (
            analysis
            or AvatarMediaEvidenceAnalysis()
        )

        try:
            asset_id = UUID(
                metadata.asset_id
            )
        except ValueError as error:
            raise AvatarMediaEvidenceBridgeError(
                "Media asset_id is not a valid UUID."
            ) from error

        try:
            profile_id = UUID(
                metadata.profile_id
            )
        except ValueError as error:
            raise AvatarMediaEvidenceBridgeError(
                "Media profile_id is not a valid UUID."
            ) from error

        evidence_kind = (
            self.evidence_kind_for(
                metadata.asset_type
            )
        )

        storage_key = (
            f"{metadata.profile_id}/"
            f"{metadata.filename}"
        )

        return (
            self.repository
            .upsert_uploaded_asset(
                asset_id=asset_id,
                profile_id=profile_id,
                asset_type=(
                    metadata.asset_type
                ),
                evidence_kind=(
                    evidence_kind
                ),
                title=metadata.title,
                filename=metadata.filename,
                content_type=(
                    metadata.content_type
                ),
                size_bytes=(
                    metadata.size_bytes
                ),
                storage_backend=(
                    "local_private"
                ),
                storage_key=storage_key,
                storage_path=(
                    metadata.storage_path
                ),
                quality_score=(
                    self._clamp(
                        resolved_analysis
                        .quality_score
                    )
                ),
                has_face=(
                    resolved_analysis
                    .has_face
                ),
                has_frontal_face=(
                    resolved_analysis
                    .has_frontal_face
                ),
                has_clear_lighting=(
                    resolved_analysis
                    .has_clear_lighting
                ),
                has_voice=(
                    resolved_analysis
                    .has_voice
                ),
                voice_usable=(
                    resolved_analysis
                    .voice_usable
                ),
                motion_usable=(
                    resolved_analysis
                    .motion_usable
                ),
                emotional_presence_score=(
                    self._clamp(
                        resolved_analysis
                        .emotional_presence_score
                    )
                ),
                identity_consistency_score=(
                    self._clamp(
                        resolved_analysis
                        .identity_consistency_score
                    )
                ),
                motion_quality_score=(
                    self._clamp(
                        resolved_analysis
                        .motion_quality_score
                    )
                ),
                expression_range_score=(
                    self._clamp(
                        resolved_analysis
                        .expression_range_score
                    )
                ),
                lip_visibility_score=(
                    self._clamp(
                        resolved_analysis
                        .lip_visibility_score
                    )
                ),
                head_pose_stability_score=(
                    self._clamp(
                        resolved_analysis
                        .head_pose_stability_score
                    )
                ),
                recommended_for_avatar=(
                    resolved_analysis
                    .recommended_for_avatar
                ),
                rejection_code=(
                    resolved_analysis
                    .rejection_code
                ),
                rejection_reason=(
                    resolved_analysis
                    .rejection_reason
                ),
                analysis_version=(
                    resolved_analysis
                    .analysis_version
                ),
                analysis_metadata={
                    **(
                        resolved_analysis
                        .analysis_metadata
                        or {}
                    ),
                    "registration_source": (
                        "avatar-media-upload"
                    ),
                },
                source_metadata={
                    "media_asset_id": (
                        metadata.asset_id
                    ),
                    "media_profile_id": (
                        metadata.profile_id
                    ),
                    "created_at": (
                        metadata.created_at
                    ),
                },
            )
        )

    def archive_uploaded_media_if_present(
        self,
        *,
        asset_id: str,
        profile_id: str,
    ) -> None:
        try:
            normalized_asset_id = UUID(asset_id)
            normalized_profile_id = UUID(profile_id)
        except ValueError as error:
            raise AvatarMediaEvidenceBridgeError(
                "Media evidence identity is not valid."
            ) from error

        try:
            self.repository.archive(
                asset_id=normalized_asset_id,
                profile_id=normalized_profile_id,
            )
        except AvatarEvidenceNotFoundError:
            # Failed uploads can own stored bytes before evidence exists.
            # Deletion remains idempotent when no active evidence is present.
            return
        except AvatarEvidenceRepositoryError as error:
            raise AvatarMediaEvidenceBridgeError(
                "Media evidence could not be archived."
            ) from error

    @staticmethod
    def evidence_kind_for(
        asset_type: str,
    ) -> str:
        normalized = (
            asset_type
            .strip()
            .lower()
        )

        if normalized in {
            "image",
            "reference",
        }:
            return "identity_photo"

        if normalized in {
            "video",
            "training_sample",
        }:
            return "motion_video"

        if normalized in {
            "voice",
            "audio",
        }:
            return "voice_sample"

        raise AvatarMediaEvidenceBridgeError(
            "Asset type is not valid source "
            f"evidence: {asset_type}"
        )

    @staticmethod
    def analysis_from_face_result(
        result: Dict[str, Any],
    ) -> AvatarMediaEvidenceAnalysis:
        return AvatarMediaEvidenceAnalysis(
            quality_score=float(
                result.get(
                    "quality_score",
                    0.0,
                )
            ),
            has_face=bool(
                result.get(
                    "has_face",
                    False,
                )
            ),
            has_frontal_face=bool(
                result.get(
                    "has_frontal_face",
                    False,
                )
            ),
            has_clear_lighting=bool(
                result.get(
                    "has_clear_lighting",
                    False,
                )
            ),
            emotional_presence_score=float(
                result.get(
                    "emotional_presence_score",
                    0.0,
                )
            ),
            identity_consistency_score=float(
                result.get(
                    "identity_consistency_score",
                    0.0,
                )
            ),
            recommended_for_avatar=bool(
                result.get(
                    "recommended_for_avatar",
                    False,
                )
            ),
            analysis_version=(
                "legacy-face-heuristic-v1"
            ),
            analysis_metadata={
                "analysis_kind": (
                    "identity_photo"
                )
            },
        )

    @staticmethod
    def safe_registration_analysis(
        *,
        asset_type: str,
    ) -> AvatarMediaEvidenceAnalysis:
        normalized = (
            asset_type
            .strip()
            .lower()
        )

        if normalized in {
            "voice",
            "audio",
        }:
            return AvatarMediaEvidenceAnalysis(
                has_voice=True,
                voice_usable=False,
                analysis_metadata={
                    "analysis_kind": (
                        "voice_sample"
                    ),
                    "awaiting_analysis": True,
                },
            )

        if normalized in {
            "video",
            "training_sample",
        }:
            return AvatarMediaEvidenceAnalysis(
                motion_usable=False,
                analysis_metadata={
                    "analysis_kind": (
                        "motion_video"
                    ),
                    "awaiting_analysis": True,
                },
            )

        return AvatarMediaEvidenceAnalysis()

    @staticmethod
    def _clamp(
        value: float,
    ) -> float:
        return min(
            max(
                float(value),
                0.0,
            ),
            1.0,
        )
