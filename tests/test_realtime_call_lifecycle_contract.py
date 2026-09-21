import asyncio
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.routes import realtime
from test_openai_realtime_durable import route_setup


def test_start_authorization_does_not_block_event_loop(route_setup, monkeypatch):
    repo, vector, provider, principal, row = route_setup

    def check(*args, **kwargs):
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()

    monkeypatch.setattr(realtime, 'require_profile_access', check)
    asyncio.run(realtime.create_realtime_avatar_session(
        realtime.RealtimeAvatarSessionRequest(profile_id=row['profile_id']), principal, 'stay_sdp'))
    asyncio.run(realtime.connect_realtime_avatar_session(row['session_id'],
        realtime.RealtimeConnectRequest(offer_sdp='v=0\r\n'), principal))


def test_end_persists_intent_before_immediate_hangup(route_setup):
    repo, vector, provider, principal, row = route_setup
    pending = {**row, 'call_id': 'rtc_active', 'state': 'hangup_requested'}
    repo.request.return_value = pending

    async def hangup(call_id):
        repo.request.assert_called_once_with(row['session_id'])
        repo.acknowledge.assert_not_called()
        assert call_id == 'rtc_active'

    provider.hangup_call.side_effect = hangup
    repo.owned.side_effect = [row, {**pending, 'state': 'hangup_acknowledged', 'hangup_acknowledged_at': 'now'}]
    result = asyncio.run(realtime.delete_realtime_avatar_session(row['session_id'], principal))
    assert result['hangup_acknowledged'] is True
    repo.acknowledge.assert_called_once_with(row['session_id'], 'rtc_active')


@pytest.mark.parametrize('failure', [TimeoutError(), RuntimeError('provider unavailable')])
def test_failed_immediate_end_keeps_durable_pending_state(route_setup, failure):
    repo, vector, provider, principal, row = route_setup
    repo.request.return_value = {**row, 'call_id': 'rtc_active', 'state': 'hangup_requested'}
    provider.hangup_call.side_effect = failure
    result = asyncio.run(realtime.delete_realtime_avatar_session(row['session_id'], principal))
    assert result['state'] == 'hangup_requested'
    assert result['hangup_acknowledged'] is False
    repo.acknowledge.assert_not_called()


def test_storage_failure_cannot_acknowledge_end(route_setup):
    repo, vector, provider, principal, row = route_setup
    repo.request.side_effect = RuntimeError('database unavailable')
    with pytest.raises(HTTPException) as error:
        asyncio.run(realtime.delete_realtime_avatar_session(row['session_id'], principal))
    assert error.value.status_code == 503
    provider.hangup_call.assert_not_awaited()


def test_start_preserves_authorization_denial(route_setup, monkeypatch):
    repo, vector, provider, principal, row = route_setup
    monkeypatch.setattr(realtime, 'require_profile_access', Mock(side_effect=HTTPException(403, 'Denied')))
    with pytest.raises(HTTPException) as error:
        asyncio.run(realtime.create_realtime_avatar_session(
            realtime.RealtimeAvatarSessionRequest(profile_id=row['profile_id']), principal, 'stay_sdp'))
    assert error.value.status_code == 403
    repo.reserve.assert_not_called()


def test_connect_storage_failure_is_retryable_not_false_ready(route_setup):
    repo, vector, provider, principal, row = route_setup
    repo.owned.side_effect = RuntimeError('database unavailable')
    with pytest.raises(HTTPException) as error:
        asyncio.run(realtime.connect_realtime_avatar_session(row['session_id'],
            realtime.RealtimeConnectRequest(offer_sdp='v=0\r\n'), principal))
    assert error.value.status_code == 503
    provider.create_call.assert_not_awaited()


def test_immediate_hangup_is_bounded_and_cancels_transport(route_setup, monkeypatch):
    repo, vector, provider, principal, row = route_setup
    repo.request.return_value = {**row, 'call_id': 'rtc_active', 'state': 'hangup_requested'}
    cancelled = []
    original_wait = asyncio.wait_for

    async def blocked(call_id):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(call_id)

    async def bounded(awaitable, *, timeout):
        assert timeout == 3.0
        return await original_wait(awaitable, timeout=0.01)

    provider.hangup_call.side_effect = blocked
    monkeypatch.setattr(realtime.asyncio, 'wait_for', bounded)
    result = asyncio.run(realtime.delete_realtime_avatar_session(row['session_id'], principal))
    assert result['hangup_acknowledged'] is False
    assert cancelled == ['rtc_active']
    repo.acknowledge.assert_not_called()


def test_cleanup_storage_error_preserves_safe_connect_failure(route_setup):
    repo, vector, provider, principal, row = route_setup
    provider.create_call.return_value = ('rtc_known', 'invalid-answer')
    repo.request.side_effect = RuntimeError('private database diagnostic')
    with pytest.raises(HTTPException) as error:
        asyncio.run(realtime.connect_realtime_avatar_session(row['session_id'],
            realtime.RealtimeConnectRequest(offer_sdp='v=0\r\n'), principal))
    assert error.value.status_code == 409
    assert 'private database' not in str(error.value.detail)
    provider.hangup_call.assert_awaited_once_with('rtc_known')
