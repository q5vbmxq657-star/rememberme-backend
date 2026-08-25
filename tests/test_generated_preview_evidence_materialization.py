from __future__ import annotations

import ast
from pathlib import Path


PROVIDER = Path(
    "app/services/"
    "avatar_provider_service.py"
)

REPOSITORY = Path(
    "app/services/"
    "digital_human_profile_repository.py"
)


def _method_source(
    path: Path,
    *,
    class_name: str,
    method_name: str,
) -> str:
    source = path.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    class_node = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name == class_name
    )

    method = next(
        node
        for node in class_node.body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == method_name
    )

    lines = source.splitlines(
        keepends=True
    )

    return "".join(
        lines[
            method.lineno - 1:
            method.end_lineno
        ]
    )


def test_ready_provider_video_materializes_privately():
    method = _method_source(
        PROVIDER,
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "fetch_tavus_video_status"
        ),
    )

    required = {
        "_generated_preview_download_url(",
        "ingest_remote_generated_preview(",
        "upsert_uploaded_asset(",
        '"generated_preview"',
        '"generated-preview-evidence-v1"',
        '"biometric_evaluation":',
        '"not_performed"',
        "generated_asset_id=(",
        "media_sha256=(",
        "media_content_type=(",
        "media_size_bytes=(",
        'status="ready"',
        "sign_download_url(",
    }

    for token in required:
        assert token in method


def test_provider_uses_download_url_only():
    method = _method_source(
        PROVIDER,
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "_generated_preview_download_url"
        ),
    )

    assert (
        'payload.get(\n'
        '                "download_url"'
        in method
    )

    assert (
        "hosted_url"
        not in method
    )

    assert (
        "stream_url"
        not in method
    )


def test_ready_state_is_idempotent():
    method = _method_source(
        PROVIDER,
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "fetch_tavus_video_status"
        ),
    )

    ready_lookup = method.index(
        '== "ready"'
    )

    signed_url = method.index(
        "sign_download_url(",
        ready_lookup,
    )

    provider_poll = method.index(
        "client.get("
    )

    assert (
        ready_lookup
        < signed_url
        < provider_poll
    )


def test_repository_persists_materialization_fields():
    method = _method_source(
        REPOSITORY,
        class_name=(
            "DigitalHumanProfileRepository"
        ),
        method_name=(
            "update_generated_preview_job"
        ),
    )

    required = {
        "generated_asset_id",
        "media_sha256",
        "media_content_type",
        "media_size_bytes",
        "materialized_at",
    }

    for token in required:
        assert token in method


def test_no_biometric_claim_is_created():
    method = _method_source(
        PROVIDER,
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "fetch_tavus_video_status"
        ),
    )

    forbidden = {
        "append_identity_verification_receipt",
        "identity_verified",
        "face_score=",
        "voice_score=",
        "recommended_for_avatar=True",
    }

    for token in forbidden:
        assert token not in method


def test_provider_url_is_never_returned():
    method = _method_source(
        PROVIDER,
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "fetch_tavus_video_status"
        ),
    )

    forbidden = {
        "preview_url=source_url",
        "preview_url=data.get(",
        "preview_url=data[",
    }

    for token in forbidden:
        assert token not in method
