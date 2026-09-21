import asyncio
import json
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock

import httpx
import pytest

from app.services.tavus_runtime_correlation import CorrelatedTavusAPI, find_correlated_conversation
from test_runtime_cleanup_durable import registry


def test_worker_connect_failure_does_not_imply_provider_creation(registry):
    repo, profile = registry
    repo.begin_worker('test', profile, 'room')
    repo.request('test')
    row = repo.claim()
    assert row['worker_started'] and not row['provider_create_started']
    repo.finish(row, True)
    assert repo.get('test')['completed_at'] is not None
    with pytest.raises(RuntimeError):
        repo.begin_provider_create('test')


def test_worker_connect_exception_requests_cleanup_without_remote_create(registry, monkeypatch):
    from app.workers import avatar_tavus_worker as worker
    repo, profile = registry
    repo.authorize = Mock()
    monkeypatch.setattr(worker, 'RuntimeCleanupRepository', lambda: repo)
    avatar = Mock()
    monkeypatch.setattr(worker, 'CorrelatedAvatarSession', avatar)
    ctx = SimpleNamespace(
        job=SimpleNamespace(metadata=json.dumps(dict(session_id='test', profile_id=str(profile),
            face_id='face', avatar_identity='avatar'))),
        room=SimpleNamespace(name='room'), connect=AsyncMock(side_effect=ConnectionError()),
        add_shutdown_callback=Mock())
    with pytest.raises(ConnectionError):
        asyncio.run(worker.entrypoint(ctx))
    avatar.assert_not_called()
    row = repo.get('test')
    assert row['cleanup_requested'] and not row['provider_create_started']
    repo.finish(repo.claim(), True)
    assert repo.get('test')['completed_at'] is not None


def test_create_is_correlated_and_acknowledged_before_return(registry):
    repo, profile = registry
    repo.begin_worker('test', profile, 'room')
    repo.authorize = Mock()
    calls = []
    async def create(*, extra_payload, **kwargs):
        calls.append((extra_payload, kwargs))
        assert repo.get('test')['provider_create_started']
        return 'c_returned'
    bridge = CorrelatedTavusAPI(SimpleNamespace(create_conversation=create), repo, 'test')
    assert asyncio.run(bridge.create_conversation(face_id='face', properties={'livekit_room_token': 'test'})) == 'c_returned'
    assert calls[0][0] == {'conversation_name': repo.get('test')['conversation_name']}
    assert repo.get('test')['conversation_id'] == 'c_returned'
    with pytest.raises(RuntimeError):
        asyncio.run(bridge.create_conversation(face_id='face'))
    assert len(calls) == 1


def test_lost_create_response_keeps_intent_and_never_replays(registry):
    repo, profile = registry
    repo.begin_worker('test', profile, 'room')
    repo.authorize = Mock()
    calls = []
    async def create(*, extra_payload, **kwargs):
        calls.append(extra_payload)
        raise TimeoutError()
    bridge = CorrelatedTavusAPI(SimpleNamespace(create_conversation=create), repo, 'test')
    with pytest.raises(TimeoutError):
        asyncio.run(bridge.create_conversation())
    with pytest.raises(RuntimeError):
        asyncio.run(bridge.create_conversation())
    assert len(calls) == 1
    assert repo.get('test')['provider_create_started']
    assert repo.get('test')['conversation_id'] is None
    assert repo.get('test')['cleanup_requested']


@pytest.mark.parametrize('cancelled', [False, True])
def test_create_timeout_or_cancellation_schedules_recovery(monkeypatch, cancelled):
    repository = Mock()
    repository.begin_provider_create.return_value = 'stay_' + 'b' * 32
    async def create(*, extra_payload, **kwargs):
        pass
    async def abort(awaitable, *, timeout):
        assert timeout == 45
        awaitable.close()
        raise asyncio.CancelledError() if cancelled else TimeoutError()
    monkeypatch.setattr('app.services.tavus_runtime_correlation.asyncio.wait_for', abort)
    bridge = CorrelatedTavusAPI(SimpleNamespace(create_conversation=create), repository, 'test')
    with pytest.raises(asyncio.CancelledError if cancelled else TimeoutError):
        asyncio.run(bridge.create_conversation())
    repository.request.assert_called_once_with('test')
    repository.conversation.assert_not_called()


