import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
import pytest
from fastapi import HTTPException

from app.services.openai_realtime_registry import OpenAIRealtimeRegistry, RealtimeStateConflict
from app.services.openai_realtime_cleanup_service import OpenAIRealtimeCleanupService
from app.services.openai_realtime_service import OpenAIRealtimeService
from app.routes import realtime


@pytest.fixture
def registry():
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated local AP1 database')
    assert conninfo_to_dict(url).get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    namespace = 'openai_test_' + uuid4().hex
    with psycopg.connect(url) as db:
        db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(namespace)))
    scoped = make_conninfo(url, options='-c search_path=' + namespace)
    try:
        with psycopg.connect(scoped) as db:
            db.execute('CREATE TABLE schema_migrations(version text)')
            db.execute('CREATE TABLE profile_purpose_consents(profile_id uuid,revision bigint,policy_version text,purposes text[])')
            db.execute('CREATE TABLE digital_human_profile_erasure_requests(profile_id uuid)')
            db.execute('CREATE TABLE users(user_id uuid,status text)')
            db.execute('CREATE TABLE profile_memberships(profile_id uuid,user_id uuid,status text)')
            db.execute('''CREATE TABLE user_sessions(session_id uuid,user_id uuid,revoked_at timestamptz,
                access_expires_at timestamptz,refresh_expires_at timestamptz)''')
            db.execute('CREATE TABLE memory_index_generations(profile_id uuid,generation bigint,published_generation bigint,operation_id uuid)')
            db.execute(Path('migrations/026_openai_realtime_calls.sql').read_text())
        repo = OpenAIRealtimeRegistry(scoped)
        profile, user, auth = uuid4(), uuid4(), uuid4()
        repo.execute("INSERT INTO users VALUES (%s,'active')", (user,))
        repo.execute("INSERT INTO profile_memberships VALUES (%s,%s,'active')", (profile, user))
        repo.execute("INSERT INTO user_sessions VALUES (%s,%s,NULL,NOW()+INTERVAL '1 hour',NOW()+INTERVAL '2 hours')", (auth,user))
        repo.execute("INSERT INTO profile_purpose_consents VALUES (%s,1,'avatar-consent-v1',%s)",
            (profile,['memory_context','provider_processing']))
        row = repo.reserve(profile_id=profile,user_id=user,auth_session_id=auth,purpose_revision=1,
            model='test',voice='test',metadata={'language':'en'})
        yield repo, row
    finally:
        with psycopg.connect(url) as db:
            db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(namespace)))


def get(repo, row):
    return repo.execute('SELECT * FROM openai_realtime_calls WHERE session_id=%s', (row['session_id'],))


@pytest.mark.parametrize('trigger', ['consent','policy','memory','erasure','logout','user','membership','expiry'])
def test_sweep_detects_cross_device_revocation(registry, trigger):
    repo,row = registry
    repo.begin(row['session_id'], None)
    repo.register(row['session_id'],'rtc_test')
    repo.sweep()
    assert repo.claim() is None
    mutations = {
        'consent': 'UPDATE profile_purpose_consents SET revision=2',
        'policy': "UPDATE profile_purpose_consents SET policy_version='obsolete'",
        'memory': 'INSERT INTO memory_index_generations VALUES (%s,1,1,%s)',
        'erasure': 'INSERT INTO digital_human_profile_erasure_requests VALUES (%s)',
        'logout': 'UPDATE user_sessions SET revoked_at=NOW()',
        'user': "UPDATE users SET status='deleted'",
        'membership': "UPDATE profile_memberships SET status='revoked'",
        'expiry': "UPDATE openai_realtime_calls SET expires_at=NOW()-INTERVAL '1 second'",
    }
    args = (row['profile_id'],uuid4()) if trigger=='memory' else ((row['profile_id'],) if trigger=='erasure' else ())
    repo.execute(mutations[trigger],args)
    restarted = OpenAIRealtimeRegistry(repo.database_url)
    restarted.sweep()
    claimed = restarted.claim()
    assert claimed['call_id']=='rtc_test'
    assert restarted.claim() is None
    restarted.finish(claimed, True)
    result = get(repo,row)
    assert result['state']=='hangup_acknowledged'
    assert result['hangup_acknowledged_at'] is not None
    assert 'closed_at' not in result


