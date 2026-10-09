import asyncio
import json
from types import SimpleNamespace
from threading import Event
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.services import streaming_memory_service as module
from app.routes import streaming_memory as route
from app.schemas.streaming_memory import StreamingMemoryChatRequest


def event(kind, **kwargs):
    return SimpleNamespace(type=kind, **kwargs)


@pytest.fixture
def setup(monkeypatch):
    service = module.StreamingMemoryService.__new__(module.StreamingMemoryService)
    service.client = Mock()
    service.orchestration = SimpleNamespace(route=lambda _: SimpleNamespace(
        model='test', latency_profile=SimpleNamespace(value='test'), temperature=0.5, max_output_tokens=100))
    service.emotional_reasoning_service = Mock()
    service.emotional_reasoning_service.assess.return_value = SimpleNamespace(
        recommended_mode='normal', emotional_intensity=0, dependency_risk=0, crisis_risk=0,
        signals=[], guidance='test')
    monkeypatch.setattr(module.MemoryConversationPromptBuilder, 'build', lambda **kwargs: 'test')
    request = StreamingMemoryChatRequest(profile_id='test', profile_name='Anna', relationship='Friend', user_message='Hello')
    return service, request


def install_stream(service, events):
    class Stream:
        closed = False
        consumed = 0
        def __iter__(self):
            for value in events:
                self.consumed += 1
                yield value
        def close(self): self.closed = True
    stream = Stream()
    service.client.responses.create.return_value = stream
    return stream


@pytest.mark.parametrize('events', [
    [], [event('response.completed')],
    [event('response.output_text.delta', delta='  '), event('response.completed')],
    [event('response.output_text.delta', delta='Hello')],
    *[[event('response.output_text.delta', delta='Hello'), event(kind), event('response.completed')]
      for kind in ['response.failed', 'response.incomplete', 'error']],
    [event('response.output_text.delta', delta=123), event('response.completed')],
])
def test_invalid_terminal_never_completes(setup, events):
    service, request = setup
    stream = install_stream(service, events)
    result = list(service.stream_response(request, authorize=lambda: None))
    assert not any('event: done' in item for item in result)
    assert sum('event: error' in item for item in result) == 1
    assert stream.closed


def test_first_delta_is_immediate_and_terminal_stops_further_frames(setup):
    service, request = setup
    stream = install_stream(service, [event('response.output_text.delta', delta='Hello'),
        event('response.completed'), event('response.output_text.delta', delta='forbidden late text')])
    output = service.stream_response(request, authorize=lambda: None)
    assert 'event: metadata' in next(output)
    assert stream.consumed == 0
    assert 'Hello' in next(output)
    assert stream.consumed == 1
    assert 'event: done' in next(output)
    assert stream.closed
    assert list(output) == []
    assert stream.consumed == 2


def test_voice_batches_tokens_without_losing_text_or_terminal(setup):
    service, request = setup
    request = request.model_copy(update={"channel": "voice"})
    chunks = ["Hello", " there", ".", " How", " are", " you", "?"]
    install_stream(service, [*(event('response.output_text.delta', delta=text) for text in chunks),
                             event('response.completed')])
    result = list(service.stream_response(request, authorize=lambda: None))
    deltas = [json.loads(frame.split('data: ', 1)[1])['text']
              for frame in result if frame.startswith('event: delta\n')]
    assert deltas == ["Hello", " there. How are you?"]
    assert ''.join(deltas) == ''.join(chunks)
    assert result[-1].startswith('event: done\n')


def test_voice_does_not_flush_buffered_text_after_provider_failure(setup):
    service, request = setup
    request = request.model_copy(update={"channel": "voice"})
    install_stream(service, [event('response.output_text.delta', delta='Hello'),
                            event('response.output_text.delta', delta=' unfinished'),
                            event('response.failed')])
    result = list(service.stream_response(request, authorize=lambda: None))
    assert not any('unfinished' in frame for frame in result)
    assert result[-1].startswith('event: error\n')


def test_route_uses_canonical_authorizer_without_duplicate_profile_reads(setup, monkeypatch):
    service, request = setup
    install_stream(service, [event('response.output_text.delta', delta='Hello'), event('response.completed')])
    access = Mock()
    purposes = Mock(return_value=SimpleNamespace(revision=3))
    canonical = Mock()
    history = SimpleNamespace(
        prepare=lambda *args, **kwargs: (request, SimpleNamespace(consent_revision=3), canonical),
        stream_events=lambda source, **kwargs: source)
    monkeypatch.setattr(route, 'require_profile_access', access)
    monkeypatch.setattr(route, 'require_profile_purposes', purposes)
    monkeypatch.setattr(route, 'MemoryConversationHistoryService', lambda: history)
    monkeypatch.setattr(route, 'StreamingMemoryService', lambda: service)
    monkeypatch.setattr(route, 'conversation_usage', lambda *args, **kwargs: Mock())
    response = route.stream_memory_chat(request, principal=object())
    async def consume():
        return [frame async for frame in response.body_iterator]
    frames = asyncio.run(consume())
    assert frames[-1].startswith('event: done\n')
    assert canonical.call_count >= len(frames)
    access.assert_called_once()
    purposes.assert_called_once()


