import inspect

from app.schemas.avatar_evidence import (
    AvatarEvidenceSelectionRequest,
)
from app.services.avatar_evidence_repository import (
    AvatarEvidenceRepository,
)


def test_selection_request_rejects_extra_scores():
    request = AvatarEvidenceSelectionRequest.model_validate(
        {
            "profile_id": (
                "3302091e-e8da-4d22-b4d3-b72c5c954c4c"
            ),
            "make_primary": True,
        }
    )

    assert request.make_primary is True


def test_selection_request_forbids_client_quality():
    try:
        AvatarEvidenceSelectionRequest.model_validate(
            {
                "profile_id": (
                    "3302091e-e8da-4d22-b4d3-b72c5c954c4c"
                ),
                "make_primary": True,
                "quality_score": 1.0,
            }
        )
    except Exception:
        return

    raise AssertionError(
        "Client quality_score must be forbidden."
    )


def test_repository_exposes_canonical_operations():
    methods = {
        name
        for name, value
        in inspect.getmembers(
            AvatarEvidenceRepository,
            predicate=inspect.isfunction,
        )
    }

    assert {
        "upsert_uploaded_asset",
        "get",
        "require",
        "list_profile_assets",
        "list_active_assets",
        "select_for_avatar",
        "remove_from_avatar",
        "archive",
        "resolve_primary",
    }.issubset(methods)


def test_repository_rejects_invalid_score_values():
    repository = object.__new__(
        AvatarEvidenceRepository
    )

    invalid_values = [
        -0.01,
        1.01,
    ]

    for invalid_value in invalid_values:
        try:
            repository._validate_score(
                "test_score",
                invalid_value,
            )
        except ValueError:
            continue

        raise AssertionError(
            "Invalid score value was accepted."
        )


def test_repository_accepts_score_boundaries():
    repository = object.__new__(
        AvatarEvidenceRepository
    )

    repository._validate_score(
        "minimum",
        0.0,
    )

    repository._validate_score(
        "maximum",
        1.0,
    )