def test_unknown_create_is_never_retried_or_marked_complete(registry):
    repo,row=registry
    repo.begin(row['session_id'],None)
    repo.unknown(row['session_id'])
    with pytest.raises(RealtimeStateConflict):
        repo.begin(row['session_id'],None)
    repo.sweep()
    assert repo.claim() is None
    assert get(repo,row)['state']=='creation_unknown'
    assert get(repo,row)['hangup_acknowledged_at'] is None


def test_cancel_during_create_preserves_late_handle(registry):
    repo,row=registry
    repo.begin(row['session_id'],None)
    repo.request(row['session_id'])
    registered=repo.register(row['session_id'],'rtc_late')
    assert registered['state']=='hangup_requested'
    assert repo.claim()['call_id']=='rtc_late'


def test_expired_lease_cannot_acknowledge_new_worker(registry):
    repo,row=registry
    repo.begin(row['session_id'],None)
    repo.register(row['session_id'],'rtc_test')
    repo.request(row['session_id'])
    old=repo.claim()
    repo.execute("UPDATE openai_realtime_calls SET lease_until=NOW()-INTERVAL '1 second'")
    new=repo.claim()
    repo.finish(old,True)
    assert get(repo,row)['hangup_acknowledged_at'] is None
    repo.finish(new,True)
    assert get(repo,row)['hangup_acknowledged_at'] is not None


def test_late_worker_failure_cannot_undo_immediate_provider_ack(registry):
    repo,row=registry
    repo.begin(row['session_id'],None)
    repo.register(row['session_id'],'rtc_test')
    repo.request(row['session_id'])
    claimed=repo.claim()
    repo.acknowledge(row['session_id'],'rtc_other')
    assert get(repo,row)['hangup_acknowledged_at'] is None
    repo.acknowledge(row['session_id'],'rtc_test')
    repo.finish(claimed,False)
    assert get(repo,row)['state']=='hangup_acknowledged'
    assert repo.claim() is None


def test_owner_binding_and_cancel_before_external_creation(registry):
    repo,row=registry
    principal=SimpleNamespace(user=SimpleNamespace(user_id=row['user_id']),session_id=row['auth_session_id'])
    assert repo.owned(row['session_id'],principal)
    principal.session_id=uuid4()
    assert repo.owned(row['session_id'],principal) is None
    assert repo.request(row['session_id'])['state']=='cancelled'
    with pytest.raises(RealtimeStateConflict):
        repo.begin(row['session_id'],None)
    assert repo.claim() is None


@pytest.mark.parametrize('failure',[False,True])
def test_cleanup_ack_only_on_success(failure):
    repo=Mock()
    row={'call_id':'rtc_test'}
    repo.claim.return_value=row
    provider=SimpleNamespace(hangup_call=AsyncMock(side_effect=TimeoutError() if failure else None))
    assert asyncio.run(OpenAIRealtimeCleanupService(repo,provider).recover_once())
    repo.finish.assert_called_once_with(row,not failure)


@pytest.mark.parametrize('location', ['/v1/realtime/calls/rtc_test','https://api.openai.com/v1/realtime/calls/rtc_test'])
def test_provider_exchange_uses_server_key_and_returns_handle(monkeypatch,location):
    service=OpenAIRealtimeService()
    service.api_key='test-only'
    post=AsyncMock(return_value=httpx.Response(201,headers={'Location':location},text='v=0\r\n'))
    client=AsyncMock()
    client.__aenter__.return_value=SimpleNamespace(post=post)
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client)
    call,answer=asyncio.run(service.create_call(offer_sdp='v=0',model='test',voice='test',instructions='synthetic'))
    assert call=='rtc_test' and answer.startswith('v=0')
    assert post.call_args.args[0].endswith('/realtime/calls')
    assert set(post.call_args.kwargs['files'])=={'sdp','session'}


