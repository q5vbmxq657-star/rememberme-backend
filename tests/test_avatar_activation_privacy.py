import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from unittest.mock import Mock

import psycopg
from psycopg.conninfo import conninfo_to_dict
import pytest

from app.schemas.profile_consent import CONSENT_POLICY_VERSION
from app.services.avatar_provider_service import AvatarProviderService, AvatarProviderJobState
from app.services.digital_human_profile_repository import DigitalHumanProfileRepository, StaleAvatarTrainingError
from test_tavus_face_training_contract import FakeRepository, FakeHTTPClient, valid_tavus_consent


@pytest.fixture
def avatar_job():
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated AP1 PostgreSQL')
    assert conninfo_to_dict(url).get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    profile = uuid4()
    repository = DigitalHumanProfileRepository(url)
    repository.ensure(profile)
    job_id = uuid4()
    repository.create_training_job(job_id=job_id, profile_id=profile, training_type='avatar',
        provider='tavus', status='training', training_version=1, idempotency_key=str(uuid4()),
        request_payload={'train_image_url': 'https://stay.example/photo', '_stay_consent_revision': 1})
    repository.update_training_job(job_id, status='training', provider_job_id='tavus:test-face')
    with psycopg.connect(url) as connection:
        connection.execute('INSERT INTO profile_purpose_consents (profile_id, revision, policy_version, purposes) VALUES (%s,1,%s,%s)',
            (profile, CONSENT_POLICY_VERSION, ['photo_likeness', 'provider_processing']))
        connection.execute("UPDATE digital_human_profiles SET avatar_training_job_id='tavus:test-face', avatar_training_status='training' WHERE profile_id=%s", (profile,))
    try:
        yield url, repository, profile, job_id
    finally:
        with psycopg.connect(url) as connection:
            connection.execute('DELETE FROM digital_human_profile_erasure_requests WHERE profile_id=%s', (profile,))
            connection.execute('DELETE FROM digital_human_profiles WHERE profile_id=%s', (profile,))


def activate(repository, profile):
    return repository.set_avatar_training(profile, provider='tavus', status='ready',
        provider_job_id='tavus:test-face', replica_id='test-face', expected_provider_job_id='tavus:test-face')


@pytest.mark.parametrize('mutation', ['revoked', 'regranted', 'erased', 'legacy', 'wrong_scope', 'newer_job', 'foreign_id'])
def test_late_avatar_activation_fails_closed_without_profile_mutation(avatar_job, mutation):
    url, repository, profile, job_id = avatar_job
    with psycopg.connect(url) as connection:
        if mutation == 'revoked':
            connection.execute('UPDATE profile_purpose_consents SET purposes=%s WHERE profile_id=%s', ([], profile))
        elif mutation == 'regranted':
            connection.execute('UPDATE profile_purpose_consents SET revision=2 WHERE profile_id=%s', (profile,))
        elif mutation == 'erased':
            connection.execute('INSERT INTO digital_human_profile_erasure_requests (request_id,profile_id,idempotency_key) VALUES (%s,%s,%s)', (uuid4(), profile, str(uuid4())))
        elif mutation == 'legacy':
            connection.execute("UPDATE digital_human_training_jobs SET request_payload=request_payload-'_stay_consent_revision' WHERE job_id=%s", (job_id,))
        elif mutation == 'wrong_scope':
            connection.execute('UPDATE profile_purpose_consents SET purposes=%s WHERE profile_id=%s', (['video_motion','provider_processing'], profile))
        elif mutation == 'foreign_id':
            connection.execute("UPDATE digital_human_profiles SET avatar_training_job_id='tavus:new-face' WHERE profile_id=%s", (profile,))
    if mutation == 'newer_job':
        repository.create_training_job(job_id=uuid4(), profile_id=profile, training_type='avatar', provider='tavus',
            status='created', training_version=2, idempotency_key=str(uuid4()), request_payload={})
    before = repository.require(profile)
    with pytest.raises(StaleAvatarTrainingError):
        activate(repository, profile)
    assert repository.require(profile) == before


@pytest.mark.parametrize('source', ['photo', 'video'])
def test_current_avatar_job_can_activate_with_matching_provenance(avatar_job, source):
    url, repository, profile, job_id = avatar_job
    if source == 'video':
        with psycopg.connect(url) as connection:
            connection.execute('UPDATE digital_human_training_jobs SET request_payload=%s::jsonb WHERE job_id=%s',
                (json.dumps({'train_video_url':'https://stay.example/video', '_stay_consent_revision':1}), job_id))
            connection.execute('UPDATE profile_purpose_consents SET purposes=%s WHERE profile_id=%s',
                (['video_motion', 'voice_synthesis', 'provider_processing'], profile))
    assert activate(repository, profile).avatar_training_status == 'ready'


def test_webhook_persists_job_but_does_not_activate_after_revocation(avatar_job):
    url, repository, profile, job_id = avatar_job
    with psycopg.connect(url) as connection:
        connection.execute('UPDATE profile_purpose_consents SET revision=2 WHERE profile_id=%s', (profile,))
    service = AvatarProviderService()
    service._profile_repository = repository
    service._sync_tavus_status_to_profile(state=AvatarProviderJobState(external_job_id='tavus:test-face',
        external_avatar_id='test-face', status='ready', preview_url=None, error_message=None),
        provider_payload={'face_id':'test-face', 'status':'ready'})
    assert repository.require(profile).avatar_training_status == 'training'
    assert repository.get_training_job_by_provider_job_id(provider='tavus', provider_job_id='tavus:test-face')['status'] == 'ready'


def test_avatar_activation_serializes_with_consent_lock(avatar_job):
    url, repository, profile, _ = avatar_job
    with ThreadPoolExecutor(max_workers=1) as executor:
        with psycopg.connect(url) as connection:
            connection.execute('SELECT profile_id FROM digital_human_profiles WHERE profile_id=%s FOR UPDATE', (profile,))
            connection.execute('UPDATE profile_purpose_consents SET revision=2 WHERE profile_id=%s', (profile,))
            future = executor.submit(activate, repository, profile)
            connection.commit()
        with pytest.raises(StaleAvatarTrainingError):
            future.result(timeout=10)
    assert repository.require(profile).avatar_training_status == 'training'


def test_consent_revision_is_saved_in_job_only(monkeypatch, valid_tavus_consent):
    import app.services.avatar_provider_service as module
    monkeypatch.setenv('TAVUS_API_KEY', 'contract-key')
    monkeypatch.setattr(module.httpx, 'AsyncClient', FakeHTTPClient)
    service = AvatarProviderService()
    monkeypatch.setattr(service, '_extract_tavus_training_source',
        lambda package, **kwargs: ('train_image_url', 'https://stay.example/owned-photo-lease'))
    repository = FakeRepository()
    service._profile_repository = repository
    asyncio.run(service.submit(provider='tavus', profile_id=str(uuid4()), package_record_id=str(uuid4()),
        package={'train_image_url':'https://stay.example/photo', 'voice_name':'james'}))
    assert repository.created['request_payload']['_stay_consent_revision'] == 11
    assert '_stay_consent_revision' not in FakeHTTPClient.last_payload
