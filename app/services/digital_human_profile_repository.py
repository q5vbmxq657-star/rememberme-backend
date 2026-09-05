from __future__ import annotations

import json
import os

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

import psycopg

from psycopg.rows import dict_row

from app.models.digital_human_profile import (
    DigitalHumanProfile,
)


class DigitalHumanProfileRepositoryError(RuntimeError):
    pass


class DigitalHumanProfileNotFoundError(
    DigitalHumanProfileRepositoryError
):
    pass


class DigitalHumanProfileRepository:
    """
    Canonical persistent source of truth for avatar and voice identity.

    Provider credentials remain in environment variables.
    Profile-specific provider identifiers are stored in PostgreSQL.
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
            raise DigitalHumanProfileRepositoryError(
                "DATABASE_URL is missing."
            )

    def get(
        self,
        profile_id: UUID,
    ) -> Optional[DigitalHumanProfile]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM digital_human_profiles
                    WHERE profile_id = %s
                    """,
                    (profile_id,),
                )

                row = cursor.fetchone()

        if row is None:
            return None

        return self._profile_from_row(row)

    def require(
        self,
        profile_id: UUID,
    ) -> DigitalHumanProfile:
        profile = self.get(profile_id)

        if profile is None:
            raise DigitalHumanProfileNotFoundError(
                f"Digital human profile not found: {profile_id}"
            )

        return profile

    def ensure(
        self,
        profile_id: UUID,
        *,
        consent_verified: bool = False,
    ) -> DigitalHumanProfile:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO digital_human_profiles (
                        profile_id,
                        consent_verified
                    )
                    VALUES (%s, %s)
                    ON CONFLICT (profile_id)
                    DO UPDATE SET
                        consent_verified = (
                            digital_human_profiles.consent_verified
                            OR EXCLUDED.consent_verified
                        )
                    RETURNING *
                    """,
                    (
                        profile_id,
                        consent_verified,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileRepositoryError(
                "Could not create digital human profile."
            )

        return self._profile_from_row(row)

    def update_quality(
        self,
        profile_id: UUID,
        *,
        quality_tier: str,
        quality_percentage: int,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> DigitalHumanProfile:
        if not 0 <= quality_percentage <= 100:
            raise ValueError(
                "quality_percentage must be between 0 and 100."
            )

        metadata_json = json.dumps(
            metadata or {},
            separators=(",", ":"),
            sort_keys=True,
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digital_human_profiles
                    SET
                        quality_tier = %s,
                        quality_percentage = %s,
                        metadata = metadata || %s::jsonb
                    WHERE profile_id = %s
                    RETURNING *
                    """,
                    (
                        quality_tier,
                        quality_percentage,
                        metadata_json,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileNotFoundError(
                f"Digital human profile not found: {profile_id}"
            )

        return self._profile_from_row(row)

    def set_avatar_training(
        self,
        profile_id: UUID,
        *,
        provider: str,
        status: str,
        provider_job_id: Optional[str] = None,
        replica_id: Optional[str] = None,
        persona_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        expected_provider_job_id: Optional[str] = None,
    ) -> DigitalHumanProfile:
        ready = (
            status == "ready"
            and bool(replica_id)
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digital_human_profiles
                    SET
                        avatar_provider = %s,
                        avatar_training_status = %s,
                        avatar_training_job_id = %s,
                        avatar_replica_id = COALESCE(
                            %s,
                            avatar_replica_id
                        ),
                        avatar_persona_id = COALESCE(
                            %s,
                            avatar_persona_id
                        ),
                        avatar_ready_at = CASE
                            WHEN %s THEN NOW()
                            ELSE avatar_ready_at
                        END,
                        last_error_code = %s,
                        last_error_message = %s
                    WHERE profile_id = %s
                      AND (%s::text IS NULL OR avatar_training_job_id = %s)
                    RETURNING *
                    """,
                    (
                        provider,
                        status,
                        provider_job_id,
                        replica_id,
                        persona_id,
                        ready,
                        error_code,
                        error_message,
                        profile_id,
                        expected_provider_job_id,
                        expected_provider_job_id,
                    ),
                )

                row = cursor.fetchone()
                if row is None and expected_provider_job_id is not None:
                    # A late result may update its own job, never a newer avatar.
                    cursor.execute(
                        "SELECT * FROM digital_human_profiles WHERE profile_id = %s",
                        (profile_id,),
                    )
                    row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileNotFoundError(
                f"Digital human profile not found: {profile_id}"
            )

        return self._profile_from_row(row)

    def set_voice_training(
        self,
        profile_id: UUID,
        *,
        provider: str,
        status: str,
        provider_job_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> DigitalHumanProfile:
        ready = (
            status == "ready"
            and bool(voice_id)
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digital_human_profiles
                    SET
                        voice_provider = %s,
                        voice_training_status = %s,
                        voice_training_job_id = %s,
                        voice_id = COALESCE(
                            %s,
                            voice_id
                        ),
                        voice_ready_at = CASE
                            WHEN %s THEN NOW()
                            ELSE voice_ready_at
                        END,
                        last_error_code = %s,
                        last_error_message = %s
                    WHERE profile_id = %s
                    RETURNING *
                    """,
                    (
                        provider,
                        status,
                        provider_job_id,
                        voice_id,
                        ready,
                        error_code,
                        error_message,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileNotFoundError(
                f"Digital human profile not found: {profile_id}"
            )

        return self._profile_from_row(row)

    def create_training_job(
        self,
        *,
        job_id: UUID,
        profile_id: UUID,
        training_type: str,
        provider: str,
        status: str,
        training_version: int,
        idempotency_key: str,
        request_payload: Optional[
            Dict[str, Any]
        ] = None,
    ) -> Dict[str, Any]:
        payload_json = json.dumps(
            request_payload or {},
            separators=(",", ":"),
            sort_keys=True,
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    WITH inserted AS (
                        INSERT INTO digital_human_training_jobs (
                            job_id,
                            profile_id,
                            training_type,
                            provider,
                            status,
                            training_version,
                            idempotency_key,
                            request_payload
                        )
                        VALUES (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s::jsonb
                        )
                        ON CONFLICT (idempotency_key)
                        DO NOTHING
                        RETURNING *, TRUE AS was_created
                    )
                    SELECT * FROM inserted
                    UNION ALL
                    SELECT jobs.*, FALSE AS was_created
                    FROM digital_human_training_jobs AS jobs
                    WHERE jobs.idempotency_key = %s
                      AND NOT EXISTS (SELECT 1 FROM inserted)
                    LIMIT 1
                    """,
                    (
                        job_id,
                        profile_id,
                        training_type,
                        provider,
                        status,
                        training_version,
                        idempotency_key,
                        payload_json,
                        idempotency_key,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileRepositoryError(
                "Could not create training job."
            )

        return dict(row)

    def update_training_job(
        self,
        job_id: UUID,
        *,
        status: str,
        provider_job_id: Optional[str] = None,
        provider_payload: Optional[
            Dict[str, Any]
        ] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        payload_json = json.dumps(
            provider_payload or {},
            separators=(",", ":"),
            sort_keys=True,
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digital_human_training_jobs
                    SET
                        status = %s,
                        provider_job_id = COALESCE(
                            %s,
                            provider_job_id
                        ),
                        provider_payload =
                            provider_payload
                            || %s::jsonb,
                        error_code = %s,
                        error_message = %s,
                        submitted_at = CASE
                            WHEN %s IN (
                                'submitted',
                                'training'
                            )
                            THEN COALESCE(
                                submitted_at,
                                NOW()
                            )
                            ELSE submitted_at
                        END,
                        completed_at = CASE
                            WHEN %s IN (
                                'ready',
                                'failed',
                                'cancelled',
                                'deleted'
                            )
                            THEN NOW()
                            ELSE completed_at
                        END
                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        status,
                        provider_job_id,
                        payload_json,
                        error_code,
                        error_message,
                        status,
                        status,
                        job_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileRepositoryError(
                f"Training job not found: {job_id}"
            )

        return dict(row)

    def restart_failed_voice_training_job(
        self,
        *,
        job_id: UUID,
        profile_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        """Atomically reclaim a failed, provider-rejected voice request.

        Only jobs with an explicit provider HTTP response are retryable. A
        transport timeout can be ambiguous because the provider may have
        created a voice before the connection failed; retrying that operation
        could create a second biometric voice identity.
        """

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digital_human_training_jobs
                    SET
                        status = 'created',
                        provider_job_id = NULL,
                        provider_payload = '{}'::jsonb,
                        error_code = NULL,
                        error_message = NULL,
                        submitted_at = NULL,
                        completed_at = NULL
                    WHERE job_id = %s
                      AND profile_id = %s
                      AND training_type = 'voice'
                      AND provider = 'elevenlabs'
                      AND status = 'failed'
                      AND provider_job_id IS NULL
                      AND error_code LIKE 'provider_http_%%'
                    RETURNING *
                    """,
                    (
                        job_id,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        return (
            dict(row)
            if row is not None
            else None
        )

    def get_training_job(
        self,
        job_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM digital_human_training_jobs
                    WHERE job_id = %s
                    """,
                    (job_id,),
                )

                row = cursor.fetchone()

        return (
            dict(row)
            if row is not None
            else None
        )

    def get_training_job_by_provider_job_id(
        self,
        *,
        provider: str,
        provider_job_id: str,
    ) -> Optional[Dict[str, Any]]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM digital_human_training_jobs
                    WHERE provider = %s
                      AND provider_job_id = %s
                    """,
                    (
                        provider,
                        provider_job_id,
                    ),
                )

                row = cursor.fetchone()

        return (
            dict(row)
            if row is not None
            else None
        )


    def list_training_jobs(
        self,
        profile_id: UUID,
    ) -> List[Dict[str, Any]]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM digital_human_training_jobs
                    WHERE profile_id = %s
                    ORDER BY created_at DESC
                    """,
                    (profile_id,),
                )

                rows = cursor.fetchall()

        return [
            dict(row)
            for row in rows
        ]

    def resolve_ready_avatar_by_replica(
        self,
        *,
        provider: str,
        replica_id: str,
    ) -> Optional[Dict[str, Any]]:
        normalized_provider = (
            provider.strip().lower()
        )

        normalized_replica_id = (
            replica_id.strip()
        )

        if not normalized_provider:
            raise ValueError(
                "provider is required."
            )

        if not normalized_replica_id:
            raise ValueError(
                "replica_id is required."
            )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        profile.profile_id,
                        profile.training_version,
                        profile.avatar_provider,
                        profile.avatar_replica_id,
                        profile.avatar_training_status,
                        training.job_id
                            AS training_job_id,
                        training.request_payload,
                        training.provider_payload
                    FROM digital_human_profiles
                        AS profile
                    JOIN LATERAL (
                        SELECT
                            job_id,
                            request_payload,
                            provider_payload
                        FROM
                            digital_human_training_jobs
                        WHERE
                            profile_id =
                                profile.profile_id
                            AND training_type =
                                'avatar'
                            AND provider = %s
                            AND training_version =
                                profile.training_version
                            AND status = 'ready'
                        ORDER BY
                            completed_at
                                DESC NULLS LAST,
                            updated_at DESC,
                            created_at DESC
                        LIMIT 1
                    ) AS training
                        ON TRUE
                    WHERE
                        profile.avatar_provider = %s
                        AND
                        profile.avatar_replica_id = %s
                        AND
                        profile.avatar_training_status =
                            'ready'
                    LIMIT 1
                    """,
                    (
                        normalized_provider,
                        normalized_provider,
                        normalized_replica_id,
                    ),
                )

                row = cursor.fetchone()

        if row is None:
            return None

        result = dict(
            row
        )

        request_payload = dict(
            result.get(
                "request_payload"
            )
            or {}
        )

        package_record_id = str(
            request_payload.get(
                "package_record_id",
                "",
            )
        ).strip()

        if not package_record_id:
            raise (
                DigitalHumanProfileRepositoryError(
                    "Ready avatar training job "
                    "has no package_record_id."
                )
            )

        result[
            "package_record_id"
        ] = package_record_id

        return result

    def create_generated_preview_job(
        self,
        *,
        job_id: UUID,
        profile_id: UUID,
        training_version: int,
        package_record_id: str,
        provider: str,
        replica_id: str,
    ) -> Dict[str, Any]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO
                        digital_human_generated_preview_jobs (
                            job_id,
                            profile_id,
                            training_version,
                            package_record_id,
                            provider,
                            replica_id,
                            status
                        )
                    SELECT
                        %s,
                        profile_id,
                        %s,
                        %s,
                        %s,
                        %s,
                        'created'
                    FROM digital_human_profiles
                    WHERE
                        profile_id = %s
                        AND training_version = %s
                        AND avatar_provider = %s
                        AND avatar_replica_id = %s
                        AND avatar_training_status =
                            'ready'
                    RETURNING *
                    """,
                    (
                        job_id,
                        training_version,
                        package_record_id,
                        provider,
                        replica_id,
                        profile_id,
                        training_version,
                        provider,
                        replica_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise (
                DigitalHumanProfileRepositoryError(
                    "Generated preview binding "
                    "is stale or invalid."
                )
            )

        return dict(
            row
        )

    def update_generated_preview_job(
        self,
        *,
        job_id: UUID,
        status: str,
        provider_video_id: Optional[
            str
        ] = None,
        provider_payload: Optional[
            Dict[str, Any]
        ] = None,
        generated_asset_id: Optional[
            UUID
        ] = None,
        media_sha256: Optional[
            str
        ] = None,
        media_content_type: Optional[
            str
        ] = None,
        media_size_bytes: Optional[
            int
        ] = None,
        error_code: Optional[
            str
        ] = None,
        error_message: Optional[
            str
        ] = None,
    ) -> Dict[str, Any]:
        serialized_payload = json.dumps(
            provider_payload or {},
            separators=(",", ":"),
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE
                        digital_human_generated_preview_jobs
                    SET
                        status = %s,

                        provider_video_id =
                            COALESCE(
                                %s,
                                provider_video_id
                            ),

                        provider_payload =
                            CASE
                                WHEN
                                    %s::jsonb =
                                    '{}'::jsonb
                                THEN provider_payload
                                ELSE %s::jsonb
                            END,

                        generated_asset_id =
                            COALESCE(
                                %s,
                                generated_asset_id
                            ),

                        media_sha256 =
                            COALESCE(
                                %s,
                                media_sha256
                            ),

                        media_content_type =
                            COALESCE(
                                %s,
                                media_content_type
                            ),

                        media_size_bytes =
                            COALESCE(
                                %s,
                                media_size_bytes
                            ),

                        error_code = %s,
                        error_message = %s,

                        submitted_at =
                            CASE
                                WHEN %s IN (
                                    'submitted',
                                    'generating'
                                )
                                THEN COALESCE(
                                    submitted_at,
                                    NOW()
                                )
                                ELSE submitted_at
                            END,

                        completed_at =
                            CASE
                                WHEN %s IN (
                                    'ready',
                                    'failed',
                                    'cancelled',
                                    'stale'
                                )
                                THEN COALESCE(
                                    completed_at,
                                    NOW()
                                )
                                ELSE completed_at
                            END,

                        materialized_at =
                            CASE
                                WHEN
                                    %s = 'ready'
                                    AND
                                    %s::uuid IS NOT NULL
                                THEN COALESCE(
                                    materialized_at,
                                    NOW()
                                )
                                ELSE materialized_at
                            END

                    WHERE job_id = %s
                    RETURNING *
                    """,
                    (
                        status,
                        provider_video_id,
                        serialized_payload,
                        serialized_payload,
                        generated_asset_id,
                        media_sha256,
                        media_content_type,
                        media_size_bytes,
                        error_code,
                        error_message,
                        status,
                        status,
                        status,
                        generated_asset_id,
                        job_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise (
                DigitalHumanProfileRepositoryError(
                    "Generated preview job "
                    "was not found."
                )
            )

        return dict(
            row
        )

    def get_generated_preview_job_by_external_id(
        self,
        *,
        provider: str,
        external_job_id: str,
    ) -> Optional[Dict[str, Any]]:
        prefix = (
            f"{provider}:video:"
        )

        provider_video_id = (
            external_job_id.strip()
        )

        if provider_video_id.startswith(
            prefix
        ):
            provider_video_id = (
                provider_video_id[
                    len(prefix):
                ]
            )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        preview.*,

                        profile.training_version
                            AS current_training_version,

                        profile.avatar_replica_id
                            AS current_replica_id,

                        profile.avatar_training_status
                            AS current_avatar_status

                    FROM
                        digital_human_generated_preview_jobs
                        AS preview

                    JOIN digital_human_profiles
                        AS profile
                    ON
                        profile.profile_id =
                            preview.profile_id

                    WHERE
                        preview.provider = %s
                        AND
                        preview.provider_video_id = %s

                    LIMIT 1
                    """,
                    (
                        provider,
                        provider_video_id,
                    ),
                )

                row = cursor.fetchone()

        return (
            dict(
                row
            )
            if row
            else None
        )

    def get_current_identity_verification_receipt(
        self,
        profile_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT receipt.*
                    FROM digital_human_profiles AS profile
                    JOIN digital_human_identity_verification_receipts
                        AS receipt
                      ON receipt.receipt_id =
                         profile.current_identity_verification_receipt_id
                    WHERE profile.profile_id = %s
                      AND receipt.profile_id = profile.profile_id
                    """,
                    (profile_id,),
                )

                row = cursor.fetchone()

        return (
            dict(row)
            if row is not None
            else None
        )

    def append_identity_verification_receipt(
        self,
        *,
        receipt_id: UUID,
        profile_id: UUID,
        training_version: int,
        status: str,
        face_status: str,
        voice_status: str,
        evaluation_version: str,
        evaluated_at: datetime,
        face_model_version: Optional[str] = None,
        voice_model_version: Optional[str] = None,
        face_threshold: Optional[float] = None,
        voice_threshold: Optional[float] = None,
        face_score: Optional[float] = None,
        voice_score: Optional[float] = None,
        evidence: Optional[Dict[str, Any]] = None,
    ) -> DigitalHumanProfile:
        allowed_statuses = {
            "evaluating",
            "verified",
            "rejected",
            "inconclusive",
            "error",
        }

        allowed_face_statuses = {
            "not_evaluated",
            "evaluating",
            "verified",
            "rejected",
            "inconclusive",
            "error",
        }

        allowed_voice_statuses = {
            "not_required",
            "not_evaluated",
            "evaluating",
            "verified",
            "rejected",
            "inconclusive",
            "error",
        }

        if status not in allowed_statuses:
            raise ValueError(
                "Invalid identity verification status."
            )

        if face_status not in allowed_face_statuses:
            raise ValueError(
                "Invalid face verification status."
            )

        if voice_status not in allowed_voice_statuses:
            raise ValueError(
                "Invalid voice verification status."
            )

        if training_version <= 0:
            raise ValueError(
                "training_version must be positive."
            )

        if not evaluation_version.strip():
            raise ValueError(
                "evaluation_version must not be empty."
            )

        for name, value in (
            ("face_threshold", face_threshold),
            ("voice_threshold", voice_threshold),
            ("face_score", face_score),
            ("voice_score", voice_score),
        ):
            if value is not None and not 0 <= value <= 1:
                raise ValueError(
                    f"{name} must be between 0 and 1."
                )

        if status == "verified":
            if face_status != "verified":
                raise ValueError(
                    "Verified identity requires verified face output."
                )

            if voice_status not in {
                "verified",
                "not_required",
            }:
                raise ValueError(
                    "Verified identity requires verified voice output "
                    "or an explicit not_required voice contract."
                )

            if (
                not face_model_version
                or face_threshold is None
                or face_score is None
            ):
                raise ValueError(
                    "Verified identity requires a complete "
                    "model-versioned face evaluation."
                )

            if (
                voice_status == "verified"
                and (
                    not voice_model_version
                    or voice_threshold is None
                    or voice_score is None
                )
            ):
                raise ValueError(
                    "Verified personalized voice requires a complete "
                    "model-versioned speaker evaluation."
                )

        evidence_json = json.dumps(
            evidence or {},
            separators=(",", ":"),
            sort_keys=True,
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT training_version
                    FROM digital_human_profiles
                    WHERE profile_id = %s
                    FOR UPDATE
                    """,
                    (profile_id,),
                )

                profile_row = cursor.fetchone()

                if profile_row is None:
                    raise DigitalHumanProfileNotFoundError(
                        f"Digital human profile not found: {profile_id}"
                    )

                if (
                    int(profile_row["training_version"])
                    != training_version
                ):
                    raise DigitalHumanProfileRepositoryError(
                        "Identity verification receipt training version "
                        "does not match the current profile."
                    )

                cursor.execute(
                    """
                    INSERT INTO
                        digital_human_identity_verification_receipts (
                            receipt_id,
                            profile_id,
                            training_version,
                            status,
                            face_status,
                            voice_status,
                            evaluation_version,
                            face_model_version,
                            voice_model_version,
                            face_threshold,
                            voice_threshold,
                            face_score,
                            voice_score,
                            evidence,
                            evaluated_at
                        )
                    VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s::jsonb, %s
                    )
                    RETURNING receipt_id
                    """,
                    (
                        receipt_id,
                        profile_id,
                        training_version,
                        status,
                        face_status,
                        voice_status,
                        evaluation_version,
                        face_model_version,
                        voice_model_version,
                        face_threshold,
                        voice_threshold,
                        face_score,
                        voice_score,
                        evidence_json,
                        evaluated_at,
                    ),
                )

                inserted = cursor.fetchone()

                if inserted is None:
                    raise DigitalHumanProfileRepositoryError(
                        "Could not persist identity verification receipt."
                    )

                cursor.execute(
                    """
                    UPDATE digital_human_profiles
                    SET
                        identity_verification_status = %s,
                        current_identity_verification_receipt_id = %s,
                        identity_verified_at = CASE
                            WHEN %s = 'verified'
                            THEN %s
                            ELSE NULL
                        END
                    WHERE profile_id = %s
                    RETURNING *
                    """,
                    (
                        status,
                        receipt_id,
                        status,
                        evaluated_at,
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileRepositoryError(
                "Could not project identity verification state."
            )

        return self._profile_from_row(row)

    def clear_voice_identity(
        self,
        profile_id: UUID,
    ) -> DigitalHumanProfile:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digital_human_profiles
                    SET
                        voice_provider = NULL,
                        voice_id = NULL,
                        voice_training_job_id = NULL,
                        voice_training_status = 'deleted',
                        voice_ready_at = NULL,
                        last_error_code = NULL,
                        last_error_message = NULL
                    WHERE profile_id = %s
                    RETURNING *
                    """,
                    (profile_id,),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileNotFoundError(
                f"Digital human profile not found: {profile_id}"
            )

        return self._profile_from_row(row)

    def mark_runtime_verified(
        self,
        profile_id: UUID,
    ) -> DigitalHumanProfile:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE digital_human_profiles
                    SET runtime_verified_at = NOW()
                    WHERE profile_id = %s
                    RETURNING *
                    """,
                    (profile_id,),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileNotFoundError(
                f"Digital human profile not found: {profile_id}"
            )

        return self._profile_from_row(row)


    @staticmethod
    def _profile_advisory_lock_keys(
        profile_id: UUID,
    ) -> tuple[int, int]:
        profile_bytes = profile_id.bytes

        return (
            int.from_bytes(
                profile_bytes[0:4],
                byteorder="big",
                signed=True,
            ),
            int.from_bytes(
                profile_bytes[4:8],
                byteorder="big",
                signed=True,
            ),
        )

    def _lock_profile_scope(
        self,
        cursor: Any,
        profile_id: UUID,
    ) -> None:
        first_key, second_key = (
            self._profile_advisory_lock_keys(
                profile_id
            )
        )

        cursor.execute(
            """
            SELECT pg_advisory_xact_lock(
                %s::integer,
                %s::integer
            )
            """,
            (
                first_key,
                second_key,
            ),
        )

    def _require_profile_write_allowed_with_cursor(
        self,
        cursor: Any,
        profile_id: UUID,
    ) -> None:
        cursor.execute(
            """
            SELECT request_id
            FROM
                digital_human_profile_erasure_requests
            WHERE profile_id = %s::uuid
              AND status <> 'completed'
            ORDER BY
                requested_at DESC,
                request_id DESC
            LIMIT 1
            """,
            (
                profile_id,
            ),
        )

        if cursor.fetchone() is not None:
            raise DigitalHumanProfileRepositoryError(
                "Profile writes are blocked while "
                "an erasure request is active."
            )

    def create_profile_erasure_request(
        self,
        *,
        request_id: UUID,
        profile_id: UUID,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        normalized_key = idempotency_key.strip()

        if not normalized_key:
            raise ValueError(
                "idempotency_key is required."
            )

        if len(normalized_key) > 200:
            raise ValueError(
                "idempotency_key is too long."
            )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                self._lock_profile_scope(
                    cursor,
                    profile_id,
                )

                cursor.execute(
                    """
                    SELECT
                        avatar_provider,
                        avatar_replica_id,
                        avatar_persona_id,
                        avatar_training_job_id,
                        voice_provider,
                        voice_id,
                        voice_training_job_id,
                        training_version
                    FROM digital_human_profiles
                    WHERE profile_id = %s::uuid
                    FOR UPDATE
                    """,
                    (
                        profile_id,
                    ),
                )

                profile = cursor.fetchone()

                if profile is None:
                    raise DigitalHumanProfileNotFoundError(
                        "Digital human profile "
                        f"not found: {profile_id}"
                    )

                cursor.execute(
                    """
                    SELECT *
                    FROM
                        digital_human_profile_erasure_requests
                    WHERE profile_id = %s::uuid
                      AND status <> 'completed'
                    ORDER BY
                        requested_at DESC,
                        request_id DESC
                    LIMIT 1
                    """,
                    (
                        profile_id,
                    ),
                )

                active_request = cursor.fetchone()

                if active_request is not None:
                    row = active_request
                else:
                    provider_snapshot = {
                        "avatar_provider":
                            profile["avatar_provider"],
                        "avatar_replica_id":
                            profile["avatar_replica_id"],
                        "avatar_persona_id":
                            profile["avatar_persona_id"],
                        "avatar_training_job_id":
                            profile[
                                "avatar_training_job_id"
                            ],
                        "voice_provider":
                            profile["voice_provider"],
                        "voice_id":
                            profile["voice_id"],
                        "voice_training_job_id":
                            profile[
                                "voice_training_job_id"
                            ],
                        "training_version":
                            profile["training_version"],
                    }

                    cursor.execute(
                        """
                        INSERT INTO
                            digital_human_profile_erasure_requests (
                                request_id,
                                profile_id,
                                idempotency_key,
                                status,
                                provider_snapshot
                            )
                        VALUES (
                            %s::uuid,
                            %s::uuid,
                            %s::text,
                            'requested',
                            %s::jsonb
                        )
                        ON CONFLICT (
                            idempotency_key
                        )
                        DO UPDATE SET
                            updated_at = NOW()
                        RETURNING *
                        """,
                        (
                            request_id,
                            profile_id,
                            normalized_key,
                            json.dumps(
                                provider_snapshot,
                                separators=(",", ":"),
                            ),
                        ),
                    )

                    row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise DigitalHumanProfileRepositoryError(
                "Could not create profile "
                "erasure request."
            )

        if (
            row["profile_id"] is not None
            and UUID(str(row["profile_id"]))
            != profile_id
        ):
            raise DigitalHumanProfileRepositoryError(
                "Erasure idempotency key belongs "
                "to another profile."
            )

        return dict(row)




    def get_active_profile_erasure_request(
        self,
        *,
        profile_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM
                        digital_human_profile_erasure_requests
                    WHERE profile_id = %s::uuid
                      AND status <> 'completed'
                    ORDER BY
                        requested_at DESC,
                        request_id DESC
                    LIMIT 1
                    """,
                    (
                        profile_id,
                    ),
                )

                row = cursor.fetchone()

        return dict(row) if row is not None else None

    def get_profile_erasure_request_for_resume(
        self,
        *,
        request_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM
                        digital_human_profile_erasure_requests
                    WHERE request_id = %s::uuid
                      AND status = 'retryable_failed'
                      AND resume_stage IS NOT NULL
                      AND (
                          next_retry_at IS NULL
                          OR next_retry_at <= NOW()
                      )
                    LIMIT 1
                    """,
                    (
                        request_id,
                    ),
                )

                row = cursor.fetchone()

        return dict(row) if row is not None else None

    def require_profile_write_allowed(
        self,
        *,
        profile_id: UUID,
    ) -> None:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                self._lock_profile_scope(
                    cursor,
                    profile_id,
                )

                self._require_profile_write_allowed_with_cursor(
                    cursor,
                    profile_id,
                )

            connection.commit()

    def transition_profile_erasure_request(
        self,
        *,
        request_id: UUID,
        expected_status: str,
        new_status: str,
        resume_stage: Optional[str] = None,
        next_retry_at: Optional[datetime] = None,
        storage_asset_ids: Optional[
            list[str]
        ] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        allowed_transitions = {
            "requested": {
                "provider_cleanup",
                "retryable_failed",
            },
            "provider_cleanup": {
                "provider_cleanup_required",
                "storage_cleanup",
                "retryable_failed",
            },
            "provider_cleanup_required": {
                "provider_cleanup",
                "retryable_failed",
            },
            "storage_cleanup": {
                "database_cleanup",
                "retryable_failed",
            },
            "database_cleanup": {
                "completed",
                "retryable_failed",
            },
            "retryable_failed": {
                "requested",
                "provider_cleanup",
                "storage_cleanup",
                "database_cleanup",
            },
            "completed": set(),
        }

        resumable_stages = {
            "requested",
            "provider_cleanup",
            "storage_cleanup",
            "database_cleanup",
        }

        if expected_status not in allowed_transitions:
            raise ValueError(
                "Unsupported expected erasure status."
            )

        if (
            new_status
            not in allowed_transitions[
                expected_status
            ]
        ):
            raise ValueError(
                "Unsupported erasure status transition."
            )

        if new_status == "retryable_failed":
            if resume_stage not in resumable_stages:
                raise ValueError(
                    "retryable_failed requires "
                    "a valid resume_stage."
                )
        elif (
            resume_stage is not None
            or next_retry_at is not None
        ):
            raise ValueError(
                "Resume metadata is only allowed "
                "for retryable_failed."
            )

        serialized_assets = (
            json.dumps(
                storage_asset_ids,
                separators=(",", ":"),
            )
            if storage_asset_ids is not None
            else None
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE
                        digital_human_profile_erasure_requests
                    SET
                        status = %s::text,

                        resume_stage =
                            CASE
                                WHEN %s::text =
                                    'retryable_failed'
                                THEN %s::text
                                ELSE NULL::text
                            END,

                        next_retry_at =
                            CASE
                                WHEN %s::text =
                                    'retryable_failed'
                                THEN %s::timestamptz
                                ELSE NULL::timestamptz
                            END,

                        attempt_count =
                            CASE
                                WHEN %s::text =
                                    'retryable_failed'
                                THEN attempt_count + 1
                                ELSE attempt_count
                            END,

                        storage_asset_ids =
                            CASE
                                WHEN %s::jsonb IS NULL
                                THEN storage_asset_ids
                                ELSE %s::jsonb
                            END,

                        error_code = %s::text,
                        error_message = %s::text,

                        started_at =
                            CASE
                                WHEN %s::text <> 'requested'
                                THEN COALESCE(
                                    started_at,
                                    NOW()
                                )
                                ELSE started_at
                            END,

                        completed_at =
                            CASE
                                WHEN %s::text = 'completed'
                                THEN COALESCE(
                                    completed_at,
                                    NOW()
                                )
                                ELSE completed_at
                            END,

                        profile_id =
                            CASE
                                WHEN %s::text = 'completed'
                                THEN NULL::uuid
                                ELSE profile_id
                            END,

                        updated_at = NOW()

                    WHERE request_id = %s::uuid
                      AND status = %s::text
                    RETURNING *
                    """,
                    (
                        new_status,
                        new_status,
                        resume_stage,
                        new_status,
                        next_retry_at,
                        new_status,
                        serialized_assets,
                        serialized_assets,
                        error_code,
                        error_message,
                        new_status,
                        new_status,
                        new_status,
                        request_id,
                        expected_status,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            current = self.get_profile_erasure_request(
                request_id=request_id,
            )

            if current is None:
                raise DigitalHumanProfileRepositoryError(
                    "Profile erasure request "
                    "was not found."
                )

            raise DigitalHumanProfileRepositoryError(
                "Profile erasure request changed "
                "concurrently."
            )

        return dict(row)

    def get_profile_erasure_request(
        self,
        *,
        request_id: UUID,
    ) -> Optional[Dict[str, Any]]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT *
                    FROM
                        digital_human_profile_erasure_requests
                    WHERE request_id = %s
                    """,
                    (
                        request_id,
                    ),
                )

                row = cursor.fetchone()

        return (
            dict(row)
            if row is not None
            else None
        )


    def update_profile_erasure_request(
        self,
        *,
        request_id: UUID,
        status: str,
        storage_asset_ids: Optional[
            list[str]
        ] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> Dict[str, Any]:
        allowed_statuses = {
            "requested",
            "provider_cleanup",
            "provider_cleanup_required",
            "storage_cleanup",
            "database_cleanup",
            "retryable_failed",
            "completed",
        }

        if status not in allowed_statuses:
            raise ValueError(
                "Unsupported erasure status."
            )

        serialized_assets = (
            json.dumps(
                storage_asset_ids,
                separators=(",", ":"),
            )
            if storage_asset_ids is not None
            else None
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE
                        digital_human_profile_erasure_requests
                    SET
                        status = %s,

                        storage_asset_ids =
                            CASE
                                WHEN %s::jsonb IS NULL
                                THEN storage_asset_ids
                                ELSE %s::jsonb
                            END,

                        error_code = %s,
                        error_message = %s,

                        started_at =
                            CASE
                                WHEN %s <> 'requested'
                                THEN COALESCE(
                                    started_at,
                                    NOW()
                                )
                                ELSE started_at
                            END,

                        completed_at =
                            CASE
                                WHEN %s = 'completed'
                                THEN COALESCE(
                                    completed_at,
                                    NOW()
                                )
                                ELSE completed_at
                            END,

                        profile_id =
                            CASE
                                WHEN %s = 'completed'
                                THEN NULL
                                ELSE profile_id
                            END,

                        updated_at = NOW()

                    WHERE request_id = %s
                    RETURNING *
                    """,
                    (
                        status,
                        serialized_assets,
                        serialized_assets,
                        error_code,
                        error_message,
                        status,
                        status,
                        status,
                        request_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise (
                DigitalHumanProfileRepositoryError(
                    "Profile erasure request "
                    "was not found."
                )
            )

        return dict(row)


    def delete_profile_graph(
        self,
        *,
        profile_id: UUID,
    ) -> Dict[str, int]:
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        (
                            SELECT COUNT(*)
                            FROM digital_human_training_jobs
                            WHERE profile_id = %s
                        ) AS training_jobs,

                        (
                            SELECT COUNT(*)
                            FROM avatar_evidence_assets
                            WHERE profile_id = %s
                        ) AS evidence_assets,

                        (
                            SELECT COUNT(*)
                            FROM
                                digital_human_identity_verification_receipts
                            WHERE profile_id = %s
                        ) AS identity_receipts,

                        (
                            SELECT COUNT(*)
                            FROM
                                digital_human_generated_preview_jobs
                            WHERE profile_id = %s
                        ) AS preview_jobs
                    """,
                    (
                        profile_id,
                        profile_id,
                        profile_id,
                        profile_id,
                    ),
                )

                counts = cursor.fetchone()

                cursor.execute(
                    """
                    DELETE FROM digital_human_profiles
                    WHERE profile_id = %s
                    RETURNING profile_id
                    """,
                    (
                        profile_id,
                    ),
                )

                deleted = cursor.fetchone()

            connection.commit()

        return {
            "profile_deleted":
                1
                if deleted is not None
                else 0,

            "training_jobs":
                int(
                    counts[
                        "training_jobs"
                    ]
                    if counts
                    else 0
                ),

            "evidence_assets":
                int(
                    counts[
                        "evidence_assets"
                    ]
                    if counts
                    else 0
                ),

            "identity_receipts":
                int(
                    counts[
                        "identity_receipts"
                    ]
                    if counts
                    else 0
                ),

            "preview_jobs":
                int(
                    counts[
                        "preview_jobs"
                    ]
                    if counts
                    else 0
                ),
        }

    def _profile_from_row(
        self,
        row: Dict[str, Any],
    ) -> DigitalHumanProfile:
        return DigitalHumanProfile(
            profile_id=row["profile_id"],
            quality_tier=row["quality_tier"],
            quality_percentage=row["quality_percentage"],
            avatar_provider=row["avatar_provider"],
            avatar_replica_id=row["avatar_replica_id"],
            avatar_persona_id=row["avatar_persona_id"],
            avatar_training_job_id=row["avatar_training_job_id"],
            avatar_training_status=row["avatar_training_status"],
            voice_provider=row["voice_provider"],
            voice_id=row["voice_id"],
            voice_training_job_id=row["voice_training_job_id"],
            voice_training_status=row["voice_training_status"],
            approved_portrait_url=row["approved_portrait_url"],
            consent_verified=row["consent_verified"],
            training_version=row["training_version"],
            runtime_verified_at=row["runtime_verified_at"],
            avatar_ready_at=row["avatar_ready_at"],
            voice_ready_at=row["voice_ready_at"],
            last_error_code=row["last_error_code"],
            last_error_message=row["last_error_message"],
            identity_verification_status=(
                row.get("identity_verification_status")
                or "not_evaluated"
            ),
            current_identity_verification_receipt_id=(
                row.get(
                    "current_identity_verification_receipt_id"
                )
            ),
            identity_verified_at=(
                row.get("identity_verified_at")
            ),
            metadata=dict(row["metadata"] or {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
