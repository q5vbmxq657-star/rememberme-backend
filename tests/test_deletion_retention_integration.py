import os
import asyncio
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.services.deletion_retention_service import DeletionRetentionService
from app.services.digital_human_profile_repository import DigitalHumanProfileRepository
from app.services.profile_erasure_service import ProfileErasureService, ProfileErasureServiceError


@pytest.fixture
def database_url():
    url = os.getenv("STAY_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Requires isolated AP1 PostgreSQL")
    config = conninfo_to_dict(url)
    assert config.get("host", "").startswith("/private/tmp/STAY-AP1-PG-")
    assert config.get("dbname") == "stay_ap1"
    return url


@pytest.mark.parametrize("table,column,kind", [
    ("digital_human_profiles", "profile_id", "profile"),
    ("users", "user_id", "account"),
])
def test_deleted_subject_cannot_be_recreated(database_url, table, column, kind):
    identifier = uuid4()
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(f"INSERT INTO {table} ({column}) VALUES (%s)", (identifier,))
        connection.execute(f"DELETE FROM {table} WHERE {column} = %s", (identifier,))
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(f"INSERT INTO {table} ({column}) VALUES (%s)", (identifier,))
        row = connection.execute("""
            SELECT octet_length(subject_digest), backup_delete_by - deleted_at
            FROM deletion_tombstones WHERE subject_kind = %s AND subject_digest =
                sha256(convert_to(%s, 'UTF8'))
        """, (kind, f"{kind}:{identifier}")).fetchone()
        assert row[0] == 32
        assert row[1].days == 30


def test_restore_gate_rejects_replayed_deletion(database_url):
    identifier = uuid4()
    with psycopg.connect(database_url) as connection:
        connection.execute("INSERT INTO users(user_id) VALUES (%s)", (identifier,))
        connection.execute("""INSERT INTO deletion_tombstones(subject_kind, subject_digest)
            VALUES ('account', sha256(convert_to(%s, 'UTF8')))""", (f"account:{identifier}",))
    try:
        with pytest.raises(RuntimeError, match="traffic must remain disabled"):
            DeletionRetentionService(database_url=database_url).assert_restore_clean()
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute("DELETE FROM users WHERE user_id = %s", (identifier,))


def test_completed_request_removes_provider_and_asset_identifiers(database_url):
    request = uuid4()
    with psycopg.connect(database_url) as connection:
        connection.execute("""INSERT INTO digital_human_profile_erasure_requests
            (request_id, idempotency_key, status, provider_snapshot, storage_asset_ids,
             error_code, error_message)
            VALUES (%s, %s, 'completed', '{"voice_id":"sensitive"}', '["private-path"]',
                    'error', 'private diagnostic')""", (request, str(request)))
        row = connection.execute("""SELECT provider_snapshot, storage_asset_ids,
            error_code, error_message FROM digital_human_profile_erasure_requests
            WHERE request_id = %s""", (request,)).fetchone()
        assert row == ({}, [], None, None)


def test_recovery_finds_unfinished_stages(database_url):
    request = uuid4()
    with psycopg.connect(database_url) as connection:
        connection.execute("""INSERT INTO digital_human_profile_erasure_requests
            (request_id, idempotency_key, status) VALUES (%s, %s, 'database_cleanup')""",
            (request, str(request)))
    try:
        rows = DeletionRetentionService(database_url=database_url).pending_requests(limit=1000)
        assert any(row['request_id'] == request for row in rows)
    finally:
        with psycopg.connect(database_url) as connection:
            connection.execute("DELETE FROM digital_human_profile_erasure_requests WHERE request_id = %s", (request,))


def test_recovery_finishes_after_graph_commit_without_repeating_provider_calls(database_url):
    repository = DigitalHumanProfileRepository(database_url=database_url)
    request = uuid4()
    with psycopg.connect(database_url) as connection:
        connection.execute("""INSERT INTO digital_human_profile_erasure_requests
            (request_id, idempotency_key, status) VALUES (%s, %s, 'database_cleanup')""",
            (request, str(request)))
    provider, voice = AsyncMock(), AsyncMock()
    service = ProfileErasureService(repository=repository, avatar_provider=provider,
                                   voice_service=voice, media_storage=Mock())
    asyncio.run(service._run(repository.get_profile_erasure_request(request_id=request)))
    assert repository.get_profile_erasure_request(request_id=request)['status'] == 'completed'
    provider.delete_tavus_identity.assert_not_called()
    voice.delete_profile_voice.assert_not_called()


def test_recovery_does_not_execute_a_claimed_request(database_url):
    repository = DigitalHumanProfileRepository(database_url=database_url)
    request = uuid4()
    service = ProfileErasureService(repository=repository, avatar_provider=AsyncMock(),
                                   voice_service=AsyncMock(), media_storage=Mock())
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtextextended(%s, 22))", (str(request),))
        with pytest.raises(ProfileErasureServiceError, match='already running'):
            asyncio.run(service._run({'request_id': request}))
