from __future__ import annotations

import ast
from pathlib import Path


def _method_source() -> str:
    path = Path(
        "app/services/"
        "digital_human_profile_repository.py"
    )

    source = path.read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    owner = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name
        == "DigitalHumanProfileRepository"
    )

    method = next(
        node
        for node in owner.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name
        == "update_generated_preview_job"
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


def test_nullable_generated_asset_is_uuid_typed():
    method = _method_source()

    assert (
        "%s::uuid IS NOT NULL"
        in method
    )

    assert (
        "%s IS NOT NULL"
        not in method
    )


def test_materialized_at_requires_ready_and_asset():
    method = _method_source()

    materialized = method.index(
        "materialized_at ="
    )

    ready = method.index(
        "%s = 'ready'",
        materialized,
    )

    typed_asset = method.index(
        "%s::uuid IS NOT NULL",
        ready,
    )

    assert (
        materialized
        < ready
        < typed_asset
    )


def test_single_canonical_update_method():
    source = Path(
        "app/services/"
        "digital_human_profile_repository.py"
    ).read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    owner = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name
        == "DigitalHumanProfileRepository"
    )

    matches = [
        node
        for node in owner.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name
        == "update_generated_preview_job"
    ]

    assert len(matches) == 1
