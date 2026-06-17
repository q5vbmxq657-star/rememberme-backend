from __future__ import annotations

import json
import os

from pathlib import Path
from typing import Any, Dict, Optional
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

    def apply_migration(
        self,
        migration_path: Path,
    ) -> None:
        if not migration_path.is_file():
            raise DigitalHumanProfileRepositoryError(
                f"Migration file does not exist: {migration_path}"
            )

        sql = migration_path.read_text(
            encoding="utf-8"
        )

        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(sql)

            connection.commit()

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
                    ),
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
                    DO UPDATE SET
                        updated_at = NOW()
                    RETURNING *
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
            metadata=dict(row["metadata"] or {}),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
