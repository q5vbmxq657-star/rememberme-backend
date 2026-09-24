from __future__ import annotations

import json
import os
import re
from contextlib import nullcontext

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

import psycopg

from psycopg.rows import dict_row

from app.models.digital_human_profile import (
    DigitalHumanProfile,
)

VOICE_RETRY_ERROR_PATTERN = r'^provider_http_(400|401|403|404|413|415|422|429)(_[A-Za-z0-9_-]{1,64})?$'


class DigitalHumanProfileRepositoryError(RuntimeError):
    pass


class DigitalHumanProfileNotFoundError(
    DigitalHumanProfileRepositoryError
):
    pass


class StaleVoiceTrainingError(DigitalHumanProfileRepositoryError):
    pass


class StaleAvatarTrainingError(DigitalHumanProfileRepositoryError):
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
        training_job_id: Optional[UUID] = None,
        _connection=None,
    ) -> DigitalHumanProfile:
        if provider == 'tavus' and replica_id and provider_job_id != f'tavus:{replica_id}':
            raise StaleAvatarTrainingError('Avatar provider identity does not match.')
        ready = (
            status == "ready"
            and bool(replica_id)
        )

        with (nullcontext(_connection) if _connection is not None else psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        )) as connection:
            with connection.cursor() as cursor:
                # Consent changes and erasure take this same profile lock.
                cursor.execute('SELECT * FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile_id,))
                current = cursor.fetchone()
                if current is None:
                    raise DigitalHumanProfileNotFoundError('Avatar profile was not found.')
                cursor.execute('SELECT 1 FROM digital_human_profile_erasure_requests WHERE profile_id=%s LIMIT 1', (profile_id,))
                if cursor.fetchone():
                    raise StaleAvatarTrainingError('Avatar profile is being erased.')
                cursor.execute("""SELECT * FROM digital_human_training_jobs
                    WHERE profile_id=%s AND provider=%s AND training_type='avatar'
                    ORDER BY created_at DESC, job_id DESC LIMIT 1 FOR UPDATE""", (profile_id, provider))
                job = cursor.fetchone()
                replacing_verified_avatar = (
                    current.get('avatar_training_status') == 'ready'
                    and bool(current.get('avatar_replica_id'))
                    and current.get('runtime_verified_at') is not None
                    and current.get('consent_verified') is True
                    and current.get('avatar_training_job_id') != provider_job_id
                )
                if (not job or (training_job_id is not None and job['job_id'] != training_job_id)
                        or job['provider_job_id'] != provider_job_id
                        or (training_job_id is None and (not provider_job_id or job['provider_job_id'] != provider_job_id))
                        or (expected_provider_job_id is not None and current['avatar_training_job_id'] != expected_provider_job_id
                            and not replacing_verified_avatar)):
                    raise StaleAvatarTrainingError('Avatar training is no longer current.')
                if (job.get('status') in {'cancelled', 'deleted'}
                        or not self._avatar_status_allows(job.get('status', 'training'), status)
                        or (current['avatar_training_job_id'] == provider_job_id
                            and not self._avatar_status_allows(current.get('avatar_training_status', 'training'), status))):
                    raise StaleAvatarTrainingError('Avatar training result is superseded.')
                payload = job['request_payload'] or {}
                revision = payload.get('_stay_consent_revision')
                photo, video = bool(payload.get('train_image_url')), bool(payload.get('train_video_url'))
                if type(revision) is not int or revision < 1 or photo == video:
                    raise StaleAvatarTrainingError('Avatar training permission provenance is unavailable.')
                from app.schemas.profile_consent import CONSENT_POLICY_VERSION
                cursor.execute('SELECT revision, policy_version, purposes FROM profile_purpose_consents WHERE profile_id=%s', (profile_id,))
                consent = cursor.fetchone()
                required = {'provider_processing', 'photo_likeness' if photo else 'video_motion'}
                if video:
                    required.add('voice_synthesis')
                if (not consent or consent['revision'] != revision
                        or consent['policy_version'] != CONSENT_POLICY_VERSION
                        or not required.issubset(consent['purposes'])):
                    raise StaleAvatarTrainingError('Avatar training permission changed.')
                # The durable job owns upgrade progress. Keep the verified active
                # generation available until the latest candidate is ready.
                if replacing_verified_avatar and not ready:
                    return self._profile_from_row(current)
                expected_current_job_id = (current['avatar_training_job_id']
                    if replacing_verified_avatar else expected_provider_job_id)
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
                            WHEN %s THEN CASE
                                WHEN avatar_training_job_id IS NOT DISTINCT FROM %s
                                THEN COALESCE(avatar_ready_at, NOW()) ELSE NOW() END
                            ELSE NULL
                        END,
                        runtime_verified_at = CASE
                            WHEN avatar_training_job_id IS NOT DISTINCT FROM %s AND %s
                            THEN runtime_verified_at ELSE NULL
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
                        provider_job_id,
                        provider_job_id,
                        ready,
                        error_code,
                        error_message,
                        profile_id,
                        expected_current_job_id,
                        expected_current_job_id,
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

            if _connection is None:
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
        expected_job_id: Optional[str] = None,
        expected_voice_id: Optional[str] = None,
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
                if expected_job_id is not None or expected_voice_id is not None:
                    # Serialize with erasure before taking the authorization snapshot.
                    cursor.execute(
                        "SELECT profile_id FROM digital_human_profiles WHERE profile_id = %s FOR UPDATE",
                        (profile_id,),
                    )
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
                      AND (%s::text IS NULL OR voice_training_job_id = %s::text)
                      AND (%s::text IS NULL OR voice_id = %s::text)
                      AND ((%s::text IS NULL AND %s::text IS NULL) OR NOT EXISTS (
                          SELECT 1 FROM digital_human_profile_erasure_requests AS erasure
                          WHERE erasure.profile_id = digital_human_profiles.profile_id
                      ))
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
                        expected_job_id,
                        expected_job_id,
                        expected_voice_id,
                        expected_voice_id,
                        expected_job_id,
                        expected_voice_id,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            if expected_job_id is not None or expected_voice_id is not None:
                raise StaleVoiceTrainingError("Voice training is no longer current.")
            raise DigitalHumanProfileNotFoundError(
                f"Digital human profile not found: {profile_id}"
            )

        return self._profile_from_row(row)

    @staticmethod
    def _voice_activation_snapshot(profile: Dict[str, Any]) -> Dict[str, Any]:
        fields = ('voice_provider', 'voice_id', 'voice_training_job_id', 'voice_training_status', 'voice_ready_at')
        return {key: value.isoformat() if isinstance(value, datetime) else value
                for key in fields for value in (profile[key],)}

    def _lock_voice_job(self, cursor, profile_id: UUID, job_id: UUID):
        self._lock_profile_scope(cursor, profile_id)
        cursor.execute('SELECT * FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile_id,))
        profile = cursor.fetchone()
        if profile is None:
            raise DigitalHumanProfileNotFoundError('Voice profile was not found.')
        cursor.execute("""SELECT * FROM digital_human_training_jobs
            WHERE job_id=%s AND profile_id=%s AND training_type='voice'
              AND provider='elevenlabs' FOR UPDATE""", (job_id, profile_id))
        job = cursor.fetchone()
        if job is None:
            raise StaleVoiceTrainingError('Voice job does not belong to this profile.')
        return profile, job

    @staticmethod
    def _voice_consent_matches(cursor, profile_id: UUID, revision: int) -> bool:
        from app.schemas.profile_consent import CONSENT_POLICY_VERSION
        cursor.execute('SELECT revision,policy_version,purposes FROM profile_purpose_consents WHERE profile_id=%s', (profile_id,))
        grant = cursor.fetchone()
        return bool(type(revision) is int and revision > 0 and grant
            and grant['revision'] == revision and grant['policy_version'] == CONSENT_POLICY_VERSION
            and {'voice_synthesis','provider_processing'}.issubset(grant['purposes']))

    def begin_voice_training(self, profile_id: UUID, job_id: UUID, expected_consent_revision: int) -> Dict[str, Any]:
        """Claim submission while preserving the active voice; no provider work here.

        submission_claimed is True only for the caller that changed created to
        submitted. Other callers must not replay the external create request.
        """
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                profile, job = self._lock_voice_job(cursor, profile_id, job_id)
                self._require_profile_write_allowed_with_cursor(cursor, profile_id)
                if not self._voice_consent_matches(cursor, profile_id, expected_consent_revision):
                    raise StaleVoiceTrainingError('Voice permission changed.')
                cursor.execute("""SELECT job_id FROM digital_human_training_jobs
                    WHERE profile_id=%s AND training_type='voice' AND provider='elevenlabs'
                    ORDER BY created_at DESC,job_id DESC LIMIT 1""", (profile_id,))
                if cursor.fetchone()['job_id'] != job_id:
                    raise StaleVoiceTrainingError('A newer voice request was selected.')
                payload = dict(job['request_payload'] or {})
                snapshot = self._voice_activation_snapshot(profile)
                if '_stay_voice_selection' in payload and (
                        payload['_stay_voice_selection'] != snapshot
                        or payload.get('_stay_consent_revision') != expected_consent_revision):
                    raise StaleVoiceTrainingError('The selected voice request is obsolete.')
                if job['status'] != 'created' or job['provider_job_id'] is not None:
                    return {**dict(job), 'submission_claimed': False}
                payload.update(_stay_voice_selection=snapshot, _stay_consent_revision=expected_consent_revision)
                cursor.execute("""UPDATE digital_human_training_jobs SET status='submitted',
                    request_payload=%s::jsonb, submitted_at=COALESCE(submitted_at,NOW())
                    WHERE job_id=%s RETURNING *""", (json.dumps(payload), job_id))
                return {**dict(cursor.fetchone()), 'submission_claimed': True}

    def apply_voice_training_result(self, *, profile_id: UUID, job_id: UUID, status: str,
                                    voice_id: Optional[str] = None, provider_payload: Optional[Dict[str, Any]] = None,
                                    error_code: Optional[str] = None,
                                    error_message: Optional[str] = None) -> Dict[str, Any]:
        """Store provider evidence; activate only the latest authorized selection.

        voice_activated describes profile projection, never merely provider success.
        A failed/pending replacement leaves the last active voice untouched.
        """
        if status not in {'submitted','training','verification_required','ready','failed'}:
            raise DigitalHumanProfileRepositoryError('Invalid voice result status.')
        if status in {'ready','verification_required'} and not voice_id:
            raise StaleVoiceTrainingError('Provider voice identity is missing.')
        evidence = dict(provider_payload or {})
        if evidence.get('voice_id') and evidence['voice_id'] != voice_id:
            raise StaleVoiceTrainingError('Provider voice identity does not match.')
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                profile, job = self._lock_voice_job(cursor, profile_id, job_id)
                if voice_id and job['provider_job_id'] and voice_id != job['provider_job_id']:
                    raise StaleVoiceTrainingError('Provider voice identity changed.')
                ranks = {'created':0, 'submitted':1, 'training':2, 'verification_required':3, 'ready':4, 'failed':4}
                terminal = job['status'] in {'cancelled','deleted','failed','ready'}
                accepted = (status == job['status'] if terminal else
                    status in ranks and ranks[status] >= ranks.get(job['status'], 100))
                if not accepted:
                    if voice_id and not job['provider_job_id']:
                        cursor.execute("""UPDATE digital_human_training_jobs SET provider_job_id=%s,
                            provider_payload=provider_payload || %s::jsonb WHERE job_id=%s RETURNING *""",
                            (voice_id, json.dumps(evidence), job_id))
                        job = cursor.fetchone()
                    return {**dict(job), 'voice_activated': False}
                cursor.execute("""UPDATE digital_human_training_jobs SET status=%s,
                    provider_job_id=COALESCE(%s,provider_job_id), provider_payload=provider_payload || %s::jsonb,
                    error_code=%s,error_message=%s, completed_at=CASE WHEN %s IN ('ready','failed')
                        THEN COALESCE(completed_at,NOW()) ELSE completed_at END
                    WHERE job_id=%s RETURNING *""",
                    (status,voice_id,json.dumps(evidence),error_code,error_message,status,job_id))
                job = cursor.fetchone()
                if status != 'ready':
                    return {**dict(job), 'voice_activated': False}
                payload = dict(job['request_payload'] or {})
                cursor.execute('SELECT 1 FROM digital_human_profile_erasure_requests WHERE profile_id=%s LIMIT 1', (profile_id,))
                erased = cursor.fetchone() is not None
                cursor.execute("""SELECT job_id FROM digital_human_training_jobs
                    WHERE profile_id=%s AND training_type='voice' AND provider='elevenlabs'
                    ORDER BY created_at DESC,job_id DESC LIMIT 1""", (profile_id,))
                latest = cursor.fetchone()['job_id'] == job_id
                authorized = self._voice_consent_matches(cursor, profile_id, payload.get('_stay_consent_revision'))
                already_active = (profile['voice_id'] == voice_id and profile['voice_training_job_id'] == str(job_id)
                                  and profile['voice_training_status'] == 'ready' and profile['voice_provider'] == 'elevenlabs')
                if not profile['consent_verified'] or erased or not latest or not authorized or (
                        not already_active and payload.get('_stay_voice_selection') != self._voice_activation_snapshot(profile)):
                    return {**dict(job), 'voice_activated': False}
                if not already_active:
                    # Record actual activation, not merely provider completion. A
                    # running call may retain only a voice that was really active.
                    cursor.execute("""UPDATE digital_human_training_jobs
                        SET provider_payload=provider_payload || '{"_stay_activated":true}'::jsonb
                        WHERE profile_id=%s AND training_type='voice' AND provider='elevenlabs'
                          AND status='ready' AND job_id::text=%s AND provider_job_id=%s""",
                        (profile_id, profile['voice_training_job_id'], profile['voice_id']))
                    cursor.execute("""UPDATE digital_human_profiles SET voice_provider='elevenlabs',
                        voice_id=%s, voice_training_job_id=%s, voice_training_status='ready', voice_ready_at=NOW()
                        WHERE profile_id=%s""", (voice_id,str(job_id),profile_id))
                cursor.execute("""UPDATE digital_human_training_jobs
                    SET provider_payload=provider_payload || '{"_stay_activated":true}'::jsonb
                    WHERE job_id=%s""", (job_id,))
                return {**dict(job), 'voice_activated': True}

    def get_voice_status_snapshot(self, profile_id: UUID):
        """Read active voice and latest request from the same MVCC snapshot."""
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute("""SELECT row_to_json(p) AS profile,
                    (SELECT row_to_json(j) FROM digital_human_training_jobs j
                     WHERE j.profile_id=p.profile_id AND j.training_type='voice'
                       AND j.provider='elevenlabs'
                     ORDER BY j.created_at DESC,j.job_id DESC LIMIT 1) AS latest_job
                    FROM digital_human_profiles p WHERE p.profile_id=%s""", (profile_id,))
                row = cursor.fetchone()
                return (row['profile'], row['latest_job']) if row else (None, None)

    def claim_avatar_submission(self, job_id: UUID, profile_id: UUID) -> bool:
        """Claim exactly one unattempted Tavus creation; never replay submitted work.

        Returns False for a nonmatching/already claimed job or missing profile.
        Active erasure and database failures propagate through the repository contract.
        """
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                self._lock_profile_scope(cursor, profile_id)
                cursor.execute('SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile_id,))
                if cursor.fetchone() is None:
                    return False
                self._require_profile_write_allowed_with_cursor(cursor, profile_id)
                cursor.execute("""UPDATE digital_human_training_jobs
                    SET status='submitted', submitted_at=COALESCE(submitted_at, NOW())
                    WHERE job_id=%s AND profile_id=%s AND training_type='avatar'
                      AND provider='tavus' AND provider_job_id IS NULL AND status='created'
                    RETURNING job_id""", (job_id, profile_id))
                return cursor.fetchone() is not None

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
                self._lock_profile_scope(cursor, profile_id)
                cursor.execute(
                    "SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE",
                    (profile_id,),
                )
                if cursor.fetchone() is None:
                    raise DigitalHumanProfileNotFoundError("Training profile was not found.")
                self._require_profile_write_allowed_with_cursor(cursor, profile_id)
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
        _connection=None,
    ) -> Dict[str, Any]:
        payload_json = json.dumps(
            provider_payload or {},
            separators=(",", ":"),
            sort_keys=True,
        )

        with (nullcontext(_connection) if _connection is not None else psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        )) as connection:
            with connection.cursor() as cursor:
                cursor.execute('SELECT * FROM digital_human_training_jobs WHERE job_id=%s FOR UPDATE', (job_id,))
                existing = cursor.fetchone()
                if existing is None:
                    raise DigitalHumanProfileNotFoundError('Training job was not found.')
                if existing['training_type'] == 'avatar':
                    if (provider_job_id is not None and existing['provider_job_id'] is not None
                            and existing['provider_job_id'] != provider_job_id):
                        raise StaleAvatarTrainingError('Avatar provider identity changed.')
                    identity = provider_job_id or existing['provider_job_id']
                    if existing['provider'] == 'tavus' and identity:
                        for payload in (existing['provider_payload'] or {}, provider_payload or {}):
                            for key in ('face_id', 'faceId', 'replica_id'):
                                if payload.get(key) and identity != f"tavus:{payload[key]}":
                                    raise StaleAvatarTrainingError('Avatar result identity changed.')
                    if not self._avatar_status_allows(existing['status'], status):
                        if existing['provider_job_id'] is None and provider_job_id is not None:
                            # A cancelled create can still finish remotely. Retain its
                            # identity for erasure without reviving the terminal job.
                            cursor.execute("""UPDATE digital_human_training_jobs
                                SET provider_job_id=%s, provider_payload=provider_payload || %s::jsonb
                                WHERE job_id=%s RETURNING *""", (provider_job_id, payload_json, job_id))
                            return dict(cursor.fetchone())
                        return dict(existing)
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
                            THEN CASE WHEN training_type = 'avatar'
                                THEN COALESCE(completed_at, NOW()) ELSE NOW() END
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

            if _connection is None:
                connection.commit()

        if row is None:
            raise DigitalHumanProfileRepositoryError(
                f"Training job not found: {job_id}"
            )

        return dict(row)

    @staticmethod
    def _avatar_status_allows(current: str, incoming: str) -> bool:
        if incoming == 'deleted':
            return True
        if current == 'deleted':
            return False
        if incoming == 'cancelled':
            return True
        if current == 'cancelled':
            return False
        if current in {'ready', 'failed'}:
            return incoming == current
        ranks = {'not_started': 0, 'created': 0, 'collecting': 0, 'validating': 1,
                 'submitted': 1, 'training': 2, 'ready': 3, 'failed': 3}
        return incoming in ranks and current in ranks and ranks[incoming] >= ranks[current]

    def apply_avatar_training_result(self, *, profile_id: UUID, job_id: UUID,
                                    provider: str, provider_job_id: str, status: str,
                                    replica_id: Optional[str], provider_payload: Dict[str, Any],
                                    error_code: Optional[str] = None,
                                    error_message: Optional[str] = None) -> Dict[str, Any]:
        """Atomically persist a callback/poll and project it when still authorized.

        Returns the canonical job plus profile_updated. Callers must use its status,
        not the incoming event status, when a late event was ignored.
        """
        if status not in {'submitted', 'training', 'ready', 'failed'}:
            raise DigitalHumanProfileRepositoryError('Invalid avatar provider status.')
        if provider != 'tavus' or (replica_id and provider_job_id != f'tavus:{replica_id}'):
            raise StaleAvatarTrainingError('Avatar provider identity does not match.')
        if status == 'ready' and not replica_id:
            raise StaleAvatarTrainingError('Ready avatar identity is missing.')
        for key in ('face_id', 'faceId', 'replica_id'):
            if provider_payload.get(key) and provider_job_id != f"tavus:{provider_payload[key]}":
                raise StaleAvatarTrainingError('Avatar result contains a different provider identity.')
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                self._lock_profile_scope(cursor, profile_id)
                cursor.execute('SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile_id,))
                if cursor.fetchone() is None:
                    raise DigitalHumanProfileNotFoundError('Avatar profile was not found.')
                self._require_profile_write_allowed_with_cursor(cursor, profile_id)
                cursor.execute('SELECT * FROM digital_human_training_jobs WHERE job_id=%s FOR UPDATE', (job_id,))
                job = cursor.fetchone()
                if (not job or job['profile_id'] != profile_id or job['provider'] != provider
                        or job['training_type'] != 'avatar' or job['provider_job_id'] != provider_job_id):
                    raise StaleAvatarTrainingError('Avatar job does not match the requested profile.')
                if not self._avatar_status_allows(job['status'], status):
                    return {**dict(job), 'profile_updated': False}
            job = self.update_training_job(job_id, status=status, provider_job_id=provider_job_id,
                provider_payload=provider_payload, error_code=error_code, error_message=error_message,
                _connection=connection)
            try:
                projected = self.set_avatar_training(profile_id, provider=provider, status=job['status'],
                    provider_job_id=provider_job_id, replica_id=replica_id, training_job_id=job_id,
                    expected_provider_job_id=provider_job_id, error_code=error_code,
                    error_message=error_message, _connection=connection)
            except StaleAvatarTrainingError:
                # Keep provider evidence for cleanup without reviving obsolete state.
                return {**job, 'profile_updated': False}
            return {**job, 'profile_updated': projected.avatar_training_job_id == provider_job_id}

    @staticmethod
    def voice_training_retry_allowed(job: Dict[str, Any]) -> bool:
        return bool(job.get('training_type') == 'voice' and job.get('provider') == 'elevenlabs'
            and job.get('status') == 'failed' and job.get('provider_job_id') is None
            and re.fullmatch(VOICE_RETRY_ERROR_PATTERN, job.get('error_code') or ''))

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
                self._lock_voice_job(cursor, profile_id, job_id)
                self._require_profile_write_allowed_with_cursor(cursor, profile_id)
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
                      AND error_code ~ %s
                    RETURNING *
                    """,
                    (
                        job_id,
                        profile_id,
                        VOICE_RETRY_ERROR_PATTERN,
                    ),
                )

                row = cursor.fetchone()

            connection.commit()

        return (
            dict(row)
            if row is not None
            else None
        )

    def adopt_reconciled_avatar(self, *, job_id: UUID, correlation_name: str,
                               face_id: str, provider_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Record a recovered create result without overwriting a newer job outcome."""
        if provider_payload.get('face_name') != correlation_name or provider_payload.get('face_id') != face_id:
            raise DigitalHumanProfileRepositoryError('Recovered avatar identity does not match.')
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM digital_human_training_jobs WHERE job_id=%s FOR UPDATE", (job_id,)
            ).fetchone()
            if not row or row['provider'] != 'tavus' or row['training_type'] != 'avatar':
                raise DigitalHumanProfileNotFoundError('Avatar job was not found.')
            if (row['request_payload'] or {}).get('_stay_face_name') != correlation_name:
                raise DigitalHumanProfileRepositoryError('Avatar creation identity changed.')
            if row['provider_job_id'] or row['status'] not in {'created', 'submitted'}:
                return dict(row)
            row = connection.execute("""UPDATE digital_human_training_jobs
                SET provider_job_id=%s, provider_payload=%s::jsonb, status='training',
                    submitted_at=COALESCE(submitted_at,NOW()), error_code=NULL, error_message=NULL
                WHERE job_id=%s RETURNING *""",
                (f'tavus:{face_id}', json.dumps(provider_payload), job_id)).fetchone()
            return dict(row)

    def get_avatar_training_request(self, profile_id: UUID, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Read the exact profile-owned Tavus request without inspecting its media."""
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            row = connection.execute("""SELECT * FROM digital_human_training_jobs
                WHERE profile_id=%s AND provider='tavus' AND training_type='avatar'
                  AND idempotency_key=%s""", (profile_id, idempotency_key)).fetchone()
        return dict(row) if row is not None else None

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
        *,
        expected_voice_id: Optional[str],
        expected_job_id: Optional[str],
        expected_provider: Optional[str],
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
                      AND voice_id IS NOT DISTINCT FROM %s::text
                      AND voice_training_job_id IS NOT DISTINCT FROM %s::text
                      AND voice_provider IS NOT DISTINCT FROM %s::text
                    RETURNING *
                    """,
                    (profile_id, expected_voice_id, expected_job_id, expected_provider),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise StaleVoiceTrainingError(
                "Voice changed while deletion was in progress."
            )

        return self._profile_from_row(row)

    def mark_runtime_verified(
        self,
        profile_id: UUID,
        *,
        expected_profile: DigitalHumanProfile,
    ) -> DigitalHumanProfile:
        if expected_profile.profile_id != profile_id or not expected_profile.has_runtime_avatar:
            raise StaleAvatarTrainingError('Avatar runtime binding is not ready.')
        with psycopg.connect(
            self.database_url,
            connect_timeout=10,
            row_factory=dict_row,
        ) as connection:
            with connection.cursor() as cursor:
                self._lock_profile_scope(cursor, profile_id)
                cursor.execute('SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile_id,))
                if cursor.fetchone() is None:
                    raise DigitalHumanProfileNotFoundError('Avatar profile was not found.')
                self._require_profile_write_allowed_with_cursor(cursor, profile_id)
                cursor.execute(
                    """
                    UPDATE digital_human_profiles
                    SET runtime_verified_at = NOW()
                    WHERE profile_id = %s
                      AND avatar_provider IS NOT DISTINCT FROM %s
                      AND avatar_replica_id IS NOT DISTINCT FROM %s
                      AND avatar_persona_id IS NOT DISTINCT FROM %s
                      AND avatar_training_job_id IS NOT DISTINCT FROM %s
                      AND training_version = %s
                      AND avatar_training_status = 'ready'
                      AND consent_verified = TRUE
                    RETURNING *
                    """,
                    (profile_id, expected_profile.avatar_provider, expected_profile.avatar_replica_id,
                     expected_profile.avatar_persona_id, expected_profile.avatar_training_job_id,
                     expected_profile.training_version),
                )

                row = cursor.fetchone()

            connection.commit()

        if row is None:
            raise StaleAvatarTrainingError('Avatar material changed during runtime verification.')

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
                        WHERE digital_human_profile_erasure_requests.profile_id = EXCLUDED.profile_id
                          AND digital_human_profile_erasure_requests.status <> 'completed'
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

                if row is None or row["profile_id"] is None or UUID(str(row["profile_id"])) != profile_id:
                    raise DigitalHumanProfileRepositoryError(
                        "Could not create a profile-bound erasure request."
                    )
                cursor.execute(
                    """
                    UPDATE memory_index_generations
                    SET generation = generation + 1,
                        operation_id = %s::uuid,
                        updated_at = NOW()
                    WHERE profile_id = %s::uuid
                    """,
                    (row["request_id"], profile_id),
                )

            connection.commit()

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
                # Fence both new generation reservations and already-running publishers.
                cursor.execute(
                    "SELECT profile_id FROM digital_human_profiles WHERE profile_id = %s FOR UPDATE",
                    (profile_id,),
                )
                cursor.execute(
                    "SELECT profile_id FROM memory_index_generations WHERE profile_id = %s FOR UPDATE",
                    (profile_id,),
                )
                cursor.execute(
                    "DELETE FROM memory_embeddings WHERE lower(profile_id) = lower(%s::text)",
                    (str(profile_id),),
                )
                deleted_memories = cursor.rowcount
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
            "memory_embeddings": deleted_memories,
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
