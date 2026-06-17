from __future__ import annotations

import json
import os

from typing import Any, Dict, List, Optional
from uuid import UUID

import psycopg

from psycopg.rows import dict_row

from app.models.avatar_evidence_asset import (
    AvatarEvidenceAsset,
)


class AvatarEvidenceRepositoryError(RuntimeError):
    pass


class AvatarEvidenceNotFoundError(
    AvatarEvidenceRepositoryError
):
    pass


class AvatarEvidenceConflictError(
    AvatarEvidenceRepositoryError
):
    pass


class AvatarEvidenceRepository:
    """
    Canonical PostgreSQL source of truth for profile-specific avatar
    evidence metadata, analysis, lifecycle and user selection.

    Binary media remains in the configured private media storage layer.
    """

    def __init__(
        self,
        database_url: Optional[str] = None,
    ) -> None:
        self.database_url = (
            database_url
            or os.getenv("DATABASE_URL")
            or ""
        ).strip()

        if not self.database_url:
            raise AvatarEvidenceRepositoryError(
                "DATABASE_URL is missing."
            )

    def upsert_uploaded_asset(
        self,
        *,
        asset_id: UUID,
        profile_id: UUID,
        asset_type: str,
        evidence_kind: str,
        title: str,
        filename: str,
        content_type: str,
        size_bytes: int,
        storage_backend: str,
        storage_key: str,
        storage_path: Optional[str],
        duration_seconds: Optional[float] = None,
        quality_score: float = 0.0,
        has_face: bool = False,
        has_frontal_face: bool = False,
        has_clear_lighting: bool = False,
        has_voice: bool = False,
        voice_usable: bool = False,
        motion_usable: bool = False,
        emotional_presence_score: float = 0.0,
        identity_consistency_score: float = 0.0,
        motion_quality_score: float = 0.0,
        expression_range_score: float = 0.0,
        lip_visibility_score: float = 0.0,
        head_pose_stability_score: float = 0.0,
        recommended_for_avatar: bool = False,
        rejection_code: Optional[str] = None,
        rejection_reason: Optional[str] = None,
        analysis_version: str = "legacy-v1",
        analysis_metadata: Optional[
            Dict[str, Any]
        ] = None,
        source_metadata: Optional[
            Dict[str, Any]
        ] = None,
    ) -> AvatarEvidenceAsset:
        score_values = {
            "quality_score": quality_score,
            "emotional_presence_score": (
                emotional_presence_score
            ),
            "identity_consistency_score": (
                identity_consistency_score
            ),
            "motion_quality_score": (
                motion_quality_score
            ),
            "expression_range_score": (
                expression_range_score
            ),
            "lip_visibility_score": (
                lip_visibility_score
            ),
            "head_pose_stability_score": (
                head_pose_stability_score
            ),
        }

        for score_name, score_value in score_values.items():
            self._validate_score(
                score_name,
                score_value,
            )

        processing_status = (
            "rejected"
            if rejection_reason
            else "uploaded"
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO avatar_evidence_assets (
                        asset_id,
                        profile_id,
                        asset_type,
                        evidence_kind,
                        title,
                        filename,
                        content_type,
                        size_bytes,
                        storage_backend,
                        storage_key,
                        storage_path,
                        processing_status,
                        duration_seconds,
                        quality_score,
                        has_face,
                        has_frontal_face,
                        has_clear_lighting,
                        has_voice,
                        voice_usable,
                        motion_usable,
                        emotional_presence_score,
                        identity_consistency_score,
                        motion_quality_score,
                        expression_range_score,
                        lip_visibility_score,
                        head_pose_stability_score,
                        recommended_for_avatar,
                        rejection_code,
                        rejection_reason,
                        analysis_version,
                        analysis_metadata,
                        source_metadata
                    )
                    VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s,
                        %s::jsonb,
                        %s::jsonb
                    )
                    ON CONFLICT (asset_id)
                    DO UPDATE SET
                        title = EXCLUDED.title,
                        filename = EXCLUDED.filename,
                        content_type = EXCLUDED.content_type,
                        size_bytes = EXCLUDED.size_bytes,
                        storage_backend = EXCLUDED.storage_backend,
                        storage_key = EXCLUDED.storage_key,
                        storage_path = EXCLUDED.storage_path,
                        duration_seconds = EXCLUDED.duration_seconds,
                        quality_score = EXCLUDED.quality_score,
                        has_face = EXCLUDED.has_face,
                        has_frontal_face =
                            EXCLUDED.has_frontal_face,
                        has_clear_lighting =
                            EXCLUDED.has_clear_lighting,
                        has_voice = EXCLUDED.has_voice,
                        voice_usable = EXCLUDED.voice_usable,
                        motion_usable = EXCLUDED.motion_usable,
                        emotional_presence_score =
                            EXCLUDED.emotional_presence_score,
                        identity_consistency_score =
                            EXCLUDED.identity_consistency_score,
                        motion_quality_score =
                            EXCLUDED.motion_quality_score,
                        expression_range_score =
                            EXCLUDED.expression_range_score,
                        lip_visibility_score =
                            EXCLUDED.lip_visibility_score,
                        head_pose_stability_score =
                            EXCLUDED.head_pose_stability_score,
                        recommended_for_avatar =
                            EXCLUDED.recommended_for_avatar,
                        rejection_code =
                            EXCLUDED.rejection_code,
                        rejection_reason =
                            EXCLUDED.rejection_reason,
                        analysis_version =
                            EXCLUDED.analysis_version,
                        analysis_metadata =
                            avatar_evidence_assets
                            .analysis_metadata
                            || EXCLUDED.analysis_metadata,
                        source_metadata =
                            avatar_evidence_assets
                            .source_metadata
                            || EXCLUDED.source_metadata
                    RETURNING *
                    """,
                    (
                        asset_id,
                        profile_id,
                        asset_type,
                        evidence_kind,
                        title,
                        filename,
                        content_type,
                        max(size_bytes, 0),
                        storage_backend,
                        storage_key,
                        storage_path,
                        processing_status,
                        duration_seconds,
                        quality_score,
                        has_face,
                        has_frontal_face,
                        has_clear_lighting,
                        has_voice,
                        voice_usable,
                        motion_usable,
                        emotional_presence_score,
                        identity_consistency_score,
                        motion_quality_score,
                        expression_range_score,
                        lip_visibility_score,
                        head_pose_stability_score,
                        recommended_for_avatar,
                        rejection_code,
                        rejection_reason,
                        analysis_version,
                        self._json(analysis_metadata),
                        self._json(source_metadata),
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise AvatarEvidenceRepositoryError(
                "Could not persist avatar evidence asset."
            )

        return self._asset_from_row(row)

    def get(
        self,
        asset_id: UUID,
    ) -> Optional[AvatarEvidenceAsset]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM avatar_evidence_assets
                    WHERE asset_id = %s
                    """,
                    (asset_id,),
                )

                row = cursor.fetchone()

        return (
            self._asset_from_row(row)
            if row is not None
            else None
        )

    def require(
        self,
        asset_id: UUID,
    ) -> AvatarEvidenceAsset:
        asset = self.get(asset_id)

        if asset is None:
            raise AvatarEvidenceNotFoundError(
                f"Avatar evidence asset not found: {asset_id}"
            )

        return asset

    def list_profile_assets(
        self,
        profile_id: UUID,
        *,
        include_archived: bool = False,
    ) -> List[AvatarEvidenceAsset]:
        archived_clause = (
            ""
            if include_archived
            else "AND archived_at IS NULL"
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM avatar_evidence_assets
                    WHERE profile_id = %s
                    {archived_clause}
                    ORDER BY
                        is_included_in_avatar DESC,
                        recommended_for_avatar DESC,
                        quality_score DESC,
                        updated_at DESC,
                        asset_id ASC
                    """,
                    (profile_id,),
                )

                rows = cursor.fetchall()

        return [
            self._asset_from_row(row)
            for row in rows
        ]

    def list_active_assets(
        self,
        profile_id: UUID,
        *,
        evidence_kind: Optional[str] = None,
    ) -> List[AvatarEvidenceAsset]:
        conditions = [
            "profile_id = %s",
            "archived_at IS NULL",
            "is_included_in_avatar = TRUE",
            "quality_score >= 0.72",
            "rejection_reason IS NULL",
            "processing_status IN ('ready', 'training')",
        ]

        values: List[Any] = [
            profile_id
        ]

        if evidence_kind:
            conditions.append(
                "evidence_kind = %s"
            )
            values.append(
                evidence_kind
            )

        where_clause = " AND ".join(
            conditions
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    SELECT *
                    FROM avatar_evidence_assets
                    WHERE {where_clause}
                    ORDER BY
                        CASE
                            WHEN selection_status = 'primary'
                            THEN 1
                            ELSE 0
                        END DESC,
                        recommended_for_avatar DESC,
                        quality_score DESC,
                        updated_at DESC,
                        asset_id ASC
                    """,
                    values,
                )

                rows = cursor.fetchall()

        return [
            self._asset_from_row(row)
            for row in rows
        ]

    def select_for_avatar(
        self,
        *,
        profile_id: UUID,
        asset_id: UUID,
        make_primary: bool = False,
    ) -> AvatarEvidenceAsset:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM avatar_evidence_assets
                    WHERE asset_id = %s
                      AND profile_id = %s
                    FOR UPDATE
                    """,
                    (
                        asset_id,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

                if row is None:
                    raise AvatarEvidenceNotFoundError(
                        "Avatar evidence asset does not "
                        "belong to this profile."
                    )

                asset = self._asset_from_row(row)

                self._validate_selectable(
                    asset
                )

                if make_primary:
                    cursor.execute(
                        """
                        UPDATE avatar_evidence_assets
                        SET selection_status = 'selected'
                        WHERE profile_id = %s
                          AND evidence_kind = %s
                          AND selection_status = 'primary'
                          AND asset_id <> %s
                        """,
                        (
                            profile_id,
                            asset.evidence_kind,
                            asset_id,
                        ),
                    )

                cursor.execute(
                    """
                    UPDATE avatar_evidence_assets
                    SET
                        processing_status = CASE
                            WHEN processing_status = 'uploaded'
                            THEN 'ready'
                            ELSE processing_status
                        END,
                        is_included_in_avatar = TRUE,
                        included_in_avatar_at = COALESCE(
                            included_in_avatar_at,
                            NOW()
                        ),
                        selection_status = %s
                    WHERE asset_id = %s
                      AND profile_id = %s
                    RETURNING *
                    """,
                    (
                        (
                            "primary"
                            if make_primary
                            else "selected"
                        ),
                        asset_id,
                        profile_id,
                    ),
                )

                updated = cursor.fetchone()

            connection.commit()

        if updated is None:
            raise AvatarEvidenceRepositoryError(
                "Could not select avatar evidence."
            )

        return self._asset_from_row(
            updated
        )

    def remove_from_avatar(
        self,
        *,
        profile_id: UUID,
        asset_id: UUID,
    ) -> AvatarEvidenceAsset:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE avatar_evidence_assets
                    SET
                        is_included_in_avatar = FALSE,
                        included_in_avatar_at = NULL,
                        selection_status = 'removed',
                        processing_status = CASE
                            WHEN processing_status = 'training'
                            THEN 'ready'
                            ELSE processing_status
                        END
                    WHERE asset_id = %s
                      AND profile_id = %s
                    RETURNING *
                    """,
                    (
                        asset_id,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise AvatarEvidenceNotFoundError(
                "Avatar evidence asset does not "
                "belong to this profile."
            )

        return self._asset_from_row(row)

    def archive(
        self,
        *,
        profile_id: UUID,
        asset_id: UUID,
    ) -> AvatarEvidenceAsset:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE avatar_evidence_assets
                    SET
                        processing_status = 'archived',
                        is_included_in_avatar = FALSE,
                        included_in_avatar_at = NULL,
                        selection_status = 'removed',
                        archived_at = NOW()
                    WHERE asset_id = %s
                      AND profile_id = %s
                    RETURNING *
                    """,
                    (
                        asset_id,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise AvatarEvidenceNotFoundError(
                "Avatar evidence asset does not "
                "belong to this profile."
            )

        return self._asset_from_row(row)

    def resolve_primary(
        self,
        profile_id: UUID,
        evidence_kind: str,
    ) -> Optional[AvatarEvidenceAsset]:
        assets = self.list_active_assets(
            profile_id,
            evidence_kind=evidence_kind,
        )

        return (
            assets[0]
            if assets
            else None
        )

    def _validate_selectable(
        self,
        asset: AvatarEvidenceAsset,
    ) -> None:
        if asset.is_archived:
            raise AvatarEvidenceConflictError(
                "Archived evidence cannot be selected."
            )

        if not asset.is_training_source:
            raise AvatarEvidenceConflictError(
                "Generated avatar output cannot be "
                "selected as source evidence."
            )

        if asset.rejection_reason:
            raise AvatarEvidenceConflictError(
                asset.rejection_reason
            )

        if asset.quality_score < 0.72:
            raise AvatarEvidenceConflictError(
                "Avatar evidence quality is below "
                "the required threshold."
            )

        if asset.evidence_kind == "identity_photo":
            if not (
                asset.has_face
                and asset.has_frontal_face
                and asset.has_clear_lighting
            ):
                raise AvatarEvidenceConflictError(
                    "Identity evidence must contain a "
                    "clear, frontal and well-lit face."
                )

        if (
            asset.evidence_kind == "motion_video"
            and not asset.motion_usable
        ):
            raise AvatarEvidenceConflictError(
                "Motion evidence is not usable."
            )

        if (
            asset.evidence_kind == "voice_sample"
            and not asset.voice_usable
        ):
            raise AvatarEvidenceConflictError(
                "Voice evidence is not usable."
            )

    def _asset_from_row(
        self,
        row: Dict[str, Any],
    ) -> AvatarEvidenceAsset:
        return AvatarEvidenceAsset(
            asset_id=row["asset_id"],
            profile_id=row["profile_id"],
            asset_type=row["asset_type"],
            evidence_kind=row["evidence_kind"],
            title=row["title"],
            filename=row["filename"],
            content_type=row["content_type"],
            size_bytes=row["size_bytes"],
            storage_backend=row["storage_backend"],
            storage_key=row["storage_key"],
            storage_path=row["storage_path"],
            processing_status=row["processing_status"],
            selection_status=row["selection_status"],
            is_included_in_avatar=(
                row["is_included_in_avatar"]
            ),
            included_in_avatar_at=(
                row["included_in_avatar_at"]
            ),
            archived_at=row["archived_at"],
            duration_seconds=row["duration_seconds"],
            quality_score=float(
                row["quality_score"]
            ),
            has_face=row["has_face"],
            has_frontal_face=(
                row["has_frontal_face"]
            ),
            has_clear_lighting=(
                row["has_clear_lighting"]
            ),
            has_voice=row["has_voice"],
            voice_usable=row["voice_usable"],
            motion_usable=row["motion_usable"],
            emotional_presence_score=float(
                row["emotional_presence_score"]
            ),
            identity_consistency_score=float(
                row["identity_consistency_score"]
            ),
            motion_quality_score=float(
                row["motion_quality_score"]
            ),
            expression_range_score=float(
                row["expression_range_score"]
            ),
            lip_visibility_score=float(
                row["lip_visibility_score"]
            ),
            head_pose_stability_score=float(
                row["head_pose_stability_score"]
            ),
            recommended_for_avatar=(
                row["recommended_for_avatar"]
            ),
            rejection_code=row["rejection_code"],
            rejection_reason=row["rejection_reason"],
            analysis_version=row["analysis_version"],
            analysis_metadata=dict(
                row["analysis_metadata"] or {}
            ),
            source_metadata=dict(
                row["source_metadata"] or {}
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _json(
        self,
        value: Optional[Dict[str, Any]],
    ) -> str:
        return json.dumps(
            value or {},
            separators=(",", ":"),
            sort_keys=True,
        )

    def _validate_score(
        self,
        name: str,
        value: float,
    ) -> None:
        if not 0.0 <= float(value) <= 1.0:
            raise ValueError(
                f"{name} must be between 0 and 1."
            )
