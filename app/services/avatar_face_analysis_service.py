from typing import Dict


class AvatarFaceAnalysisService:

    def analyze(
        self,
        content_type: str,
        size_bytes: int
    ) -> Dict:
        quality_score = self._quality_score(size_bytes)

        return {
            "has_face": True,
            "has_frontal_face": quality_score >= 0.72,
            "has_clear_lighting": quality_score >= 0.68,
            "emotional_presence_score": round(min(quality_score + 0.08, 1.0), 3),
            "identity_consistency_score": round(min(quality_score + 0.12, 1.0), 3),
            "quality_score": quality_score,
            "recommended_for_avatar": quality_score >= 0.70
        }

    def _quality_score(
        self,
        size_bytes: int
    ) -> float:
        if size_bytes >= 5_000_000:
            return 0.96

        if size_bytes >= 2_000_000:
            return 0.88

        if size_bytes >= 700_000:
            return 0.81

        if size_bytes >= 250_000:
            return 0.74

        return 0.61
