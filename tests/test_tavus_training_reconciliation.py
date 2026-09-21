import asyncio
from contextlib import asynccontextmanager

import httpx
import pytest

from app.services import tavus_training_reconciliation as module


NAME = 'stay_face_' + 'a' * 32


def face(identifier='r_owned', name=NAME):
    return {'face_id': identifier, 'face_name': name, 'status': 'completed'}


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    clients, requests = [], []
    def record(request):
        requests.append(request)
        return handler(request)
    def factory(**kwargs):
        assert kwargs == {'timeout': 10, 'follow_redirects': False}
        client = original(transport=httpx.MockTransport(record), **kwargs)
        clients.append(client)
        return client
    monkeypatch.setenv('TAVUS_API_KEY', 'test-only-secret')
    monkeypatch.setattr(module.httpx, 'AsyncClient', factory)
    return requests, clients


def test_exact_match_is_returned_only_after_all_pages(monkeypatch):
    pages = [[{**face(), 'private_url': 'discard'}], [face('r_other', 'Other')]]
    requests, clients = transport(monkeypatch, lambda request: httpx.Response(200,
        json={'data': pages[int(request.url.params['page'])-1], 'total_count': 2}))
    assert asyncio.run(module.find_training_face(NAME)) == face()
    assert len(requests) == 2 and clients[0].is_closed
    for index, request in enumerate(requests, 1):
        assert request.method == 'GET' and request.url.path == '/v2/faces'
        assert dict(request.url.params) == {'limit': '100', 'page': str(index)}
        assert request.headers['x-api-key'] == 'test-only-secret'


@pytest.mark.parametrize('rows', [[], [face(name=NAME+'_extra')], [face(name=NAME.upper())]])
def test_only_complete_listing_without_exact_match_returns_none(monkeypatch, rows):
    transport(monkeypatch, lambda _: httpx.Response(200, json={'data': rows, 'total_count': len(rows)}))
    assert asyncio.run(module.find_training_face(NAME)) is None


@pytest.mark.parametrize('payload', [None, [], {}, {'data': [], 'total_count': True},
    {'data': [], 'total_count': -1}, {'data': {}, 'total_count': 1},
    {'data': [face()], 'total_count': 0}, {'data': [None], 'total_count': 1},
    {'data': [dict(face(), face_id='../other')], 'total_count': 1},
    {'data': [dict(face(), face_name=None)], 'total_count': 1},
    {'data': [dict(face(), status='')], 'total_count': 1},
    {'data': [dict(face(), status=1)], 'total_count': 1}])
def test_malformed_listing_never_means_not_found(monkeypatch, payload):
    requests, clients = transport(monkeypatch, lambda _: httpx.Response(200, json=payload))
    with pytest.raises(module.TavusTrainingReconciliationError) as error:
        asyncio.run(module.find_training_face(NAME))
    assert NAME not in str(error.value) and 'test-only-secret' not in str(error.value)
    assert clients[0].is_closed and len(requests) == 1


@pytest.mark.parametrize('scenario', ['overlap', 'changing_total', 'ambiguous', 'empty_page', 'limit'])
def test_unsafe_or_incomplete_pagination_raises(monkeypatch, scenario):
    def handler(request):
        page = int(request.url.params['page'])
        item = face('r_'+str(page), NAME if scenario == 'ambiguous' else 'Other')
        if scenario == 'overlap':
            item['face_id'] = 'same'
        total = 101 if scenario == 'limit' else 2
        if scenario == 'changing_total' and page == 2:
            total = 3
        rows = [] if scenario == 'empty_page' else [item]
        return httpx.Response(200, json={'data': rows, 'total_count': total})
    requests, clients = transport(monkeypatch, handler)
    with pytest.raises(module.TavusTrainingReconciliationError):
        asyncio.run(module.find_training_face(NAME))
    assert len(requests) <= 100 and clients[0].is_closed
    if scenario == 'limit':
        assert len(requests) == 100


@pytest.mark.parametrize('status', [301, 401, 403, 404, 429, 503])
def test_http_failure_is_not_not_found(monkeypatch, status):
    transport(monkeypatch, lambda _: httpx.Response(status, text=NAME+' test-only-secret'))
    with pytest.raises(module.TavusTrainingReconciliationError) as error:
        asyncio.run(module.find_training_face(NAME))
    assert NAME not in str(error.value) and 'test-only-secret' not in str(error.value)


def test_invalid_json_is_sanitized(monkeypatch):
    transport(monkeypatch, lambda _: httpx.Response(200, text='test-only-secret'))
    with pytest.raises(module.TavusTrainingReconciliationError, match='unavailable'):
        asyncio.run(module.find_training_face(NAME))


@pytest.mark.parametrize('name', ['', 'stay_face_x', NAME.upper(), NAME+'\n', None])
def test_invalid_correlation_never_calls_provider(monkeypatch, name):
    requests, _ = transport(monkeypatch, lambda _: pytest.fail('Unexpected request'))
    with pytest.raises(module.TavusTrainingReconciliationError):
        asyncio.run(module.find_training_face(name))
    assert requests == []


def test_total_deadline_is_applied_and_sanitized(monkeypatch):
    transport(monkeypatch, lambda _: httpx.Response(200, json={'data': [], 'total_count': 0}))
    @asynccontextmanager
    async def deadline(seconds):
        assert seconds == 30
        yield
        raise TimeoutError('test-only-secret')
    monkeypatch.setattr(module.asyncio, 'timeout', deadline)
    with pytest.raises(module.TavusTrainingReconciliationError, match='unavailable'):
        asyncio.run(module.find_training_face(NAME))
