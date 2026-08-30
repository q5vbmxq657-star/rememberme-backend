from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from app.schemas.podcast import PodcastInvitationRecord, PodcastInvitationStatus


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
                        prompt_audio_asset_id, memory_id, expires_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        invitation_id, profile_id, created_by_user_id,
                        token_digest, requester_name, subject_name, prompt,
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

    @staticmethod
    def _record(row: dict[str, Any]) -> PodcastInvitationRecord:
        return PodcastInvitationRecord.model_validate(row)
