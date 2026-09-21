"""Operational restore gate; never asserts that external backups were deleted."""
from __future__ import annotations

import psycopg


class DeletionRetentionService:
    def __init__(self, *, database_url: str) -> None:
        self.database_url = database_url

    def assert_restore_clean(self) -> None:
        """Run after importing the CURRENT deletion ledger, before enabling traffic.

        A historical backup's own ledger is insufficient. Independent ledger
        provenance/freshness is not integrated yet, so even a consistent database
        cannot pass this gate. No caller-supplied flag can override this decision.
        """
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            row = connection.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM digital_human_profiles p JOIN deletion_tombstones t
                    ON t.subject_kind = 'profile' AND t.subject_digest =
                       sha256(convert_to('profile:' || p.profile_id::text, 'UTF8'))
                    UNION ALL
                    SELECT 1 FROM users u JOIN deletion_tombstones t
                    ON t.subject_kind = 'account' AND t.subject_digest =
                       sha256(convert_to('account:' || u.user_id::text, 'UTF8'))
                )
            """).fetchone()
        if row[0]:
            raise RuntimeError("Restore contains deleted subjects; traffic must remain disabled.")
        raise RuntimeError(
            "Current independent deletion ledger cannot be verified; traffic must remain disabled."
        )

    def pending_requests(self, *, limit: int = 100) -> list[dict]:
        from psycopg.rows import dict_row

        if not 1 <= limit <= 1000:
            raise ValueError("Recovery batch must contain between 1 and 1000 requests.")
        with psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row) as connection:
            return connection.execute("""
                SELECT * FROM digital_human_profile_erasure_requests
                WHERE status <> 'completed'
                  AND (next_retry_at IS NULL OR next_retry_at <= NOW())
                ORDER BY requested_at, request_id LIMIT %s
            """, (limit,)).fetchall()

    def purge_completed_cleanup_records(self) -> int:
        with psycopg.connect(self.database_url, connect_timeout=10) as connection:
            deleted = connection.execute("""DELETE FROM avatar_runtime_cleanup
                WHERE completed_at IS NOT NULL
                  AND completed_at <= NOW() - INTERVAL '30 days'""").rowcount
            connection.execute("""UPDATE openai_realtime_calls AS call SET metadata = '{}'::jsonb
                WHERE metadata <> '{}'::jsonb AND (
                    create_started_at IS NOT NULL OR hangup_requested_at IS NOT NULL
                    OR state IN ('cancelled', 'hangup_acknowledged')
                    OR EXISTS (SELECT 1 FROM digital_human_profile_erasure_requests AS erasure
                               WHERE erasure.profile_id = call.profile_id))""")
            # Hangup acknowledgement is not terminal-state proof in the registry.
            # Keep those handles until confirmed termination can be persisted.
            deleted += connection.execute("""DELETE FROM openai_realtime_calls
                WHERE state = 'cancelled' AND create_started_at IS NULL AND call_id IS NULL
                  AND hangup_requested_at <= NOW() - INTERVAL '30 days'""").rowcount
            return deleted
