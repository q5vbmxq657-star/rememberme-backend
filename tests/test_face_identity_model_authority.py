from __future__ import annotations

from pathlib import Path

import pytest

from app.services.avatar_face_analysis_service import (
    AvatarFaceAnalysisService,
)
from app.services.face_identity_model_authority import (
    FaceIdentityModelAuthority,
    FaceIdentityModelConfigurationError,
    FaceIdentityModelIntegrityError,
    FaceIdentityModelPolicy,
)


def _policy(
    tmp_path: Path,
    *,
    commercial_license_confirmed: bool = True,
    model_sha256: str = "0" * 64,
    threshold: float = 0.82,
) -> FaceIdentityModelPolicy:
    model_path = tmp_path / "licensed-face-model.onnx"
    model_path.write_bytes(
        b"not-a-real-onnx-model"
    )

    return FaceIdentityModelPolicy(
        model_path=model_path,
        model_version="licensed-face-model-v1",
        model_sha256=model_sha256,
        policy_version="face-verification-policy-v1",
        similarity_threshold=threshold,
        commercial_license_confirmed=(
            commercial_license_confirmed
        ),
    )


def test_metadata_preflight_never_claims_face_or_identity():
    result = AvatarFaceAnalysisService().analyze(
        content_type="image/jpeg",
        size_bytes=8_000_000,
    )

    assert result["has_face"] is False
    assert result["has_frontal_face"] is False
    assert result["identity_consistency_score"] == 0.0
    assert result["recommended_for_avatar"] is False
    assert (
        result["analysis_metadata"][
            "pixel_analysis_performed"
        ]
        is False
    )
    assert (
        result["analysis_metadata"][
            "biometric_analysis_performed"
        ]
        is False
    )


def test_model_authority_requires_commercial_license(
    tmp_path: Path,
):
    authority = FaceIdentityModelAuthority(
        policy=_policy(
            tmp_path,
            commercial_license_confirmed=False,
        )
    )

    with pytest.raises(
        FaceIdentityModelConfigurationError,
        match="Commercial face-model usage",
    ):
        authority.validate()


@pytest.mark.parametrize(
    "threshold",
    [
        0.0,
        1.0,
        -0.1,
        1.1,
    ],
)
def test_model_authority_rejects_invalid_threshold(
    tmp_path: Path,
    threshold: float,
):
    authority = FaceIdentityModelAuthority(
        policy=_policy(
            tmp_path,
            threshold=threshold,
        )
    )

    with pytest.raises(
        FaceIdentityModelConfigurationError,
        match="strictly between 0 and 1",
    ):
        authority.validate()


def test_model_authority_rejects_checksum_mismatch(
    tmp_path: Path,
):
    authority = FaceIdentityModelAuthority(
        policy=_policy(
            tmp_path,
            model_sha256="0" * 64,
        )
    )

    with pytest.raises(
        FaceIdentityModelIntegrityError,
        match="checksum",
    ):
        authority.validate()


def test_environment_configuration_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
):
    for key in (
        FaceIdentityModelAuthority
        .REQUIRED_ENVIRONMENT_KEYS
    ):
        monkeypatch.delenv(
            key,
            raising=False,
        )

    with pytest.raises(
        FaceIdentityModelConfigurationError,
        match="Missing face-identity model configuration",
    ):
        FaceIdentityModelAuthority()
