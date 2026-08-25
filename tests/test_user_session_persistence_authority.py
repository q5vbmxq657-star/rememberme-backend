from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_migration_012_has_canonical_session_contract():
    source = (
        ROOT
        / "migrations/012_user_sessions.sql"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "CREATE TABLE IF NOT EXISTS user_sessions"
        in source
    )

    assert (
        "REFERENCES users(user_id)"
        in source
    )

    assert (
        "refresh_token_hash TEXT NOT NULL UNIQUE"
        in source
    )

    assert (
        "revoked_at TIMESTAMPTZ"
        in source
    )

    assert (
        "refresh_expires_at TIMESTAMPTZ NOT NULL"
        in source
    )

    assert (
        source.count(
            "INSERT INTO schema_migrations"
        )
        == 1
    )

    assert (
        source.count(
            "'012_user_sessions'"
        )
        == 1
    )

    assert "BEGIN;" not in source
    assert "COMMIT;" not in source


def test_refresh_token_plaintext_column_is_absent():
    source = (
        ROOT
        / "migrations/012_user_sessions.sql"
    ).read_text(
        encoding="utf-8"
    )

    assert "\n    refresh_token TEXT" not in source
    assert "\n    refresh_token VARCHAR" not in source
