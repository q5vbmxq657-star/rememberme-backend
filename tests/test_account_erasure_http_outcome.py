import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.routes.auth import delete_account


@pytest.mark.parametrize('outcome,code', [('completed', 204), ('deletion_pending', 202), (None, 503)])
def test_account_http_outcome(outcome, code):
    principal = SimpleNamespace(user=SimpleNamespace(user_id=uuid4()))
    with patch('app.routes.auth.AccountErasureService') as factory:
        factory.return_value.erase_account = AsyncMock(return_value=outcome)
        if code == 503:
            with pytest.raises(HTTPException) as error:
                asyncio.run(delete_account(principal))
            assert error.value.status_code == 503
        else:
            response = asyncio.run(delete_account(principal))
            assert response.status_code == code
            if code == 202:
                assert response.body == b'{"status":"deletion_pending"}'


def test_unaccepted_account_request_is_unavailable():
    principal = SimpleNamespace(user=SimpleNamespace(user_id=uuid4()))
    with patch('app.routes.auth.AccountErasureService') as factory:
        factory.return_value.erase_account = AsyncMock(side_effect=RuntimeError('database unavailable'))
        with pytest.raises(HTTPException) as error:
            asyncio.run(delete_account(principal))
        assert error.value.status_code == 503
