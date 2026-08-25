from __future__ import annotations

import ast
from pathlib import Path


def _repository_source() -> str:
    return Path(
        "app/services/"
        "digital_human_profile_repository.py"
    ).read_text(
        encoding="utf-8"
    )


def test_single_canonical_erasure_methods_exist():
    source = _repository_source()
    tree = ast.parse(source)

    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "DigitalHumanProfileRepository"
    )

    method_names = [
        node.name
        for node in owner.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
    ]

    for expected in (
        "create_profile_erasure_request",
        "get_profile_erasure_request",
        "update_profile_erasure_request",
        "delete_profile_graph",
    ):
        assert (
            method_names.count(expected)
            == 1
        )


def test_erasure_request_is_idempotent():
    source = _repository_source()

    assert (
        "ON CONFLICT ("
        in source
    )

    assert (
        "idempotency_key"
        in source
    )


def test_completion_removes_profile_identifier():
    source = _repository_source()

    assert (
        "WHEN %s = 'completed'"
        in source
    )

    assert (
        "THEN NULL"
        in source
    )


def test_profile_graph_delete_is_single_transaction():
    source = _repository_source()

    method_start = source.index(
        "def delete_profile_graph("
    )

    method_end = source.index(
        "def _profile_from_row(",
        method_start,
    )

    method = source[
        method_start:
        method_end
    ]

    assert (
        "DELETE FROM digital_human_profiles"
        in method
    )

    assert (
        "connection.commit()"
        in method
    )


def test_migration_has_durable_retry_states():
    migration = Path(
        "migrations/"
        "006_profile_erasure_requests.sql"
    ).read_text(
        encoding="utf-8"
    )

    for status in (
        "requested",
        "provider_cleanup",
        "provider_cleanup_required",
        "storage_cleanup",
        "database_cleanup",
        "retryable_failed",
        "completed",
    ):
        assert status in migration