@pytest.mark.parametrize('location',['','https://evil.example/v1/realtime/calls/rtc_test','/v1/realtime/calls/rtc_test?x=1'])
def test_untrusted_or_missing_handle_fails(monkeypatch,location):
    service=OpenAIRealtimeService(); service.api_key='test-only'
    client=AsyncMock()
    client.__aenter__.return_value=SimpleNamespace(post=AsyncMock(return_value=httpx.Response(201,headers={'Location':location},text='v=0')))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client)
    with pytest.raises(RuntimeError):
        asyncio.run(service.create_call(offer_sdp='v=0',model='test',voice='test',instructions='synthetic'))


@pytest.mark.parametrize('status',[200,401,404,429,500])
def test_hangup_only_accepts_documented_ack(monkeypatch,status):
    service=OpenAIRealtimeService(); service.api_key='test-only'
    client=AsyncMock()
    client.__aenter__.return_value=SimpleNamespace(post=AsyncMock(return_value=httpx.Response(status)))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client)
    if status==200:
        asyncio.run(service.hangup_call('rtc_test'))
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(service.hangup_call('rtc_test'))


def test_legacy_client_receives_update_intent_without_provider_call():
    with pytest.raises(HTTPException) as error:
        asyncio.run(realtime.create_realtime_avatar_session(
            realtime.RealtimeAvatarSessionRequest(profile_id=uuid4()),Mock(),None))
    assert error.value.status_code==426
    assert error.value.detail['code']=='app_update_required'


def test_deletion_gate_does_not_treat_ack_as_closed(registry):
    repo,row=registry
    repo.require_profile_terminated(row['profile_id'])
    row=repo.reserve(profile_id=row['profile_id'],user_id=row['user_id'],
        auth_session_id=row['auth_session_id'],purpose_revision=1,model='test',voice='test',metadata={})
    repo.begin(row['session_id'],None)
    repo.register(row['session_id'],'rtc_test')
    repo.request_profile(row['profile_id'])
    repo.finish(repo.claim(),True)
    result=repo.profile_cleanup_status(row['profile_id'])
    assert result['hangup_acknowledged']==1
    assert result['confirmed_terminated'] is False
    with pytest.raises(RealtimeStateConflict):
        repo.require_profile_terminated(row['profile_id'])


@pytest.fixture
def route_setup(monkeypatch):
    profile,session=uuid4(),uuid4()
    row={'session_id':session,'profile_id':profile,'purpose_revision':1,'model':'test','voice':'test',
         'metadata':{},'hangup_requested_at':None,'hangup_acknowledged_at':None,'state':'reserved'}
    repo=Mock()
    repo.owned.return_value=row
    repo.register.return_value=row
    repo.reserve.return_value=row
    repo.request.return_value={**row,'state':'hangup_requested'}
    vector=Mock()
    vector.evidence_version.return_value=None
    provider=Mock()
    provider.configuration.return_value=('test','test')
    provider.create_call=AsyncMock(return_value=('rtc_test','v=0\r\n'))
    provider.hangup_call=AsyncMock()
    provider._build_avatar_instructions.return_value='synthetic'
    monkeypatch.setattr(realtime,'OpenAIRealtimeRegistry',lambda:repo)
    monkeypatch.setattr(realtime,'PGVectorMemoryService',lambda:vector)
    monkeypatch.setattr(realtime,'openai_realtime_service',provider)
    monkeypatch.setattr(realtime,'require_profile_access',Mock())
    monkeypatch.setattr(realtime,'require_profile_purposes',Mock(return_value=SimpleNamespace(revision=1)))
    monkeypatch.setattr(realtime,'require_current_memory_evidence',Mock())
    monkeypatch.setattr(realtime,'retrieval_service',SimpleNamespace(retrieve=Mock(return_value=[])))
    principal=SimpleNamespace(user=SimpleNamespace(user_id=uuid4()),session_id=uuid4())
    return repo,vector,provider,principal,row


