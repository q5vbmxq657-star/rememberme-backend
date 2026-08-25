from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_migration_011_owns_identity_contract():
    source = (
        ROOT
        / "migrations"
        / "011_user_identity_profile_memberships.sql"
    ).read_text(encoding="utf-8")

    required = (
        "CREATE TABLE IF NOT EXISTS users",
        "CREATE TABLE IF NOT EXISTS external_user_identities",
        "CREATE TABLE IF NOT EXISTS profile_memberships",
        "UNIQUE (\n            provider,\n            provider_subject",
        "UNIQUE (\n            user_id,\n            profile_id",
        "REFERENCES users(user_id)",
        "REFERENCES digital_human_profiles(profile_id)",
        "ON DELETE CASCADE",
    )

    for token in required:
        assert token in source


def test_release_one_membership_contract_is_fail_closed():
    source = (
        ROOT
        / "migrations"
        / "011_user_identity_profile_memberships.sql"
    ).read_text(encoding="utf-8")

    assert "'owner'" in source
    assert "'active'" in source
    assert "'inactive'" in source
    assert "'revoked'" in source


def test_no_historical_profile_owner_backfill():
    source = (
        ROOT
        / "migrations"
        / "011_user_identity_profile_memberships.sql"
    ).read_text(encoding="utf-8")

    assert "INSERT INTO profile_memberships" not in source
    assert "SELECT profile_id FROM digital_human_profiles" not in source

def test_migration_011_records_own_version_exactly_once():
    source = (
        ROOT
        / "migrations"
        / "011_user_identity_profile_memberships.sql"
    ).read_text(encoding="utf-8")

    assert (
        source.count(
            "INSERT INTO schema_migrations"
        )
        == 1
    )

    assert (
        source.count(
            "'011_user_identity_profile_memberships'"
        )
        == 1
    )

    assert "BEGIN;" not in source
    assert "COMMIT;" not in source

