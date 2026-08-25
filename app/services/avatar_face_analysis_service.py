from __future__ import annotations

from typing import Any, Dict


class AvatarFaceAnalysisService:
    """
    Legacy upload-preflight metadata estimator.

    This service does not inspect image pixels and must never be used
    as face-detection, face-recognition or biometric verification
    authority.

    Pixel-derived face analysis belongs to a real media-analysis
    pipeline. Biometric identity verification belongs exclusively to
    the server-side licensed Face Identity Comparator.
    """

    ANALYSIS_VERSION = "upload-metadata-preflight-v2"

    def analyze(
        self,
        content_type: str,
        size_bytes: int,
    ) -> Dict[str, Any]:
        normalized_content_type = (
            content_type
            .strip()
            .lower()
        )

        is_supported_visual_media = (
            normalized_content_type.startswith(
                "image/"
            )
            or normalized_content_type.startswith(
                "video/"
            )
        )

        metadata_quality_score = (
            self._metadata_quality_score(
                size_bytes=size_bytes,
                is_supported_visual_media=(
                    is_supported_visual_media
                ),
            )
        )

        return {
            "has_face": False,
            "has_frontal_face": False,
            "has_clear_lighting": False,
            "emotional_presence_score": 0.0,
            "identity_consistency_score": 0.0,
            "quality_score": metadata_quality_score,
            "recommended_for_avatar": False,
            "analysis_version": self.ANALYSIS_VERSION,
            "analysis_metadata": {
                "pixel_analysis_performed": False,
                "biometric_analysis_performed": False,
                "content_type_supported": (
                    is_supported_visual_media
                ),
                "requires_pixel_analysis": True,
            },
        }

    def _metadata_quality_score(
        self,
        *,
        size_bytes: int,
        is_supported_visual_media: bool,
    ) -> float:
        if (
            not is_supported_visual_media
            or size_bytes <= 0
        ):
            return 0.0

        if size_bytes < 25_000:
            return 0.1

        if size_bytes < 100_000:
            return 0.2

        return 0.3
