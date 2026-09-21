import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import psycopg
from fastapi import HTTPException

from app.routes import avatar_provider as route
from app.schemas.avatar_provider import AvatarProviderSubmitRequest
from app.services.avatar_provider_service import AvatarProviderJobState, AvatarProviderStatusUnavailableError
from app.services.digital_human_profile_repository import DigitalHumanProfileRepositoryError


@pytest.fixture
def setup(monkeypatch):
    profile = uuid4()
    state = AvatarProviderJobState('tavus:test', 'face_test', 'training', None)
    service = SimpleNamespace(submit=AsyncMock(return_value=state),status=AsyncMock(return_value=state),
        require_training_job_profile_id=Mock(return_value=profile))
    guard = Mock()
    monkeypatch.setattr(route,'avatar_provider_service',service)
    monkeypatch.setattr(route,'require_profile_access',guard)
    def invoke(kind):
        if kind == 'submit':
            return asyncio.run(route.submit_avatar_provider_job(AvatarProviderSubmitRequest(
                provider='tavus',profile_id=str(profile),package_record_id='package',package={}),principal=object()))
        return asyncio.run(route.get_avatar_provider_job_status('tavus:test',profile_id=profile,principal=object()))
    return service,guard,state,invoke


@pytest.mark.parametrize('kind',['submit','status'])
@pytest.mark.parametrize('error_type',[AvatarProviderStatusUnavailableError,DigitalHumanProfileRepositoryError])
def test_unavailability_is_503_not_failed_job(setup,kind,error_type):
    service,guard,state,invoke=setup
    getattr(service,kind).side_effect=error_type('private upstream detail')
    with pytest.raises(HTTPException) as raised:
        invoke(kind)
    assert raised.value.status_code==503
    assert 'private' not in raised.value.detail


@pytest.mark.parametrize('kind',['submit','status'])
@pytest.mark.parametrize('status',[None,'','completed','totally_unknown',42])
def test_malformed_status_is_not_successful_response(setup,kind,status):
    service,guard,state,invoke=setup
    state.status=status
    with pytest.raises(HTTPException) as raised:
        invoke(kind)
    assert raised.value.status_code==503


@pytest.mark.parametrize('kind',['submit','status'])
def test_revoked_access_after_await_blocks_response(setup,kind):
    service,guard,state,invoke=setup
    async def revoke(**kwargs):
        guard.side_effect=HTTPException(404,'Profile not found.')
        return state
    async def revoke_status(*args):
        return await revoke()
    getattr(service,kind).side_effect=revoke if kind=='submit' else revoke_status
    with pytest.raises(HTTPException) as raised:
        invoke(kind)
    assert raised.value.status_code==404


def test_job_owner_change_during_status_blocks_response(setup):
    service,guard,state,invoke=setup
    async def changed(*args):
        service.require_training_job_profile_id.return_value=uuid4()
        return state
    service.status.side_effect=changed
    with pytest.raises(HTTPException) as raised:
        invoke('status')
    assert raised.value.status_code==404


def test_resolved_handle_must_belong_to_same_profile(setup):
    service,guard,state,invoke=setup
    owner=service.require_training_job_profile_id.return_value
    state.external_job_id='tavus:resolved'
    service.require_training_job_profile_id.side_effect=lambda job: owner if job=='tavus:test' else uuid4()
    with pytest.raises(HTTPException) as raised:
        invoke('status')
    assert raised.value.status_code==404


def test_submit_result_must_belong_to_requested_profile(setup):
    service,guard,state,invoke=setup
    service.require_training_job_profile_id.return_value=uuid4()
    with pytest.raises(HTTPException) as raised:
        invoke('submit')
    assert raised.value.status_code==404


@pytest.mark.parametrize('kind',['submit','status'])
def test_owner_lookup_outage_is_sanitized_unavailable(setup,kind):
    service,guard,state,invoke=setup
    service.require_training_job_profile_id.side_effect=AvatarProviderStatusUnavailableError('private detail')
    with pytest.raises(HTTPException) as raised:
        invoke(kind)
    assert raised.value.status_code==503
    assert 'private' not in raised.value.detail


@pytest.mark.parametrize('kind',['submit','status'])
@pytest.mark.parametrize('status',['uploading','training','ready','failed','generatingPreview'])
def test_canonical_states_preserved(setup,kind,status):
    service,guard,state,invoke=setup
    state.status=status
    if status=='failed':
        state.error_message='The material could not be used.'
    response=invoke(kind)
    assert response.status==status
    assert response.error_message==state.error_message


@pytest.mark.parametrize('error_type', [psycopg.OperationalError, psycopg.DatabaseError])
@pytest.mark.parametrize('boundary', ['submit', 'status', 'submit_owner', 'status_owner_before',
                                     'status_owner_after', 'status_resolved_owner'])
def test_raw_database_failures_are_sanitized_503(setup, error_type, boundary):
    service, guard, state, invoke = setup
    failure = error_type('private database host password and SQL details')
    kind = 'submit' if boundary.startswith('submit') else 'status'
    if boundary in {'submit', 'status'}:
        getattr(service, boundary).side_effect = failure
    elif boundary == 'status_owner_after':
        owner = service.require_training_job_profile_id.return_value
        service.require_training_job_profile_id.side_effect = [owner, failure]
    elif boundary == 'status_resolved_owner':
        owner = service.require_training_job_profile_id.return_value
        state.external_job_id = 'tavus:resolved'
        service.require_training_job_profile_id.side_effect = [owner, owner, failure]
    else:
        service.require_training_job_profile_id.side_effect = failure
    with pytest.raises(HTTPException) as raised:
        invoke(kind)
    assert raised.value.status_code == 503
    assert 'private' not in raised.value.detail
    assert 'password' not in raised.value.detail
    assert raised.value.__cause__ is failure
