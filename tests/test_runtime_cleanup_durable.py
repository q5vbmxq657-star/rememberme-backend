import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest

from app.services.runtime_cleanup_repository import RuntimeCleanupRepository
from app.services.runtime_cleanup_service import RuntimeCleanupService


@pytest.mark.parametrize('scenario', ['success', 'provider_failure', 'unknown', 'transport_failure'])
def test_recovery_never_acknowledges_unverified_cleanup(scenario):
    row = dict(session_id='test', room_name='room', dispatch_id='dispatch',
               conversation_id=None if scenario == 'unknown' else 'conversation',
               worker_started=True, provider_create_started=True, conversation_name=None,
               tavus_ended=False, conversation_deleted=False)
    repo = Mock()
    repo.claim.return_value = row
    repo.get.return_value = row
    adapter = SimpleNamespace(_delete_remote_resources=AsyncMock())
    provider = SimpleNamespace(end_tavus_conversation=AsyncMock(), delete_tavus_conversation=AsyncMock())
    if scenario == 'provider_failure':
        provider.end_tavus_conversation.side_effect = TimeoutError()
    if scenario == 'transport_failure':
        adapter._delete_remote_resources.side_effect = TimeoutError()
    service = RuntimeCleanupService(repo, adapter, provider)
    assert asyncio.run(service.recover_once())
    repo.finish.assert_called_once_with(row, scenario == 'success')
    if scenario in {'success', 'transport_failure'}:
        repo.ended.assert_called_once_with('test', 'conversation')
    else:
        repo.ended.assert_not_called()
    if scenario == 'unknown':
        provider.end_tavus_conversation.assert_not_called()


@pytest.fixture
def registry():
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated local AP1 database')
    assert conninfo_to_dict(url).get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    namespace = 'runtime_test_' + uuid4().hex
    with psycopg.connect(url) as connection:
        connection.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(namespace)))
    scoped = make_conninfo(url, options='-c search_path=' + namespace)
    try:
        with psycopg.connect(scoped) as connection:
            connection.execute('CREATE TABLE schema_migrations(version text)')
            connection.execute('CREATE TABLE profile_purpose_consents(profile_id uuid, revision bigint, purposes text[])')
            connection.execute('CREATE TABLE digital_human_profile_erasure_requests(profile_id uuid)')
            connection.execute(Path('migrations/021_runtime_session_registry.sql').read_text())
            connection.execute(Path('migrations/025_runtime_creation_correlation.sql').read_text())
            connection.execute(Path('migrations/029_runtime_conversation_deletion.sql').read_text())
        repository = RuntimeCleanupRepository(scoped)
        profile = uuid4()
        repository._execute('INSERT INTO profile_purpose_consents VALUES (%s,1,%s)',
                            (profile, ['photo_likeness', 'provider_processing']))
        repository.register('test', profile, 'room', datetime.now(timezone.utc)+timedelta(hours=1),
                            1, {'photo_likeness'})
        yield repository, profile
    finally:
        with psycopg.connect(url) as connection:
            connection.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(namespace)))


def test_restart_recovers_pending_and_preserves_unknown_conversation(registry):
    repo, profile = registry
    repo.begin_worker('test', profile, 'room')
    repo.begin_provider_create('test')
    repo.request('test')
    restarted = RuntimeCleanupRepository(repo.database_url)
    row = restarted.claim()
    assert row['session_id'] == 'test'
    assert restarted.claim() is None
    restarted.finish(row, True)
    assert restarted.get('test')['completed_at'] is None
    restarted.conversation('test', 'conversation')
    restarted.ended('test', 'conversation')
    restarted.deleted('test', 'conversation')
    restarted.request('test')
    row = restarted.claim()
    restarted.finish(row, True)
    assert restarted.get('test')['completed_at'] is not None


@pytest.mark.parametrize('trigger', ['revision', 'erasure', 'expiry', 'explicit'])
def test_recovery_claims_revoked_erased_expired_and_requested_sessions(registry, trigger):
    repo, profile = registry
    assert repo.claim() is None
    if trigger == 'revision':
        repo._execute('UPDATE profile_purpose_consents SET revision=2')
    elif trigger == 'erasure':
        repo._execute('INSERT INTO digital_human_profile_erasure_requests VALUES (%s)', (profile,))
    elif trigger == 'expiry':
        repo._execute("UPDATE avatar_runtime_cleanup SET expires_at=NOW()-INTERVAL '1 second'")
    else:
        repo.request_profile(profile)
    assert repo.claim()['session_id'] == 'test'
    with pytest.raises(RuntimeError):
        repo.begin_worker('test', profile, 'room')


def test_expired_lease_is_reclaimed_and_old_worker_cannot_finish(registry):
    repo, _ = registry
    repo.request('test')
    old = repo.claim()
    repo._execute("UPDATE avatar_runtime_cleanup SET lease_until=NOW()-INTERVAL '1 second'")
    new = repo.claim()
    assert old['lease_token'] != new['lease_token']
    repo.finish(old, True)
    assert repo.get('test')['completed_at'] is None
    repo.finish(new, True)
    assert repo.get('test')['completed_at'] is not None


def test_audio_authorization_uses_original_purpose_revision(registry, monkeypatch):
    repo, profile = registry
    guard = Mock()
    monkeypatch.setattr('app.security.purpose_authorization.require_profile_purposes', guard)
    repo.authorize('test')
    guard.assert_called_once_with(profile, {'photo_likeness'}, expected_revision=1)
    guard.side_effect = RuntimeError('revoked')
    with pytest.raises(RuntimeError):
        repo.authorize('test')


@pytest.mark.parametrize('cause', ['erasure', 'expiry', 'closing'])
def test_audio_is_blocked_before_purpose_lookup_when_session_invalid(registry, monkeypatch, cause):
    repo, profile = registry
    guard = Mock()
    monkeypatch.setattr('app.security.purpose_authorization.require_profile_purposes', guard)
    if cause == 'erasure':
        repo._execute('INSERT INTO digital_human_profile_erasure_requests VALUES (%s)', (profile,))
    elif cause == 'expiry':
        repo._execute("UPDATE avatar_runtime_cleanup SET expires_at=NOW()-INTERVAL '1 second'")
    else:
        repo.request('test')
    with pytest.raises(RuntimeError):
        repo.authorize('test')
    guard.assert_not_called()
