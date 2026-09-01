from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.schemas.podcast import (
    PodcastInvitationRecord,
    PodcastInvitationStatus,
    PodcastResponseRecord,
)


class PodcastRepositoryError(RuntimeError):
    pass


class PodcastInvitationNotFound(PodcastRepositoryError):
    pass


class PodcastRepository:
    def __init__(self, database_url: str | None = None) -> None:
        self.database_url = (database_url or os.getenv("DATABASE_URL") or "").strip()
        if not self.database_url:
            raise PodcastRepositoryError("DATABASE_URL is missing.")

    def create(
        self,
        *,
        invitation_id: UUID,
        profile_id: UUID,
        created_by_user_id: UUID,
        token_digest: str,
        requester_name: str,
        subject_name: str,
        prompt: str,
        theme: str,
        prompt_sequence: list[dict[str, Any]],
        memory_id: UUID,
        expires_at: datetime,
        prompt_audio_asset_id: UUID | None,
    ) -> PodcastInvitationRecord:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO podcast_invitations (
                        invitation_id, profile_id, created_by_user_id,
                        token_digest, requester_name, subject_name, prompt,
                        theme, prompt_sequence,
                        prompt_audio_asset_id, memory_id, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        invitation_id, profile_id, created_by_user_id,
                        token_digest, requester_name, subject_name, prompt,
                        theme, psycopg.types.json.Jsonb(prompt_sequence),
                        prompt_audio_asset_id, memory_id, expires_at,
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise PodcastRepositoryError("Invitation could not be created.")
        return self._record(row)

    def get_by_token_digest(self, token_digest: str, *, for_update: bool = False) -> PodcastInvitationRecord:
        suffix = " FOR UPDATE" if for_update else ""
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT * FROM podcast_invitations WHERE token_digest = %s" + suffix,
                    (token_digest,),
                )
                row = cursor.fetchone()
        if row is None:
            raise PodcastInvitationNotFound("Invitation not found.")
        return self._record(row)

    def mark_status(
        self,
        *,
        invitation_id: UUID,
        expected_statuses: tuple[PodcastInvitationStatus, ...],
        status: PodcastInvitationStatus,
        response_audio_asset_id: UUID | None = None,
        safe_error_code: str | None = None,
    ) -> PodcastInvitationRecord:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE podcast_invitations
                    SET status = %s,
                        response_audio_asset_id = COALESCE(%s, response_audio_asset_id),
                        safe_error_code = %s
                    WHERE invitation_id = %s AND status = ANY(%s)
                    RETURNING *
                    """,
                    (
                        status.value,
                        response_audio_asset_id,
                        safe_error_code,
                        invitation_id,
                        [value.value for value in expected_statuses],
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise PodcastRepositoryError("Invitation state changed before this operation completed.")
        return self._record(row)

    def claim_recording_upload(
        self,
        *,
        invitation_id: UUID,
    ) -> PodcastInvitationRecord:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE podcast_invitations
                    SET status = 'recording', safe_error_code = NULL
                    WHERE invitation_id = %s
                      AND (
                        status IN ('pending', 'retryable_failed')
                        OR (
                            status = 'recording'
                            AND updated_at < NOW() - INTERVAL '15 minutes'
                        )
                      )
                    RETURNING *
                    """,
                    (invitation_id,),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise PodcastRepositoryError("Invitation is already receiving an answer.")
        return self._record(row)

    def complete(
        self,
        *,
        invitation_id: UUID,
        transcript: str,
        summary: str,
        memory_payload: dict[str, Any],
    ) -> PodcastInvitationRecord:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE podcast_invitations
                    SET status = 'completed', transcript = %s, summary = %s,
                        memory_payload = %s::jsonb, safe_error_code = NULL,
                        completed_at = NOW()
                    WHERE invitation_id = %s AND status = 'processing'
                    RETURNING *
                    """,
                    (transcript, summary, psycopg.types.json.Jsonb(memory_payload), invitation_id),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise PodcastRepositoryError("Invitation could not be completed.")
        return self._record(row)

    def complete_session(
        self,
        *,
        invitation_id: UUID,
        responses: list[PodcastResponseRecord],
        speaker_confirmed_subject: bool,
        voice_training_consent_granted: bool,
    ) -> PodcastInvitationRecord:
        if not responses:
            raise PodcastRepositoryError("A podcast session requires at least one response.")
        consent_granted = speaker_confirmed_subject and voice_training_consent_granted
        first = responses[0]
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status FROM podcast_invitations WHERE invitation_id = %s FOR UPDATE",
                    (invitation_id,),
                )
                current = cursor.fetchone()
                if current is None:
                    raise PodcastInvitationNotFound("Invitation not found.")
                if current["status"] != PodcastInvitationStatus.processing.value:
                    raise PodcastRepositoryError("Invitation is not ready to complete.")

                cursor.execute(
                    "DELETE FROM podcast_responses WHERE invitation_id = %s",
                    (invitation_id,),
                )
                for response in responses:
                    cursor.execute(
                        """
                        INSERT INTO podcast_responses (
                            response_id, invitation_id, turn_index, prompt_id,
                            category, question, audio_asset_id, memory_id,
                            transcript, summary, memory_payload
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                        """,
                        (
                            response.response_id,
                            response.invitation_id,
                            response.turn_index,
                            response.prompt_id,
                            response.category,
                            response.question,
                            response.audio_asset_id,
                            response.memory_id,
                            response.transcript,
                            response.summary,
                            psycopg.types.json.Jsonb(response.memory_payload),
                        ),
                    )

                cursor.execute(
                    """
                    UPDATE podcast_invitations
                    SET status = 'completed',
                        response_audio_asset_id = %s,
                        memory_id = %s,
                        transcript = %s,
                        summary = %s,
                        memory_payload = %s::jsonb,
                        speaker_confirmed_subject = %s,
                        voice_training_consent_granted = %s,
                        voice_training_consented_at = CASE WHEN %s THEN NOW() ELSE NULL END,
                        safe_error_code = NULL,
                        completed_at = NOW()
                    WHERE invitation_id = %s AND status = 'processing'
                    RETURNING *
                    """,
                    (
                        first.audio_asset_id,
                        first.memory_id,
                        first.transcript,
                        first.summary,
                        psycopg.types.json.Jsonb(first.memory_payload),
                        speaker_confirmed_subject,
                        consent_granted,
                        consent_granted,
                        invitation_id,
                    ),
                )
                row = cursor.fetchone()
            connection.commit()
        if row is None:
            raise PodcastRepositoryError("Podcast session could not be completed.")
        return self._record(row)

    def list_completed_responses(
        self,
        *,
        profile_id: UUID,
        limit: int = 150,
    ) -> list[tuple[PodcastInvitationRecord, PodcastResponseRecord]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        to_jsonb(i) AS invitation_record,
                        to_jsonb(r) AS response_record
                    FROM podcast_invitations i
                    JOIN podcast_responses r ON r.invitation_id = i.invitation_id
                    WHERE i.profile_id = %s AND i.status = 'completed'
                    ORDER BY i.completed_at DESC, r.turn_index ASC
                    LIMIT %s
                    """,
                    (profile_id, min(max(limit, 1), 300)),
                )
                rows = cursor.fetchall()

        results: list[tuple[PodcastInvitationRecord, PodcastResponseRecord]] = []
        for row in rows:
            results.append((
                self._record(row["invitation_record"]),
                PodcastResponseRecord.model_validate(row["response_record"]),
            ))
        return results

    def mark_voice_training_used(
        self,
        *,
        invitation_id: UUID,
        profile_id: UUID,
    ) -> bool:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE podcast_invitations
                    SET voice_training_used_at = COALESCE(voice_training_used_at, NOW())
                    WHERE invitation_id = %s
                      AND profile_id = %s
                      AND status = 'completed'
                      AND speaker_confirmed_subject = TRUE
                      AND voice_training_consent_granted = TRUE
                    RETURNING invitation_id
                    """,
                    (invitation_id, profile_id),
                )
                row = cursor.fetchone()
            connection.commit()
        return row is not None

    def list_completed(self, *, profile_id: UUID, limit: int = 50) -> list[PodcastInvitationRecord]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT * FROM podcast_invitations
                    WHERE profile_id = %s AND status = 'completed'
                    ORDER BY completed_at DESC
                    LIMIT %s
                    """,
                    (profile_id, min(max(limit, 1), 100)),
                )
                rows = cursor.fetchall()
        return [self._record(row) for row in rows]

    def list_recent(
        self,
        *,
        profile_id: UUID,
        limit: int = 20,
    ) -> list[tuple[PodcastInvitationRecord, int]]:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT to_jsonb(i) AS invitation_record,
                           COUNT(r.response_id)::integer AS answer_count
                    FROM podcast_invitations i
                    LEFT JOIN podcast_responses r ON r.invitation_id = i.invitation_id
                    WHERE i.profile_id = %s
                    GROUP BY i.invitation_id
                    ORDER BY i.created_at DESC
                    LIMIT %s
                    """,
                    (profile_id, min(max(limit, 1), 50)),
                )
                rows = cursor.fetchall()
        return [
            (self._record(row["invitation_record"]), int(row["answer_count"]))
            for row in rows
        ]

    @staticmethod
    def _record(row: dict[str, Any]) -> PodcastInvitationRecord:
        return PodcastInvitationRecord.model_validate(row)
