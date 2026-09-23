import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg.conninfo import conninfo_to_dict

from app.schemas.profile_consent import CONSENT_POLICY_VERSION
from app.services.digital_human_profile_repository import DigitalHumanProfileRepository, StaleVoiceTrainingError


@pytest.fixture
def voice_scope():
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated PostgreSQL')
    assert conninfo_to_dict(url).get('host', '').startswith('/private/tmp/STAY-AP1-PG-')
    repo = DigitalHumanProfileRepository(url)
    profile = uuid4()
    repo.ensure(profile, consent_verified=True)
    with psycopg.connect(url) as db:
        db.execute("UPDATE digital_human_profiles SET voice_provider='elevenlabs',voice_id='voice-A',voice_training_status='ready',voice_training_job_id=%s,voice_ready_at=NOW() WHERE profile_id=%s", (str(uuid4()), profile))
        db.execute('INSERT INTO profile_purpose_consents(profile_id,revision,policy_version,purposes) VALUES (%s,1,%s,%s)', (profile, CONSENT_POLICY_VERSION, ['voice_synthesis', 'provider_processing']))
    try:
        yield repo, profile, url
    finally:
        with psycopg.connect(url) as db:
            db.execute('DELETE FROM digital_human_profile_erasure_requests WHERE profile_id=%s', (profile,))
            db.execute('DELETE FROM digital_human_profiles WHERE profile_id=%s', (profile,))


def create(scope):
    repo, profile, _ = scope
    job = uuid4()
    repo.create_training_job(job_id=job, profile_id=profile, training_type='voice', provider='elevenlabs', status='created', training_version=1, idempotency_key=str(job), request_payload={})
    return job


def result(scope, job, status='ready', voice_id='voice-B'):
    repo, profile, _ = scope
    return repo.apply_voice_training_result(profile_id=profile, job_id=job, status=status, voice_id=voice_id)


def test_concurrent_begin_has_single_submission_owner(voice_scope):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: repo.begin_voice_training(profile, job, 1), range(4)))
    assert sum(r['submission_claimed'] for r in claims) == 1
    assert repo.require(profile).voice_id == 'voice-A'
    assert repo.require(profile).voice_training_status == 'ready'


@pytest.mark.parametrize('status', ['failed', 'submitted', 'verification_required'])
def test_pending_or_failed_preserves_active_voice(voice_scope, status):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    assert not result(voice_scope, job, status)['voice_activated']
    assert repo.require(profile).voice_id == 'voice-A'
    assert repo.require(profile).voice_training_status == 'ready'
    active, pending = repo.get_voice_status_snapshot(profile)
    assert active['voice_id'] == 'voice-A'
    assert pending['status'] == status


@pytest.mark.parametrize('c_status', ['ready', 'failed', 'submitted'])
def test_latest_selection_wins_over_delayed_b(voice_scope, c_status):
    repo, profile, _ = voice_scope
    b = create(voice_scope)
    repo.begin_voice_training(profile, b, 1)
    c = create(voice_scope)
    repo.begin_voice_training(profile, c, 1)
    result(voice_scope, c, c_status, 'voice-C')
    assert not result(voice_scope, b)['voice_activated']
    assert repo.get_training_job(b)['provider_payload'].get('_stay_activated') is not True
    assert repo.require(profile).voice_id == ('voice-C' if c_status == 'ready' else 'voice-A')


def test_actual_activation_history_is_retained_for_running_calls(voice_scope):
    repo, profile, _ = voice_scope
    first = create(voice_scope)
    repo.begin_voice_training(profile, first, 1)
    assert result(voice_scope, first, voice_id='voice-first')['voice_activated']
    second = create(voice_scope)
    repo.begin_voice_training(profile, second, 1)
    assert result(voice_scope, second, voice_id='voice-second')['voice_activated']
    assert repo.get_training_job(first)['provider_payload']['_stay_activated'] is True
    assert repo.get_training_job(second)['provider_payload']['_stay_activated'] is True


@pytest.mark.parametrize('code,allowed', [('provider_http_429', True), ('provider_http_422_invalid_sample', True),
    ('provider_http_503', False), ('provider_http_200', False), ('transport_timeout', False), ('', False)])
def test_retry_ui_contract_matches_safe_provider_rejection(code, allowed):
    job = {'training_type': 'voice', 'provider': 'elevenlabs', 'status': 'failed',
        'provider_job_id': None, 'error_code': code}
    assert DigitalHumanProfileRepository.voice_training_retry_allowed(job) is allowed
    job['provider_job_id'] = 'known-voice'
    assert not DigitalHumanProfileRepository.voice_training_retry_allowed(job)


