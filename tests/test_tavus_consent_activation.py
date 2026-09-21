from uuid import uuid4
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

from app.services.avatar_provider_service import AvatarProviderService


def test_revoked_consent_preserves_external_resource_without_activating_it():
    service = AvatarProviderService()
    repository = Mock()
    service._profile_repository = repository
    authorize = Mock(side_effect=HTTPException(status_code=403))
    job_id = uuid4()

    with pytest.raises(HTTPException):
        service._mark_tavus_training_submitted(
            job_id=job_id,
            profile_id=uuid4(),
            provider_job_id="tavus:face-owned",
            replica_id="face-owned",
            provider_payload={"face_id": "face-owned"},
            authorize=authorize,
        )

    repository.update_training_job.assert_called_once_with(
        job_id,
        status="training",
        provider_job_id="tavus:face-owned",
        provider_payload={"face_id": "face-owned"},
    )
    authorize.assert_called_once_with()
    repository.set_avatar_training.assert_not_called()