def test_reserve_returns_no_secret_and_sends_no_provider_content(route_setup):
    repo,vector,provider,principal,row=route_setup
    result=asyncio.run(realtime.create_realtime_avatar_session(
        realtime.RealtimeAvatarSessionRequest(profile_id=row['profile_id']),principal,'stay_sdp'))
    assert result.connection_mode=='stay_sdp'
    assert 'client_secret' not in result.model_dump()
    provider.create_call.assert_not_called()
    vector.evidence_version.assert_not_called()


def test_connect_persists_before_releasing_sdp(route_setup):
    repo,vector,provider,principal,row=route_setup
    result=asyncio.run(realtime.connect_realtime_avatar_session(row['session_id'],
        realtime.RealtimeConnectRequest(offer_sdp='v=0\r\n'),principal))
    assert result['answer_sdp']=='v=0\r\n'
    repo.register.assert_called_once_with(row['session_id'],'rtc_test')
    assert vector.require_evidence_version.call_count==3


@pytest.mark.parametrize('reason',['revoked','bad_answer','timeout','storage','duplicate'])
def test_connect_failures_never_return_sdp(route_setup,reason):
    repo,vector,provider,principal,row=route_setup
    if reason=='revoked':
        vector.require_evidence_version.side_effect=[None,None,realtime.PGVectorStaleIndexError('changed')]
    elif reason=='bad_answer':
        provider.create_call.return_value=('rtc_test','invalid')
    elif reason=='timeout':
        provider.create_call.side_effect=TimeoutError()
    elif reason=='storage':
        repo.register.side_effect=RuntimeError('database unavailable')
    else:
        repo.begin.side_effect=RealtimeStateConflict('already used')
    with pytest.raises(HTTPException):
        asyncio.run(realtime.connect_realtime_avatar_session(row['session_id'],
            realtime.RealtimeConnectRequest(offer_sdp='v=0\r\n'),principal))
    if reason=='timeout':
        repo.unknown.assert_called_once_with(row['session_id'])
    elif reason=='duplicate':
        provider.create_call.assert_not_called()
    else:
        provider.hangup_call.assert_awaited_once_with('rtc_test')


def test_client_disconnect_still_registers_and_requests_hangup(route_setup):
    repo,vector,provider,principal,row=route_setup
    async def scenario():
        entered=asyncio.Event()
        release=asyncio.Event()
        async def create(**kwargs):
            entered.set()
            await release.wait()
            return 'rtc_late','v=0\r\n'
        provider.create_call.side_effect=create
        task=asyncio.create_task(realtime.connect_realtime_avatar_session(row['session_id'],
            realtime.RealtimeConnectRequest(offer_sdp='v=0\r\n'),principal))
        await entered.wait()
        task.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    repo.register.assert_called_once_with(row['session_id'],'rtc_late')
    repo.request.assert_called_once_with(row['session_id'])
    provider.hangup_call.assert_awaited_once_with('rtc_late')


def test_delete_checks_bound_owner_but_not_profile_purpose(route_setup,monkeypatch):
    repo,vector,provider,principal,row=route_setup
    guard=Mock(side_effect=AssertionError('cleanup must survive erasure'))
    monkeypatch.setattr(realtime,'require_profile_access',guard)
    monkeypatch.setattr(realtime,'require_profile_purposes',guard)
    result=asyncio.run(realtime.delete_realtime_avatar_session(row['session_id'],principal))
    assert result['hangup_acknowledged'] is False
    guard.assert_not_called()
    repo.owned.return_value=None
    with pytest.raises(HTTPException) as error:
        asyncio.run(realtime.delete_realtime_avatar_session(row['session_id'],principal))
    assert error.value.status_code==404
