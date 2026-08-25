from __future__ import annotations

import hashlib
import os

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence, Tuple

import onnxruntime as ort


class FaceIdentityModelAuthorityError(RuntimeError):
    pass


class FaceIdentityModelConfigurationError(
    FaceIdentityModelAuthorityError
):
    pass


class FaceIdentityModelIntegrityError(
    FaceIdentityModelAuthorityError
):
    pass


class FaceIdentityModelContractError(
    FaceIdentityModelAuthorityError
):
    pass


@dataclass(frozen=True)
class FaceIdentityModelPolicy:
    model_path: Path
    model_version: str
    model_sha256: str
    policy_version: str
    similarity_threshold: float
    commercial_license_confirmed: bool


@dataclass(frozen=True)
class FaceIdentityModelDescriptor:
    model_path: Path
    model_version: str
    model_sha256: str
    policy_version: str
    similarity_threshold: float
    execution_providers: Tuple[str, ...]
    input_names: Tuple[str, ...]
    output_names: Tuple[str, ...]


class FaceIdentityModelAuthority:
    """
    Canonical server-side authority for a commercially licensed,
    versioned ONNX face-embedding model.

    This class does not download models and does not choose a biometric
    threshold. Deployment must provide both under an approved,
    versioned commercial contract.
    """

    REQUIRED_ENVIRONMENT_KEYS = (
        "FACE_IDENTITY_MODEL_PATH",
        "FACE_IDENTITY_MODEL_VERSION",
        "FACE_IDENTITY_MODEL_SHA256",
        "FACE_IDENTITY_POLICY_VERSION",
        "FACE_IDENTITY_SIMILARITY_THRESHOLD",
        "FACE_IDENTITY_COMMERCIAL_LICENSE_CONFIRMED",
    )

    def __init__(
        self,
        *,
        policy: Optional[
            FaceIdentityModelPolicy
        ] = None,
    ) -> None:
        self.policy = (
            policy
            if policy is not None
            else self._policy_from_environment()
        )

    def validate(self) -> FaceIdentityModelDescriptor:
        policy = self.policy

        if not policy.commercial_license_confirmed:
            raise FaceIdentityModelConfigurationError(
                "Commercial face-model usage has not been "
                "explicitly confirmed."
            )

        if not policy.model_version.strip():
            raise FaceIdentityModelConfigurationError(
                "FACE_IDENTITY_MODEL_VERSION must not be empty."
            )

        if not policy.policy_version.strip():
            raise FaceIdentityModelConfigurationError(
                "FACE_IDENTITY_POLICY_VERSION must not be empty."
            )

        if not 0.0 < policy.similarity_threshold < 1.0:
            raise FaceIdentityModelConfigurationError(
                "FACE_IDENTITY_SIMILARITY_THRESHOLD must be "
                "strictly between 0 and 1."
            )

        model_path = policy.model_path.expanduser().resolve()

        if not model_path.is_file():
            raise FaceIdentityModelConfigurationError(
                "Configured face-identity model does not exist."
            )

        if model_path.suffix.lower() != ".onnx":
            raise FaceIdentityModelConfigurationError(
                "The face-identity model must be an ONNX artifact."
            )

        expected_sha256 = (
            policy.model_sha256
            .strip()
            .lower()
        )

        if (
            len(expected_sha256) != 64
            or any(
                character not in "0123456789abcdef"
                for character in expected_sha256
            )
        ):
            raise FaceIdentityModelConfigurationError(
                "FACE_IDENTITY_MODEL_SHA256 must be a valid "
                "lowercase SHA-256 digest."
            )

        actual_sha256 = self._sha256(
            model_path
        )

        if actual_sha256 != expected_sha256:
            raise FaceIdentityModelIntegrityError(
                "The configured face-identity model checksum "
                "does not match the approved artifact."
            )

        try:
            session = ort.InferenceSession(
                str(model_path),
                providers=[
                    "CPUExecutionProvider",
                ],
            )
        except Exception as error:
            raise FaceIdentityModelContractError(
                "The configured face-identity model could not "
                "be loaded by ONNX Runtime."
            ) from error

        input_names = self._names(
            session.get_inputs()
        )

        output_names = self._names(
            session.get_outputs()
        )

        if len(input_names) != 1:
            raise FaceIdentityModelContractError(
                "The face-identity model must expose exactly "
                "one image input."
            )

        if len(output_names) != 1:
            raise FaceIdentityModelContractError(
                "The face-identity model must expose exactly "
                "one embedding output."
            )

        providers = tuple(
            session.get_providers()
        )

        if "CPUExecutionProvider" not in providers:
            raise FaceIdentityModelContractError(
                "CPUExecutionProvider is unavailable."
            )

        return FaceIdentityModelDescriptor(
            model_path=model_path,
            model_version=policy.model_version.strip(),
            model_sha256=actual_sha256,
            policy_version=policy.policy_version.strip(),
            similarity_threshold=(
                policy.similarity_threshold
            ),
            execution_providers=providers,
            input_names=input_names,
            output_names=output_names,
        )

    def _policy_from_environment(
        self,
    ) -> FaceIdentityModelPolicy:
        missing = [
            key
            for key in self.REQUIRED_ENVIRONMENT_KEYS
            if not os.getenv(key, "").strip()
        ]

        if missing:
            raise FaceIdentityModelConfigurationError(
                "Missing face-identity model configuration: "
                + ", ".join(missing)
            )

        threshold_raw = os.environ[
            "FACE_IDENTITY_SIMILARITY_THRESHOLD"
        ]

        try:
            threshold = float(
                threshold_raw
            )
        except ValueError as error:
            raise FaceIdentityModelConfigurationError(
                "FACE_IDENTITY_SIMILARITY_THRESHOLD must be "
                "a number."
            ) from error

        license_confirmation = (
            os.environ[
                "FACE_IDENTITY_COMMERCIAL_LICENSE_CONFIRMED"
            ]
            .strip()
            .lower()
        )

        if license_confirmation not in {
            "true",
            "false",
        }:
            raise FaceIdentityModelConfigurationError(
                "FACE_IDENTITY_COMMERCIAL_LICENSE_CONFIRMED "
                "must be true or false."
            )

        return FaceIdentityModelPolicy(
            model_path=Path(
                os.environ[
                    "FACE_IDENTITY_MODEL_PATH"
                ]
            ),
            model_version=os.environ[
                "FACE_IDENTITY_MODEL_VERSION"
            ],
            model_sha256=os.environ[
                "FACE_IDENTITY_MODEL_SHA256"
            ],
            policy_version=os.environ[
                "FACE_IDENTITY_POLICY_VERSION"
            ],
            similarity_threshold=threshold,
            commercial_license_confirmed=(
                license_confirmation == "true"
            ),
        )

    @staticmethod
    def _sha256(
        path: Path,
    ) -> str:
        digest = hashlib.sha256()

        with path.open("rb") as model_file:
            for chunk in iter(
                lambda: model_file.read(
                    1024 * 1024
                ),
                b"",
            ):
                digest.update(chunk)

        return digest.hexdigest()

    @staticmethod
    def _names(
        nodes: Sequence[object],
    ) -> Tuple[str, ...]:
        names = []

        for node in nodes:
            name = getattr(
                node,
                "name",
                None,
            )

            if not isinstance(name, str) or not name.strip():
                raise FaceIdentityModelContractError(
                    "The ONNX model contains an unnamed "
                    "input or output."
                )

            names.append(
                name.strip()
            )

        return tuple(names)