def test_route_rejects_consent_change_during_preparation(setup, monkeypatch):
    _, request = setup
    monkeypatch.setattr(route, 'require_profile_access', Mock())
    monkeypatch.setattr(route, 'require_profile_purposes', Mock(return_value=SimpleNamespace(revision=3)))
    monkeypatch.setattr(route, 'MemoryConversationHistoryService', lambda: SimpleNamespace(
        prepare=lambda *args, **kwargs: (request, SimpleNamespace(consent_revision=4), Mock())))
    with pytest.raises(HTTPException) as error:
        route.stream_memory_chat(request, principal=object())
    assert error.value.status_code == 409


def test_route_revocation_during_blocked_provider_read_drops_frame(setup, monkeypatch):
    service, request = setup
    entered = Event()
    release = Event()
    class Stream:
        closed = False
        def __iter__(self):
            entered.set()
            assert release.wait(3)
            yield event('response.output_text.delta', delta='private late frame')
            yield event('response.completed')
        def close(self): self.closed = True
    stream = Stream()
    service.client.responses.create.return_value = stream
    monkeypatch.setattr(route, 'StreamingMemoryService', lambda: service)
    permitted = True
    def authorize():
        if not permitted: raise HTTPException(403, 'revoked')
    async def consume():
        nonlocal permitted
        # Isolate the route's post-read guard; production history also guards.
        history = SimpleNamespace(stream_events=lambda source, **kwargs: source)
        async def collect():
            return [item async for item in route._authorized_events(request,
                history=history, context=None, authorize=authorize)]
        task = asyncio.create_task(collect())
        try:
            assert await asyncio.to_thread(entered.wait, 2)
            permitted = False
        finally:
            release.set()
        return await task
    rest = ''.join(asyncio.run(consume()))
    assert 'private late frame' not in rest and 'event: done' not in rest
    assert 'event: error' in rest
    assert stream.closed
    service.client.close.assert_called_once()


def test_generator_close_releases_provider_without_further_events(setup):
    service, request = setup
    stream = install_stream(service, [event('response.output_text.delta', delta='Hello'), event('response.completed')])
    output = service.stream_response(request, authorize=lambda: None)
    next(output)
    next(output)
    output.close()
    assert stream.closed
    assert stream.consumed == 1


def test_history_setup_failure_closes_service(setup, monkeypatch):
    service, request = setup
    monkeypatch.setattr(route, 'StreamingMemoryService', lambda: service)
    history = Mock()
    history.stream_events.side_effect = RuntimeError('private diagnostics')
    async def consume():
        return [item async for item in route._authorized_events(request, history=history,
            context=None, authorize=lambda: None)]
    output = ''.join(asyncio.run(consume()))
    assert 'event: error' in output and 'private diagnostics' not in output
    service.client.close.assert_called_once()
    service.emotional_reasoning_service.close.assert_called_once()


def test_partial_initialization_closes_created_client(monkeypatch):
    client = Mock()
    monkeypatch.setattr(module, 'make_memory_chat_openai_client', lambda: client)
    monkeypatch.setattr(module, 'EmotionalReasoningService', Mock(side_effect=RuntimeError('unavailable')))
    with pytest.raises(RuntimeError):
        module.StreamingMemoryService()
    client.close.assert_called_once()


def test_disconnect_during_send_closes_upstream_and_clients(setup, monkeypatch):
    service, request = setup
    stream = install_stream(service, [event('response.output_text.delta', delta='Hello'),
        event('response.output_text.delta', delta='must not send'), event('response.completed')])
    monkeypatch.setattr(route, 'StreamingMemoryService', lambda: service)
    history = SimpleNamespace(stream_events=lambda source, **kwargs: source)
    sent = []

    async def consume():
        response = route._ClosingMemoryStreamingResponse(route._authorized_events(
            request, history=history, context=None, authorize=lambda: None))
        async def send(message):
            sent.append(message)
            if b'event: delta' in message.get('body', b''):
                raise asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await response.stream_response(send)

    asyncio.run(consume())
    assert stream.closed
    assert stream.consumed == 1
    assert b'must not send' not in b''.join(item.get('body', b'') for item in sent)
    service.client.close.assert_called_once()
    service.emotional_reasoning_service.close.assert_called_once()


@pytest.mark.parametrize('mode', ['normal', 'crisis_redirect'])
def test_timings_are_monotonic_numeric_and_content_free(setup, monkeypatch, mode):
    service, request = setup
    service.emotional_reasoning_service.assess.return_value.recommended_mode = mode
    install_stream(service, [event('response.output_text.delta', delta='Hello'), event('response.completed')])
    ticks = iter([10.0, 10.1, 10.6, 11.0, 12.0])
    monkeypatch.setattr(module, 'perf_counter', lambda: next(ticks))
    output = list(service.stream_response(request, authorize=lambda: None))
    metadata = json.loads(output[0].split('data: ', 1)[1])
    done = json.loads(output[-1].split('data: ', 1)[1])
    assert metadata['timing_ms'] == {'assessment': 500.0}
    assert done['timing_ms'] == {'assessment': 500.0, 'first_delta': 1000.0, 'total': 2000.0}


def test_internal_provider_events_add_no_authorization_roundtrips(setup):
    service, request = setup
    internal = [event('response.in_progress'), event('response.output_item.added'),
                event('response.content_part.added'), event('response.output_text.delta', delta='')]
    install_stream(service, internal * 100 + [event('response.output_text.delta', delta='Hello'),
        event('response.completed')])
    authorize = Mock()
    result = list(service.stream_response(request, authorize=authorize))
    assert 'event: done' in result[-1]
    # Entry, post-assessment, pre-provider disclosure only. Route owns emission.
    assert authorize.call_count == 3
