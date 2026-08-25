from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.models.digital_human_profile import (
    DigitalHumanProfile,
)
from app.services.avatar_state_service import (
    AvatarStateService,
)
from app.services.digital_human_profile_repository import (
    DigitalHumanProfileRepository,
)


def make_profile(
    *,
    status: str = "not_evaluated",
    receipt_id=None,
    verified_at=None,
) -> DigitalHumanProfile:
    now = datetime.now(timezone.utc)

    return DigitalHumanProfile(
        profile_id=uuid4(),
        quality_tier="premium_presence",
        quality_percentage=25,
        avatar_provider="tavus",
        avatar_replica_id="replica",
        avatar_persona_id=None,
        avatar_training_job_id=None,
        avatar_training_status="ready",
        voice_provider="elevenlabs",
        voice_id="voice",
        voice_training_job_id=None,
        voice_training_status="ready",
        approved_portrait_url=None,
        consent_verified=True,
        training_version=1,
        runtime_verified_at=None,
        avatar_ready_at=now,
        voice_ready_at=now,
        last_error_code=None,
        last_error_message=None,
        identity_verification_status=status,
        current_identity_verification_receipt_id=receipt_id,
        identity_verified_at=verified_at,
        metadata={},
        created_at=now,
        updated_at=now,
    )


def test_default_profile_is_not_identity_verified():
    profile = make_profile()

    assert profile.identity_verification_status == "not_evaluated"
    assert profile.has_verified_identity is False


def test_verified_projection_requires_receipt_and_timestamp():
    incomplete = make_profile(
        status="verified",
    )

    complete = make_profile(
        status="verified",
        receipt_id=uuid4(),
        verified_at=datetime.now(timezone.utc),
    )

    assert incomplete.has_verified_identity is False
    assert complete.has_verified_identity is True


def test_state_service_projects_not_evaluated_as_missing():
    service = AvatarStateService()

    result = service._identity_verification_state(
        make_profile()
    )

    assert result.status == "missing"
    assert result.score == 0.0
    assert "not been biometrically" in result.reason


def test_state_service_rejects_verified_without_receipt():
    service = AvatarStateService()

    result = service._identity_verification_state(
        make_profile(
            status="verified",
        )
    )

    assert result.status == "blocked"
    assert result.score == 0.0


def test_state_service_accepts_complete_verified_projection():
    service = AvatarStateService()

    result = service._identity_verification_state(
        make_profile(
            status="verified",
            receipt_id=uuid4(),
            verified_at=datetime.now(timezone.utc),
        )
    )

    assert result.status == "ready"
    assert result.score == 1.0


class FakeRepository:
    def __init__(self, profile):
        self.profile = profile
        self.get_calls = []
        self.job_calls = []

    def get(self, profile_id):
        self.get_calls.append(profile_id)
        return self.profile

    def list_training_jobs(self, profile_id):
        self.job_calls.append(profile_id)
        return []


def test_unified_state_uses_real_repository_methods():
    profile = make_profile()
    repository = FakeRepository(profile)

    state = asyncio.run(
        AvatarStateService().build_state(
            profile_id=str(profile.profile_id),
            repository=repository,
        )
    )

    assert repository.get_calls == [profile.profile_id]
    assert repository.job_calls == [profile.profile_id]
    assert state.identity_verification.status == "missing"
    assert state.consent.status == "ready"


def test_migration_contains_immutable_receipt_authority():
    migration_files = sorted(
        Path("migrations").glob(
            "*_identity_verification_receipts.sql"
        )
    )

    assert len(migration_files) == 1

    source = migration_files[0].read_text(
        encoding="utf-8"
    )

    required = {
        "digital_human_identity_verification_receipts",
        "identity_verification_status",
        "current_identity_verification_receipt_id",
        "identity_verified_at",
        "not_evaluated",
        "rememberme_prevent_identity_receipt_update",
        "status <> 'verified'",
        "face_status = 'verified'",
    }

    for token in required:
        assert token in source


def test_verified_face_requires_model_versioned_evidence():
    repository = object.__new__(
        DigitalHumanProfileRepository
    )

    try:
        repository.append_identity_verification_receipt(
            receipt_id=uuid4(),
            profile_id=uuid4(),
            training_version=1,
            status="verified",
            face_status="verified",
            voice_status="not_required",
            evaluation_version="test-evaluation-v1",
            evaluated_at=datetime.now(
                timezone.utc
            ),
            face_model_version=None,
            face_threshold=None,
            face_score=None,
            evidence={},
        )
    except ValueError as error:
        assert (
            "model-versioned face evaluation"
            in str(error)
        )
        return

    raise AssertionError(
        "Verified identity was accepted without "
        "model-versioned Face evidence."
    )


def test_verified_personalized_voice_requires_speaker_evidence():
    repository = object.__new__(
        DigitalHumanProfileRepository
    )

    try:
        repository.append_identity_verification_receipt(
            receipt_id=uuid4(),
            profile_id=uuid4(),
            training_version=1,
            status="verified",
            face_status="verified",
            voice_status="verified",
            evaluation_version="test-evaluation-v1",
            evaluated_at=datetime.now(
                timezone.utc
            ),
            face_model_version="face-model-v1",
            face_threshold=0.80,
            face_score=0.90,
            voice_model_version=None,
            voice_threshold=None,
            voice_score=None,
            evidence={},
        )
    except ValueError as error:
        assert (
            "model-versioned speaker evaluation"
            in str(error)
        )
        return

    raise AssertionError(
        "Verified personalized Voice was accepted without "
        "model-versioned speaker evidence."
    )


def test_invalid_verified_receipts_fail_before_database_access():
    repository = object.__new__(
        DigitalHumanProfileRepository
    )

    assert not hasattr(
        repository,
        "database_url",
    )

    try:
        repository.append_identity_verification_receipt(
            receipt_id=uuid4(),
            profile_id=uuid4(),
            training_version=1,
            status="verified",
            face_status="not_evaluated",
            voice_status="not_required",
            evaluation_version="test-evaluation-v1",
            evaluated_at=datetime.now(
                timezone.utc
            ),
            evidence={},
        )
    except ValueError as error:
        assert (
            "requires verified face output"
            in str(error)
        )
        return

    raise AssertionError(
        "Invalid verified receipt reached persistence validation."
    )