@pytest.mark.parametrize('scenario', ['overlap', 'changing_count', 'excess_rows', 'invalid_id'])
def test_inconsistent_provider_pages_never_resolve(monkeypatch, scenario):
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    name = 'stay_' + 'c' * 32
    def handler(request):
        page = int(request.url.params['page'])
        identifier = 'same' if scenario == 'overlap' else str(page)
        if scenario == 'invalid_id':
            identifier = '../foreign'
        total = 0 if scenario == 'excess_rows' else (3 if scenario == 'changing_count' and page == 2 else 2)
        return httpx.Response(200, json={'total_count': total,
            'data': [{'conversation_id': identifier, 'conversation_name': name}]})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError):
                await find_correlated_conversation(name, client=client)
    asyncio.run(exercise())


def test_recovery_persists_reconciled_id_and_verifies_provider_end(registry, monkeypatch):
    from app.services.runtime_cleanup_service import RuntimeCleanupService
    repo, profile = registry
    repo.begin_worker('test', profile, 'room')
    name = repo.begin_provider_create('test')
    repo.request('test')
    lookup = AsyncMock(return_value='c_recovered')
    monkeypatch.setattr('app.services.runtime_cleanup_service.find_correlated_conversation', lookup)
    provider = SimpleNamespace(end_tavus_conversation=AsyncMock(), delete_tavus_conversation=AsyncMock())
    adapter = SimpleNamespace(_delete_remote_resources=AsyncMock())
    assert asyncio.run(RuntimeCleanupService(repo, adapter, provider).recover_once())
    lookup.assert_awaited_once_with(name)
    provider.end_tavus_conversation.assert_awaited_once_with(conversation_id='c_recovered')
    row = repo.get('test')
    assert row['conversation_id'] == 'c_recovered' and row['tavus_ended']
    assert row['conversation_deleted']
    assert row['completed_at'] is not None


@pytest.mark.parametrize('scenario', ['exact', 'absent', 'prefix', 'ambiguous', 'denied', 'incomplete'])
def test_reconcile_requires_exact_unique_match_and_complete_pages(monkeypatch, scenario):
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    name = 'stay_' + 'a' * 32
    requests = []
    def handler(request):
        requests.append(request)
        if scenario == 'denied':
            return httpx.Response(401)
        page = int(request.url.params['page'])
        rows = [{'conversation_id': 'other', 'conversation_name': 'someone_else'}]
        if page == 2:
            rows = [{'conversation_id': 'target', 'conversation_name': name if scenario != 'prefix' else name + '_extra'}]
            if scenario in {'absent', 'incomplete'}:
                rows = []
            if scenario == 'ambiguous':
                rows.append({'conversation_id': 'duplicate', 'conversation_name': name})
        return httpx.Response(200, json={'data': rows, 'total_count': 3 if scenario in {'ambiguous', 'incomplete'} else 2})
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            if scenario == 'exact':
                assert await find_correlated_conversation(name, client=client) == 'target'
            else:
                with pytest.raises(RuntimeError):
                    await find_correlated_conversation(name, client=client)
    asyncio.run(exercise())
    assert all(request.method == 'GET' for request in requests)
    assert all('verbose' not in request.url.params for request in requests)


def test_installed_sdk_single_attempt_and_injection_contract(monkeypatch):
    from livekit.plugins.tavus.api import TavusAPI
    from app.services.tavus_runtime_correlation import CorrelatedAvatarSession
    async def exercise():
        monkeypatch.setattr(CorrelatedAvatarSession, '_ensure_http_session', lambda self: object())
        session = CorrelatedAvatarSession(repository=Mock(), session_id='test', api_key='test-key')
        assert isinstance(session._api, CorrelatedTavusAPI)
        assert isinstance(session._api.delegate, TavusAPI)
        assert session._api.delegate._conn_options.max_retry == 1
    asyncio.run(exercise())
