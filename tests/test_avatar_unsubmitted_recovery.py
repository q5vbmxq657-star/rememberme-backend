import asyncio
import threading
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import app.services.avatar_provider_service as module
from app.services.avatar_provider_service import AvatarProviderService, AvatarProviderJobState, AvatarProviderStatusUnavailableError
from test_tavus_face_training_contract import FakeHTTPClient, FakeRepository, valid_tavus_consent


def setup_recovery(monkeypatch, *, claimed=True):
    monkeypatch.setenv('TAVUS_API_KEY', 'contract-key')
    monkeypatch.setattr(module.httpx, 'AsyncClient', FakeHTTPClient)
    FakeHTTPClient.last_payload = None
    FakeHTTPClient.last_url = None
    service = AvatarProviderService()
    repository = FakeRepository()
    service._profile_repository = repository
    source = 'https://stay.example/v1/avatar-media/public/assets/source-photo'
    saved = {'train_image_url': source + '?old-lease', '_stay_consent_revision': 11,
        '_stay_face_name': f'stay_face_{repository.job_id.hex}', 'voice_name': 'james',
        'auto_fix_training_image': False, 'model_name': 'phoenix-4'}
    row = {'job_id': repository.job_id, 'was_created': False, 'status': 'created',
        'provider_job_id': None, 'request_payload': saved}
    repository.create_training_job = Mock(return_value=row)
    repository.claim_avatar_submission = Mock(return_value=claimed)
    service._extract_tavus_training_source = Mock(return_value=('train_image_url', source + '?fresh-lease'))
    return service, repository, row


def submit(service):
    return asyncio.run(service.submit(provider='tavus', profile_id=str(uuid4()),
        package_record_id=str(uuid4()), package={'voice_name': 'james'}))


def test_never_submitted_job_resumes_original_correlation_with_fresh_lease(monkeypatch, valid_tavus_consent):
    service, repository, row = setup_recovery(monkeypatch)
    assert submit(service).status == 'training'
    assert FakeHTTPClient.last_payload['face_name'] == row['request_payload']['_stay_face_name']
    assert FakeHTTPClient.last_payload['train_image_url'].endswith('?fresh-lease')
    repository.claim_avatar_submission.assert_called_once()


def test_losing_submission_claim_returns_canonical_state_without_post(monkeypatch, valid_tavus_consent):
    service, repository, _ = setup_recovery(monkeypatch, claimed=False)
    canonical = AvatarProviderJobState(f'tavus:pending:{repository.job_id}', None, 'failed', None,
        error_message='This avatar request is no longer active.')
    service._pending_tavus_status = AsyncMock(return_value=canonical)
    assert submit(service) is canonical
    assert FakeHTTPClient.last_url is None


@pytest.mark.parametrize('change', [
    {'_stay_consent_revision': 12}, {'train_image_url': 'https://stay.example/other-photo'},
    {'_stay_face_name': 'unknown-legacy-correlation'},
])
def test_mismatched_recovery_never_claims_or_posts(monkeypatch, valid_tavus_consent, change):
    service, repository, row = setup_recovery(monkeypatch)
    row['request_payload'].update(change)
    with pytest.raises(AvatarProviderStatusUnavailableError):
        submit(service)
    repository.claim_avatar_submission.assert_not_called()
    assert FakeHTTPClient.last_url is None


def test_already_attempted_request_resumes_even_if_media_is_now_unavailable(monkeypatch):
    service, repository, row = setup_recovery(monkeypatch)
    row['status'] = 'submitted'
    repository.get_avatar_training_request = Mock(return_value=row)
    service._extract_tavus_training_source.side_effect = AssertionError('Attempted job must not revalidate or resubmit')
    result = submit(service)
    assert result.status == 'uploading'
    assert result.external_job_id == f"tavus:pending:{row['job_id']}"
    repository.claim_avatar_submission.assert_not_called()
    assert FakeHTTPClient.last_url is None


@pytest.mark.parametrize('terminal', ['failed', 'cancelled', 'deleted'])
def test_terminal_status_never_contacts_provider(terminal):
    service = AvatarProviderService()
    service._load_tavus_training_state = Mock(return_value=AvatarProviderJobState(
        'tavus:face', 'face', terminal, None))
    service._fetch_tavus_status = AsyncMock()
    result = asyncio.run(service.status('tavus:face'))
    assert result.status == 'failed'
    assert result.external_avatar_id is None
    service._fetch_tavus_status.assert_not_awaited()


def test_submission_storage_and_media_checks_do_not_block_event_loop(monkeypatch, valid_tavus_consent):
    service, repository, row = setup_recovery(monkeypatch)
    main_thread = threading.get_ident()
    workers = []
    def observe(value):
        def call(*args, **kwargs):
            workers.append(threading.get_ident())
            return value
        return call
    repository.get_avatar_training_request = Mock(side_effect=observe(None))
    repository.ensure = Mock(side_effect=observe(None))
    repository.create_training_job = Mock(side_effect=observe(row))
    repository.claim_avatar_submission = Mock(side_effect=observe(True))
    service._extract_tavus_training_source = Mock(side_effect=observe(
        ('train_image_url', row['request_payload']['train_image_url'])))
    assert submit(service).status == 'training'
    assert len(workers) == 5
    assert all(worker != main_thread for worker in workers)
