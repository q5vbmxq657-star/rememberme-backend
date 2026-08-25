from __future__ import annotations

import ast
from pathlib import Path


PROVIDER_PATH = Path(
    "app/services/"
    "avatar_provider_service.py"
)


def _fetch_method_node() -> tuple[
    str,
    ast.AsyncFunctionDef,
]:
    source = PROVIDER_PATH.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    provider = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name
        == "AvatarProviderService"
    )

    method = next(
        node
        for node in provider.body
        if isinstance(
            node,
            ast.AsyncFunctionDef,
        )
        and node.name
        == "fetch_tavus_video_status"
    )

    return source, method


def _fetch_method_source() -> str:
    source, method = (
        _fetch_method_node()
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


def _fetch_method_string_literals() -> set[str]:
    _, method = (
        _fetch_method_node()
    )

    return {
        node.value
        for node in ast.walk(
            method
        )
        if isinstance(
            node,
            ast.Constant,
        )
        and isinstance(
            node.value,
            str,
        )
    }


def test_polling_requires_durable_job():
    method = _fetch_method_source()

    lookup = method.index(
        "get_generated_preview_job_by_external_id("
    )

    provider_request = method.index(
        "client.get("
    )

    assert lookup < provider_request

    string_literals = (
        _fetch_method_string_literals()
    )

    assert (
        "Generated-preview job was not "
        "found in durable STAY state."
        in string_literals
    )


def test_polling_revalidates_avatar_binding():
    method = _fetch_method_source()

    required = {
        "current_training_version",
        "bound_training_version",
        "current_replica_id",
        "current_avatar_status",
        "binding_is_current",
        'status="stale"',
        "avatar_binding_stale",
    }

    for token in required:
        assert token in method


def test_completed_provider_state_materializes_first():
    method = _fetch_method_source()

    completed_branch = method.index(
        "if raw_status in completed_statuses:"
    )

    materializing = method.index(
        'status="materializing"',
        completed_branch,
    )

    secure_ingestion = method.index(
        "ingest_remote_generated_preview(",
        materializing,
    )

    evidence_upsert = method.index(
        "upsert_uploaded_asset(",
        secure_ingestion,
    )

    signed_url = method.index(
        "sign_download_url(",
        evidence_upsert,
    )

    ready_persistence = method.index(
        'status="ready"',
        signed_url,
    )

    ready_return = method.index(
        'status="ready"',
        ready_persistence + 1,
    )

    assert (
        completed_branch
        < materializing
        < secure_ingestion
        < evidence_upsert
        < signed_url
        < ready_persistence
        < ready_return
    )



def test_polling_never_exposes_provider_media_url():
    method = _fetch_method_source()

    assert (
        "preview_url=None"
        in method
    )

    forbidden = {
        "preview_url=data.get(",
        "preview_url=video_url",
        "preview_url=download_url",
        "preview_url=hosted_url",
    }

    for token in forbidden:
        assert token not in method


def test_polling_persists_terminal_states():
    method = _fetch_method_source()

    required = {
        'status="failed"',
        'status="cancelled"',
        "tavus_generation_failed",
        "tavus_generation_cancelled",
    }

    for token in required:
        assert token in method


def test_polling_has_no_biometric_or_receipt_write():
    method = _fetch_method_source()

    forbidden = {
        "append_identity_verification_receipt",
        "face_score=",
        "voice_score=",
        "identity_verified",
        "recommended_for_avatar=True",
        '"biometric_evaluation": "performed"',
        '"biometric_evaluation": "verified"',
        '"biometric_evaluation": "passed"',
    }

    for token in forbidden:
        assert token not in method

    assert (
        '"biometric_evaluation":'
        in method
    )

    biometric_marker = method.index(
        '"biometric_evaluation":'
    )

    biometric_contract = method[
        biometric_marker:
        biometric_marker + 180
    ]

    assert (
        '"not_performed"'
        in biometric_contract
    )

    assert (
        '"identity_verification":'
        in method
    )



def test_ready_fast_path_revalidates_current_avatar_binding():
    method = _fetch_method_source()

    current_version = method.index(
        "current_training_version ="
    )

    current_replica = method.index(
        "current_replica_id =",
        current_version,
    )

    binding = method.index(
        "binding_is_current =",
        current_replica,
    )

    stale_branch = method.index(
        "if not binding_is_current:",
        binding,
    )

    ready_fast_path = method.index(
        '== "ready"',
        stale_branch,
    )

    ready_signing = method.index(
        "sign_download_url(",
        ready_fast_path,
    )

    assert (
        current_version
        < current_replica
        < binding
        < stale_branch
        < ready_fast_path
        < ready_signing
    )
