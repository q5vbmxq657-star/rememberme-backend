from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest

from fastapi import HTTPException

import app.routes.avatar_state as avatar_state_route
from app.schemas.avatar_state import (
    AvatarStateDimension,
    AvatarUnifiedStateResponse,
)


def make_request():
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace()
        ),
        base_url=(
            "https://api.example.test/"
        ),
    )


def make_state(
    *,
    profile_id: str,
    profile_loaded: bool,
    repository_attached: bool = True,
):
    dimension = AvatarStateDimension(
        status="missing",
        score=0.0,
        label="Test",
        reason="Test",
        evidence=[],
        next_action=None,
    )

    return AvatarUnifiedStateResponse(
        profile_id=profile_id,
        overall_status="needs_training",
        readiness_score=0.0,
        can_start_runtime=False,
        next_best_action="Add evidence.",
        face_video=dimension,
        voice=dimension,
        memory=dimension,
        behavior=dimension,
        consent=dimension,
        runtime=dimension,
        identity_verification=dimension,
        source_integrity={
            "profile_loaded": profile_loaded,
            "repository_attached": (
                repository_attached
            ),
        },
    )


def test_existing_repository_is_reused():
    request = make_request()
    existing = object()

    request.app.state.digital_human_profile_repository = (
        existing
    )

    resolved = (
        avatar_state_route
        .resolve_digital_human_profile_repository(
            request
        )
    )

    assert resolved is existing


def test_new_repository_is_bound_once(
    monkeypatch,
):
    request = make_request()
    repository = object()
    constructor_calls = []

    def build_repository():
        constructor_calls.append(True)
        return repository

    monkeypatch.setattr(
        avatar_state_route,
        "DigitalHumanProfileRepository",
        build_repository,
    )

    first = (
        avatar_state_route
        .resolve_digital_human_profile_repository(
            request
        )
    )

    second = (
        avatar_state_route
        .resolve_digital_human_profile_repository(
            request
        )
    )

    assert first is repository
    assert second is repository
    assert constructor_calls == [True]


def test_invalid_profile_id_returns_422():
    request = make_request()

    with pytest.raises(
        HTTPException
    ) as result:
        asyncio.run(
            avatar_state_route
            .get_unified_avatar_state(
                profile_id="not-a-uuid",
                request=request,
            )
        )

    assert result.value.status_code == 422


def test_missing_profile_returns_404(
    monkeypatch,
):
    request = make_request()
    profile_id = str(uuid4())

    monkeypatch.setattr(
        avatar_state_route,
        "require_profile_access",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        avatar_state_route,
        (
            "resolve_digital_human_"
            "profile_repository"
        ),
        lambda request: object(),
    )

    class FakeService:
        async def build_state(
            self,
            profile_id,
            *,
            repository,
        ):
            return make_state(
                profile_id=profile_id,
                profile_loaded=False,
            )

    monkeypatch.setattr(
        avatar_state_route,
        "AvatarStateService",
        FakeService,
    )

    with pytest.raises(
        HTTPException
    ) as result:
        asyncio.run(
            avatar_state_route
            .get_unified_avatar_state(
                profile_id=profile_id,
                request=request,
            )
        )

    assert result.value.status_code == 404


def test_loaded_profile_returns_unified_state(
    monkeypatch,
):
    request = make_request()
    profile_id = str(uuid4())
    repository = object()

    monkeypatch.setattr(
        avatar_state_route,
        "require_profile_access",
        lambda **kwargs: None,
    )

    monkeypatch.setattr(
        avatar_state_route,
        (
            "resolve_digital_human_"
            "profile_repository"
        ),
        lambda request: repository,
    )

    class FakeService:
        async def build_state(
            self,
            profile_id,
            *,
            repository,
        ):
            return make_state(
                profile_id=profile_id,
                profile_loaded=True,
            )

    monkeypatch.setattr(
        avatar_state_route,
        "AvatarStateService",
        FakeService,
    )

    state = asyncio.run(
        avatar_state_route
        .get_unified_avatar_state(
            profile_id=profile_id,
            request=request,
        )
    )

    assert state.profile_id == profile_id
    assert (
        state.source_integrity[
            "profile_loaded"
        ]
        is True
    )


def test_missing_database_configuration_returns_503(
    monkeypatch,
):
    request = make_request()
    profile_id = str(uuid4())

    monkeypatch.setattr(
        avatar_state_route,
        "require_profile_access",
        lambda **kwargs: None,
    )

    def fail_repository(request):
        raise (
            avatar_state_route
            .DigitalHumanProfileRepositoryError(
                "DATABASE_URL is missing."
            )
        )

    monkeypatch.setattr(
        avatar_state_route,
        (
            "resolve_digital_human_"
            "profile_repository"
        ),
        fail_repository,
    )

    with pytest.raises(
        HTTPException
    ) as result:
        asyncio.run(
            avatar_state_route
            .get_unified_avatar_state(
                profile_id=profile_id,
                request=request,
            )
        )

    assert result.value.status_code == 503

    assert result.value.detail == (
        "Avatar profile persistence is unavailable."
    )
