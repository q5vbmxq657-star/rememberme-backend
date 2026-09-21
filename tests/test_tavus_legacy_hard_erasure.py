import asyncio

import httpx
import pytest

from app.services.avatar_provider_service import AvatarProviderService


@pytest.mark.parametrize('status', [200, 204, 404, 401, 500])
def test_legacy_replica_erasure_explicitly_deletes_training_assets(monkeypatch, status):
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only')
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status if '/replicas/' in request.url.path else 200)
    original = httpx.AsyncClient
    monkeypatch.setattr('app.services.avatar_provider_service.httpx.AsyncClient',
        lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    operation = AvatarProviderService().delete_tavus_identity(replica_id='r_owned', persona_id='p_owned')
    if status in {200, 204, 404}:
        asyncio.run(operation)
    else:
        with pytest.raises(RuntimeError):
            asyncio.run(operation)
    assert [r.url.path for r in requests] == ['/v2/personas/p_owned', '/v2/replicas/r_owned']
    assert dict(requests[0].url.params) == {}
    assert dict(requests[1].url.params) == {'hard': 'true'}
    assert all(r.method == 'DELETE' for r in requests)
