import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import psycopg
import pytest

from app.services.avatar_provider_service import AvatarProviderService
from app.services.runtime_cleanup_service import RuntimeCleanupService
from test_runtime_cleanup_durable import registry


@pytest.mark.parametrize('delete_status,get_status,accepted', [
    (204,404,True), (404,404,True), (204,200,False), (404,401,False),
    (401,404,False), (500,404,False), (202,404,False), (204,503,False)])
def test_hard_delete_requires_confirmed_absence(monkeypatch, delete_status, get_status, accepted):
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(delete_status if request.method == 'DELETE' else get_status)
    original = httpx.AsyncClient
    monkeypatch.setattr('app.services.avatar_provider_service.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    operation = AvatarProviderService().delete_tavus_conversation(conversation_id='c_owned')
    if accepted:
        asyncio.run(operation)
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(operation)
    assert requests[0].url.path == '/v2/conversations/c_owned'
    assert dict(requests[0].url.params) == {'hard': 'true'}
    if delete_status in {204,404}:
        assert [r.method for r in requests] == ['DELETE','GET']
        assert requests[1].url.path == requests[0].url.path
    else:
        assert len(requests) == 1


def prepare(repo, profile):
    repo.begin_worker('test', profile, 'room')
    repo.begin_provider_create('test')
    repo.conversation('test', 'c_owned')
    repo.request('test')


def test_failed_deletion_resumes_after_durable_end_and_preserves_id(registry):
    repo, profile = registry
    prepare(repo, profile)
    calls = []
    async def end(**kwargs):
        calls.append('end')
    async def delete(**kwargs):
        assert repo.get('test')['tavus_ended']
        calls.append('delete')
        if calls.count('delete') == 1:
            raise RuntimeError('temporary failure')
    provider = SimpleNamespace(end_tavus_conversation=AsyncMock(side_effect=end),
        delete_tavus_conversation=AsyncMock(side_effect=delete))
    adapter = SimpleNamespace(_delete_remote_resources=AsyncMock())
    asyncio.run(RuntimeCleanupService(repo, adapter, provider).recover_once())
    row = repo.get('test')
    assert row['tavus_ended'] and not row['conversation_deleted']
    assert row['conversation_id'] == 'c_owned' and row['completed_at'] is None
    repo.request('test')
    asyncio.run(RuntimeCleanupService(repo, adapter, provider).recover_once())
    assert calls == ['end','delete','delete']
    assert repo.get('test')['conversation_deleted'] and repo.get('test')['completed_at']


def test_end_failure_never_deletes_conversation(registry):
    repo, profile = registry
    prepare(repo, profile)
    provider = SimpleNamespace(end_tavus_conversation=AsyncMock(side_effect=RuntimeError()),
        delete_tavus_conversation=AsyncMock())
    asyncio.run(RuntimeCleanupService(repo, SimpleNamespace(_delete_remote_resources=AsyncMock()), provider).recover_once())
    provider.delete_tavus_conversation.assert_not_called()
    assert repo.get('test')['completed_at'] is None


def test_database_blocks_completion_and_retention_before_deletion(registry):
    repo, profile = registry
    prepare(repo, profile)
    repo.ended('test', 'c_owned')
    repo.finish(repo.claim(), True)
    assert repo.get('test')['completed_at'] is None
    with pytest.raises(psycopg.errors.CheckViolation):
        repo._execute("UPDATE avatar_runtime_cleanup SET completed_at=NOW()-INTERVAL '31 days'")
    repo._execute("DELETE FROM avatar_runtime_cleanup WHERE completed_at<=NOW()-INTERVAL '30 days'")
    assert repo.get('test')['conversation_id'] == 'c_owned'


def test_migration_reopens_completed_provider_conversations(registry):
    repo, profile = registry
    prepare(repo, profile)
    with psycopg.connect(repo.database_url) as connection:
        connection.execute('ALTER TABLE avatar_runtime_cleanup DROP COLUMN conversation_deleted CASCADE')
        connection.execute("UPDATE avatar_runtime_cleanup SET tavus_ended=TRUE, completed_at=NOW()-INTERVAL '31 days'")
        connection.execute(Path('migrations/029_runtime_conversation_deletion.sql').read_text())
    row = repo.get('test')
    assert row['completed_at'] is None and row['cleanup_requested']
    assert not row['conversation_deleted'] and row['conversation_id'] == 'c_owned'
    assert repo.claim()['session_id'] == 'test'
