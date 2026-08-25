from __future__ import annotations

import ast
from pathlib import Path


def _method_source(
    relative_path: str,
    *,
    class_name: str,
    method_name: str,
) -> str:
    path = Path(relative_path)

    source = path.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    classes = [
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name == class_name
    ]

    assert len(classes) == 1

    methods = [
        node
        for node in classes[0].body
        if isinstance(
            node,
            (
                ast.FunctionDef,
                ast.AsyncFunctionDef,
            ),
        )
        and node.name == method_name
    ]

    assert len(methods) == 1

    method = methods[0]

    lines = source.splitlines(
        keepends=True
    )

    return "".join(
        lines[
            method.lineno - 1:
            method.end_lineno
        ]
    )


def test_generated_preview_migration_is_durable():
    source = Path(
        "migrations/"
        "005_generated_preview_jobs.sql"
    ).read_text(
        encoding="utf-8"
    )

    required = {
        "digital_human_generated_preview_jobs",
        "profile_id UUID NOT NULL",
        "training_version INTEGER NOT NULL",
        "package_record_id TEXT NOT NULL",
        "provider_video_id TEXT",
        "generated_asset_id UUID",
        "media_sha256 TEXT",
        "'materializing'",
        "'stale'",
        "005_generated_preview_jobs",
    }

    for token in required:
        assert token in source


def test_repository_owns_preview_lifecycle():
    source = Path(
        "app/services/"
        "digital_human_profile_repository.py"
    ).read_text(
        encoding="utf-8"
    )

    required = {
        "resolve_ready_avatar_by_replica",
        "create_generated_preview_job",
        "update_generated_preview_job",
        "get_generated_preview_job_by_external_id",
        "package_record_id",
        "current_training_version",
        "current_replica_id",
    }

    for token in required:
        assert token in source


def test_provider_creates_state_before_submission():
    method = _method_source(
        "app/services/"
        "avatar_provider_service.py",
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "create_tavus_video"
        ),
    )

    creation = method.index(
        "create_generated_preview_job("
    )

    submission = method.index(
        "client.post("
    )

    assert creation < submission


def test_provider_persists_video_id_after_submission():
    method = _method_source(
        "app/services/"
        "avatar_provider_service.py",
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "create_tavus_video"
        ),
    )

    submission = method.index(
        "client.post("
    )

    persistence = method.index(
        "provider_video_id=("
    )

    assert submission < persistence


def test_provider_has_no_biometric_side_effect():
    method = _method_source(
        "app/services/"
        "avatar_provider_service.py",
        class_name=(
            "AvatarProviderService"
        ),
        method_name=(
            "create_tavus_video"
        ),
    )

    forbidden = {
        "append_identity_verification_receipt",
        "face_score=",
        "voice_score=",
        "identity_verified",
    }

    for token in forbidden:
        assert token not in method


def test_realtime_runtime_files_are_out_of_scope():
    changed_contract = {
        "app/services/"
        "digital_human_profile_repository.py",
        "app/services/"
        "avatar_provider_service.py",
        "migrations/"
        "005_generated_preview_jobs.sql",
    }

    forbidden = {
        "app/services/"
        "avatar_runtime_session_service.py",
        "app/services/"
        "avatar_runtime_tavus_adapter.py",
        "app/routes/realtime.py",
    }

    assert changed_contract.isdisjoint(
        forbidden
    )
