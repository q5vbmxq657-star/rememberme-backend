from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REPOSITORY = (
    ROOT
    / "app"
    / "services"
    / "digital_human_profile_repository.py"
)

MIGRATION = (
    ROOT
    / "migrations"
    / "007_profile_erasure_resume_authority.sql"
)


def repository_class() -> ast.ClassDef:
    tree = ast.parse(
        REPOSITORY.read_text(
            encoding="utf-8"
        )
    )

    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "DigitalHumanProfileRepository"
    ]

    assert len(matches) == 1
    return matches[0]


def method(name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in repository_class().body
        if isinstance(node, ast.FunctionDef)
        and node.name == name
    ]

    assert len(matches) == 1
    return matches[0]


def string_literals(name: str) -> str:
    return "\n".join(
        node.value
        for node in ast.walk(method(name))
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
    )


def test_required_authority_methods_exist_once() -> None:
    names = [
        node.name
        for node in repository_class().body
        if isinstance(node, ast.FunctionDef)
    ]

    for required in (
        "_profile_advisory_lock_keys",
        "_lock_profile_scope",
        "_require_profile_write_allowed_with_cursor",
        "get_active_profile_erasure_request",
        "get_profile_erasure_request_for_resume",
        "require_profile_write_allowed",
        "transition_profile_erasure_request",
    ):
        assert names.count(required) == 1


def test_advisory_lock_is_transaction_scoped() -> None:
    source = string_literals(
        "_lock_profile_scope"
    )

    assert "pg_advisory_xact_lock" in source


def test_create_erasure_acquires_profile_lock() -> None:
    calls = [
        node.func.attr
        for node in ast.walk(
            method(
                "create_profile_erasure_request"
            )
        )
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
    ]

    assert "_lock_profile_scope" in calls


def test_resume_due_contract() -> None:
    source = string_literals(
        "get_profile_erasure_request_for_resume"
    )

    assert "status = 'retryable_failed'" in source
    assert "resume_stage IS NOT NULL" in source
    assert "next_retry_at <= NOW()" in source


def test_transition_has_explicit_postgres_types() -> None:
    source = string_literals(
        "transition_profile_erasure_request"
    )

    assert "%s::timestamptz" in source
    assert "NULL::timestamptz" in source
    assert "%s::text" in source
    assert "NULL::text" in source
    assert "%s::uuid" in source


def test_transition_is_compare_and_set() -> None:
    source = string_literals(
        "transition_profile_erasure_request"
    )

    assert "WHERE request_id = %s::uuid" in source
    assert "AND status = %s::text" in source
    assert "attempt_count + 1" in source


def test_migration_uses_canonical_columns_only() -> None:
    source = MIGRATION.read_text(
        encoding="utf-8"
    )

    for required in (
        "resume_stage TEXT",
        "attempt_count INTEGER NOT NULL DEFAULT 0",
        "next_retry_at TIMESTAMPTZ",
        "profile_erasure_single_active_profile_index",
        "profile_erasure_resume_queue_index",
    ):
        assert required in source

    for forbidden in (
        "resume_status",
        "last_attempt_at",
        "last_resumed_at",
    ):
        assert forbidden not in source
