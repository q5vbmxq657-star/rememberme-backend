import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

import app.services.avatar_provider_service as module
from app.services.avatar_provider_service import AvatarProviderService, AvatarProviderJobState, AvatarProviderStatusUnavailableError
from app.services.digital_human_profile_repository import DigitalHumanProfileRepositoryError, DigitalHumanProfileNotFoundError
from test_tavus_face_training_contract import FakeRepository, valid_tavus_consent


@pytest.mark.parametrize('outcome', ['timeout', 'transport', 'invalid_json', 'missing_id', 'array', 408, 409, 429, 500, 503])
def test_uncertain_creation_is_durable_and_not_failed(monkeypatch, valid_tavus_consent, outcome):
    monkeypatch.setenv('TAVUS_API_KEY', 'contract-key')
    client = AsyncMock()
    client.__aenter__.return_value = client
    response = Mock(status_code=outcome if isinstance(outcome, int) else 200)
    response.json.return_value = [] if outcome == 'array' else {}
    if outcome == 'invalid_json':
        response.json.side_effect = ValueError('malformed')
    client.post.return_value = response
    if outcome == 'timeout':
        client.post.side_effect = TimeoutError()
    elif outcome == 'transport':
        client.post.side_effect = httpx.ReadError('private detail')
    monkeypatch.setattr(module.httpx, 'AsyncClient', Mock(return_value=client))
    service = AvatarProviderService()
    repository = FakeRepository()
    service._profile_repository = repository
    # Exercise uncertain provider creation after the separately tested owned-asset resolution.
    service._extract_tavus_training_source = Mock(return_value=('train_image_url', 'https://stay.example/image'))
    state = asyncio.run(service.submit(provider='tavus', profile_id=str(uuid4()), package_record_id=str(uuid4()),
        package={'train_image_url': 'https://stay.example/image', 'voice_name': 'james'}))
    assert state.status == 'uploading'
    assert state.error_message is None
    assert state.external_avatar_id is None
    assert state.external_job_id == f'tavus:pending:{repository.job_id}'
    assert repository.updates == [(repository.job_id, {'status': 'submitted'})]
    assert repository.avatar_updates == []
    client.post.assert_awaited_once()
    assert client.post.call_args.kwargs['json']['face_name'] == repository.created['request_payload']['_stay_face_name']
    assert '_stay_face_name' not in client.post.call_args.kwargs['json']


def pending_service(status='submitted'):
    service = AvatarProviderService()
    job_id, profile_id = uuid4(), uuid4()
    row = dict(job_id=job_id, profile_id=profile_id, provider='tavus', training_type='avatar', status=status,
               provider_job_id=None, created_at=datetime.now(timezone.utc), request_payload={})
    repository = Mock()
    repository.get_training_job.return_value = row
    service._profile_repository = repository
    return service, repository, row, f'tavus:pending:{job_id}'


def test_pending_alias_resolves_profile_and_does_not_fetch_without_provider_id():
    service, _, row, alias = pending_service()
    service._fetch_tavus_status = AsyncMock()
    assert service.require_training_job_profile_id(alias) == row['profile_id']
    state = asyncio.run(service.status(alias))
    assert state.status == 'uploading'
    assert state.external_job_id == alias
    service._fetch_tavus_status.assert_not_awaited()


@pytest.mark.parametrize('status', ['failed', 'cancelled', 'deleted'])
def test_definitive_terminal_result_is_preserved(status):
    service, _, _, alias = pending_service(status)
    assert asyncio.run(service.status(alias)).status == 'failed'


@pytest.mark.parametrize('change', [{'provider': 'other'}, {'training_type': 'voice'}])
def test_pending_alias_cannot_address_other_job_types(change):
    service, _, row, alias = pending_service()
    row.update(change)
    with pytest.raises(DigitalHumanProfileNotFoundError):
        service.require_training_job_profile_id(alias)


def test_known_provider_result_keeps_stable_alias_without_corrupting_cached_state():
    service, repository, row, alias = pending_service()
    row['provider_job_id'] = 'tavus:face'
    row['provider_payload'] = {'face_id': 'face'}
    repository.get_training_job_by_provider_job_id.return_value = row
    ready = AvatarProviderJobState('tavus:face', 'face', 'ready', None)
    service._fetch_tavus_status = AsyncMock(return_value=ready)
    result = asyncio.run(service.status(alias))
    assert result.external_job_id == alias
    assert result.status == 'ready'
    assert ready.external_job_id == 'tavus:face'
    assert repository.set_avatar_training.call_args.kwargs['training_job_id'] == row['job_id']


def test_storage_outage_is_not_converted_to_failed_job():
    service, repository, _, _ = pending_service()
    repository.get_training_job_by_provider_job_id.side_effect = DigitalHumanProfileRepositoryError('offline')
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service.status('tavus:face'))


@pytest.mark.parametrize('result', ['found', 'absent', 'unavailable'])
def test_lost_response_recovery_never_replays_creation(monkeypatch, result):
    import app.services.tavus_training_reconciliation as recovery
    service, repository, row, alias = pending_service()
    correlation = f"stay_face_{row['job_id'].hex}"
    row['request_payload'] = {'_stay_face_name': correlation}
    row['submitted_at'] = datetime.now(timezone.utc) - timedelta(minutes=3)
    face = {'face_id': 'found_face', 'face_name': correlation, 'status': 'started'}
    lookup = AsyncMock(return_value=face if result == 'found' else None)
    if result == 'unavailable':
        lookup.side_effect = recovery.TavusTrainingReconciliationError('ambiguous')
    monkeypatch.setattr(recovery, 'find_training_face', lookup)
    submit = AsyncMock()
    service.submit = submit
    adopted = row | {'provider_job_id': 'tavus:found_face', 'provider_payload': face, 'status': 'training'}
    repository.adopt_reconciled_avatar.return_value = adopted
    repository.get_training_job_by_provider_job_id.return_value = adopted
    service._fetch_tavus_status = AsyncMock(return_value=AvatarProviderJobState('tavus:found_face', 'found_face', 'training', None))
    if result == 'unavailable':
        with pytest.raises(AvatarProviderStatusUnavailableError):
            asyncio.run(service.status(alias))
    else:
        state = asyncio.run(service.status(alias))
        assert state.external_job_id == alias
        assert state.status == ('training' if result == 'found' else 'uploading')
    lookup.assert_awaited_once_with(correlation)
    submit.assert_not_awaited()
    if result != 'found':
        repository.adopt_reconciled_avatar.assert_not_called()


def test_in_flight_creation_does_not_race_reconciliation(monkeypatch):
    import app.services.tavus_training_reconciliation as recovery
    service, _, row, alias = pending_service()
    row['request_payload'] = {'_stay_face_name': f"stay_face_{row['job_id'].hex}"}
    lookup = AsyncMock()
    monkeypatch.setattr(recovery, 'find_training_face', lookup)
    assert asyncio.run(service.status(alias)).status == 'uploading'
    lookup.assert_not_awaited()
