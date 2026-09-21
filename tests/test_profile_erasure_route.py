import asyncio
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routes.profile_erasure import delete_profile
from app.services.profile_erasure_access import ErasureAccessDenied


@pytest.mark.parametrize('status,expected', [('completed', 204), ('retryable_failed', 503), ('database_cleanup', 503)])
def test_delete_success_requires_persisted_completion(status, expected):
    service = Mock()
    service._run = AsyncMock()
    service.repository.get_profile_erasure_request.return_value = {'status': status}
    with patch('app.routes.profile_erasure.ProfileErasureAccess') as access:
        access.return_value.authorize_request.return_value = {'request_id': uuid4()}
        if expected == 204:
            assert asyncio.run(delete_profile(uuid4(), Mock(), service)).status_code == 204
        else:
            with pytest.raises(HTTPException) as error:
                asyncio.run(delete_profile(uuid4(), Mock(), service))
            assert error.value.status_code == expected


def test_foreign_profile_does_not_run_erasure():
    service = Mock()
    service._run = AsyncMock()
    with patch('app.routes.profile_erasure.ProfileErasureAccess') as access:
        access.return_value.authorize_request.side_effect = ErasureAccessDenied()
        with pytest.raises(HTTPException) as error:
            asyncio.run(delete_profile(uuid4(), Mock(), service))
        assert error.value.status_code == 404
        assert error.value.detail == 'Profile not found.'
    service._run.assert_not_called()
