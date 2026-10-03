import base64
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest
from app.services.digital_human_profile_repository import StaleVoiceTrainingError, DigitalHumanProfileNotFoundError
from app.services.voice_reference_repository import VoiceReferenceRepository
from app.services.elevenlabs_voice_service import (
    ElevenLabsVoiceService, VoiceCloneSample, ElevenLabsVoiceProviderError, ElevenLabsVoiceConflictError,
)
from app.services.self_hosted_voice_client import SelfHostedVoiceUnavailableError
from app.services.self_hosted_voice_provider import SelfHostedVoiceProvider
from app.services.profile_consent_repository import ProfileConsentRepository
from app.schemas.profile_consent import PurposeConsentUpdate, CONSENT_POLICY_VERSION
from test_self_hosted_voice_runtime import audio_bytes, payload
from test_voice_activation_atomicity import voice_scope, create, result


@pytest.fixture
def reference_scope(voice_scope):
    repository, profile, url = voice_scope
    with psycopg.connect(url) as db:
        if db.execute("SELECT to_regclass('self_hosted_voice_references')").fetchone()[0] is None:
            db.execute((Path(__file__).parents[1] / 'migrations/038_self_hosted_voice_references.sql').read_text())
    job = create(voice_scope, 'stay_voice')
    repository.begin_voice_training(profile, job, 1)
    store = VoiceReferenceRepository(repository, keys={'test-v1': base64.b64encode(b'x' * 32).decode()},
                                     active_key_id='test-v1')
    request = SelfHostedVoiceRequest.model_validate_json(json.dumps({**payload(),
        'profile_id': str(profile), 'voice_version': str(job)}))
    return voice_scope, store, request


def test_reference_is_encrypted_and_only_readable_after_activation(reference_scope):
    scope, store, request = reference_scope
    store.put(request)
    store.put(request)
    with psycopg.connect(scope[2]) as db:
        cipher = bytes(db.execute('SELECT ciphertext FROM self_hosted_voice_references WHERE job_id=%s',
                                  (request.voice_version,)).fetchone()[0])
    assert audio_bytes() not in cipher
    with pytest.raises(StaleVoiceTrainingError):
        store.get(profile_id=request.profile_id, job_id=request.voice_version)
    assert result(scope, request.voice_version, voice_id=str(request.voice_version))['voice_activated']
    assert store.get(profile_id=request.profile_id, job_id=request.voice_version)[0] == audio_bytes()


def test_release_preflight_reads_retained_keys_and_activated_models(reference_scope):
    from app.services.voice_release_preflight import stored_requirements
    scope, store, request = reference_scope
    store.put(request)
    keys, _ = stored_requirements(scope[0])
    assert 'test-v1' in keys
    result(scope, request.voice_version, voice_id=str(request.voice_version))
    keys, revisions = stored_requirements(scope[0])
    assert 'test-v1' in keys
    assert request.model_revision in revisions


def test_reference_cannot_move_to_another_profile_or_revision(reference_scope):
    scope, store, request = reference_scope
    store.put(request)
    with pytest.raises(DigitalHumanProfileNotFoundError):
        store.put(request.model_copy(update={'profile_id': uuid4()}))
    with psycopg.connect(scope[2]) as db:
        db.execute('UPDATE self_hosted_voice_references SET model_revision=%s WHERE job_id=%s',
                   ('b' * 64, request.voice_version))
    result(scope, request.voice_version, voice_id=str(request.voice_version))
    with pytest.raises(ValueError, match='authenticated'):
        store.get(profile_id=request.profile_id, job_id=request.voice_version)


def test_delete_is_idempotent_and_blocks_late_write_and_activation(reference_scope):
    scope, store, request = reference_scope
    store.put(request)
    result(scope, request.voice_version, voice_id=str(request.voice_version))
    for _ in range(2):
        store.delete(scope[0], profile_id=request.profile_id, job_id=request.voice_version)
    assert scope[0].require(request.profile_id).voice_id is None
    with pytest.raises(StaleVoiceTrainingError):
        store.put(request)
    assert not result(scope, request.voice_version, voice_id=str(request.voice_version))['voice_activated']
    with psycopg.connect(scope[2]) as db:
        assert db.execute('SELECT count(*) FROM self_hosted_voice_references WHERE job_id=%s',
                          (request.voice_version,)).fetchone()[0] == 0


def test_revocation_blocks_reference_reads(reference_scope):
    scope, store, request = reference_scope
    store.put(request)
    result(scope, request.voice_version, voice_id=str(request.voice_version))
    with psycopg.connect(scope[2]) as db:
        db.execute('UPDATE profile_purpose_consents SET revision=revision+1 WHERE profile_id=%s',
                   (request.profile_id,))
    with pytest.raises(StaleVoiceTrainingError):
        store.get(profile_id=request.profile_id, job_id=request.voice_version)