@pytest.mark.parametrize('mutation', ['revision', 'scope', 'erasure', 'selection', 'legacy', 'profile_consent'])
def test_stale_or_revoked_result_keeps_cleanup_identity(voice_scope, mutation):
    repo, profile, url = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    with psycopg.connect(url) as db:
        if mutation == 'profile_consent':
            db.execute('UPDATE digital_human_profiles SET consent_verified=FALSE WHERE profile_id=%s', (profile,))
        elif mutation == 'revision':
            db.execute('UPDATE profile_purpose_consents SET revision=2 WHERE profile_id=%s', (profile,))
        elif mutation == 'scope':
            db.execute('UPDATE profile_purpose_consents SET purposes=%s WHERE profile_id=%s', (['provider_processing'], profile))
        elif mutation == 'erasure':
            db.execute('INSERT INTO digital_human_profile_erasure_requests(request_id,profile_id,idempotency_key) VALUES(%s,%s,%s)', (uuid4(), profile, str(uuid4())))
        elif mutation == 'selection':
            db.execute('UPDATE digital_human_profiles SET voice_id=NULL WHERE profile_id=%s', (profile,))
        else:
            db.execute("UPDATE digital_human_training_jobs SET request_payload='{}'::jsonb WHERE job_id=%s", (job,))
    ack = result(voice_scope, job)
    assert not ack['voice_activated']
    assert ack['provider_job_id'] == 'voice-B'
    assert repo.require(profile).voice_id != 'voice-B'


def test_terminal_failure_retains_late_identity_without_activation(voice_scope):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    result(voice_scope, job, 'failed', None)
    ack = result(voice_scope, job)
    assert ack['status'] == 'failed'
    assert ack['provider_job_id'] == 'voice-B'
    assert not ack['voice_activated']
    assert repo.require(profile).voice_id == 'voice-A'


def test_ready_never_regresses_and_wrong_identity_is_rejected(voice_scope):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    assert result(voice_scope, job)['voice_activated']
    assert result(voice_scope, job, 'submitted')['status'] == 'ready'
    assert result(voice_scope, job, 'failed')['status'] == 'ready'
    assert repo.require(profile).voice_id == 'voice-B'
    with pytest.raises(StaleVoiceTrainingError):
        result(voice_scope, job, voice_id='wrong')


def test_missing_profile_snapshot():
    url = os.getenv('STAY_TEST_DATABASE_URL')
    if not url:
        pytest.skip('Requires isolated PostgreSQL')
    assert DigitalHumanProfileRepository(url).get_voice_status_snapshot(uuid4()) == (None, None)


def test_failed_retry_claim_is_atomic(voice_scope):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    repo.apply_voice_training_result(profile_id=profile, job_id=job, status='failed', error_code='provider_http_429')
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: repo.restart_failed_voice_training_job(job_id=job, profile_id=profile), range(4)))
    assert sum(r is not None for r in claims) == 1
    assert repo.begin_voice_training(profile, job, 1)['submission_claimed']
    assert repo.require(profile).voice_id == 'voice-A'


@pytest.mark.parametrize('code', [400, 401, 403, 404, 413, 415, 422, 429])
@pytest.mark.parametrize('suffix', ['', '_unknown', '_voice_limit_reached', '_provider-code_2'])
def test_retry_allows_only_definite_provider_rejections(voice_scope, code, suffix):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    repo.apply_voice_training_result(profile_id=profile, job_id=job, status='failed',
                                    error_code=f'provider_http_{code}{suffix}')
    assert repo.restart_failed_voice_training_job(job_id=job, profile_id=profile)['status'] == 'created'


@pytest.mark.parametrize('error_code', [
    'provider_http_500', 'provider_http_502', 'provider_http_503',
    'provider_http_302', 'provider_http_408', 'provider_http_409',
    'provider_http_500_unknown', 'provider_http_408_timeout', 'provider_http_409_conflict',
    'provider_http_302_redirect', 'provider_http_4000', 'provider_http_4000_unknown',
    'provider_http_400_', 'provider_http_400_invalid.code',
    'prefix_provider_http_400_unknown', 'provider_http_400_unknown\n',
    'provider_http_400_' + 'a' * 65, 'provider_http_499', None,
])
def test_legacy_ambiguous_failure_cannot_be_replayed(voice_scope, error_code):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    repo.apply_voice_training_result(profile_id=profile, job_id=job, status='failed', error_code=error_code)
    before = repo.get_voice_status_snapshot(profile)
    assert repo.restart_failed_voice_training_job(job_id=job, profile_id=profile) is None
    assert repo.get_voice_status_snapshot(profile) == before


def test_delayed_begin_cannot_submit_older_selection(voice_scope):
    repo, profile, _ = voice_scope
    b = create(voice_scope)
    create(voice_scope)
    with pytest.raises(StaleVoiceTrainingError):
        repo.begin_voice_training(profile, b, 1)


def test_verification_then_ready_activates_once(voice_scope):
    repo, profile, _ = voice_scope
    job = create(voice_scope)
    repo.begin_voice_training(profile, job, 1)
    result(voice_scope, job, 'verification_required')
    assert result(voice_scope, job)['voice_activated']
    before = repo.require(profile)
    assert result(voice_scope, job)['voice_activated']
    assert repo.require(profile) == before