def test_deleting_historical_reference_preserves_replacement(reference_scope):
    scope, store, request = reference_scope
    store.put(request)
    result(scope, request.voice_version, voice_id=str(request.voice_version))
    replacement = create(scope, 'elevenlabs')
    scope[0].begin_voice_training(request.profile_id, replacement, 1)
    result(scope, replacement, voice_id='new-voice')
    store.delete(scope[0], profile_id=request.profile_id, job_id=request.voice_version)
    assert scope[0].require(request.profile_id).voice_id == 'new-voice'


def test_authenticated_revocation_deletes_reference_and_deactivates_voice(reference_scope):
    scope, store, request = reference_scope
    store.put(request)
    result(scope, request.voice_version, voice_id=str(request.voice_version))
    user, session = uuid4(), uuid4()
    with psycopg.connect(scope[2]) as db:
        db.execute('INSERT INTO users(user_id) VALUES (%s)', (user,))
        db.execute("INSERT INTO profile_memberships(membership_id,user_id,profile_id,role) VALUES (%s,%s,%s,'owner')",
                   (uuid4(),user,request.profile_id))
        db.execute("""INSERT INTO user_sessions(session_id,user_id,refresh_token_hash,access_expires_at,refresh_expires_at)
            VALUES (%s,%s,%s,NOW()+INTERVAL '1 hour',NOW()+INTERVAL '1 day')""", (session,user,str(uuid4())))
    try:
        ProfileConsentRepository(scope[2]).update(profile_id=request.profile_id, user_id=user, session_id=session,
            update=PurposeConsentUpdate(expected_revision=1, policy_version=CONSENT_POLICY_VERSION,
                purposes=[], understands_ai_disclosure=True, understands_revocation=True))
        assert not scope[0].require(request.profile_id).has_personalized_voice
        assert scope[0].get_training_job(request.voice_version)['status'] == 'deleted'
        with psycopg.connect(scope[2]) as db:
            assert db.execute('SELECT count(*) FROM self_hosted_voice_references WHERE profile_id=%s',
                              (request.profile_id,)).fetchone()[0] == 0
    finally:
        with psycopg.connect(scope[2]) as db:
            db.execute('DELETE FROM users WHERE user_id=%s', (user,))


@pytest.mark.parametrize('fail_first', [False, True])
def test_existing_gateway_prepares_activates_plays_and_deletes_own_voice(reference_scope, monkeypatch, fail_first):
    scope, store, request = reference_scope
    client = SimpleNamespace(synthesize=AsyncMock(return_value=audio_bytes()))
    monkeypatch.setenv('STAY_VOICE_MODEL_REVISION', request.model_revision)
    provider = SelfHostedVoiceProvider(scope[0], client=client, references=store)
    monkeypatch.setattr('app.services.self_hosted_voice_provider.SelfHostedVoiceProvider', lambda _: provider)
    grant = Mock(return_value=SimpleNamespace(revision=1))
    monkeypatch.setattr('app.services.elevenlabs_voice_service.require_profile_purposes', grant)
    monkeypatch.setattr('app.services.self_hosted_voice_provider.require_profile_purposes', grant)
    service = object.__new__(ElevenLabsVoiceService)
    service.repository = scope[0]
    service.training_provider = 'stay_voice'
    service._validate_sample_audio = Mock()
    arguments = dict(profile_id=request.profile_id, display_name='Test',
        samples=[VoiceCloneSample('sample.wav', 'audio/wav', audio_bytes())], consent_verified=True,
        remove_background_noise=True, idempotency_key=str(uuid4()))
    if fail_first:
        client.synthesize.side_effect = SelfHostedVoiceUnavailableError('test capacity failure')
        with pytest.raises(ElevenLabsVoiceProviderError):
            asyncio.run(service.clone_voice(**arguments))
        assert scope[0].require(request.profile_id).voice_id == 'voice-A'
        assert service.status_for_profile(request.profile_id)['pending_training']['retry_allowed'] is True
        client.synthesize.side_effect = None
    trained = asyncio.run(service.clone_voice(**arguments))
    assert trained.status == 'ready'
    assert scope[0].require(request.profile_id).voice_provider == 'stay_voice'
    speech = asyncio.run(service.synthesize_for_profile(profile_id=request.profile_id, text='Hello.',
        voice_version=str(trained.job_id), language='en'))
    assert speech.media_type == 'audio/wav'
    assert speech.audio_stream.read() == audio_bytes()
    assert client.synthesize.await_count == (3 if fail_first else 2)
    async def delete_during_inference(_):
        store.delete_profile(scope[0], profile_id=request.profile_id)
        return audio_bytes()
    client.synthesize.side_effect = delete_during_inference
    with pytest.raises(ElevenLabsVoiceConflictError):
        asyncio.run(service.synthesize_for_profile(profile_id=request.profile_id, text='Hello.',
            voice_version=str(trained.job_id), language='en'))
    asyncio.run(service.delete_profile_voice(profile_id=request.profile_id))
    assert not scope[0].require(request.profile_id).has_personalized_voice
    with psycopg.connect(scope[2]) as db:
        assert db.execute('SELECT count(*) FROM self_hosted_voice_references WHERE profile_id=%s',
                          (request.profile_id,)).fetchone()[0] == 0
