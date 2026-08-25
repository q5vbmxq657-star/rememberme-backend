from __future__ import annotations

import ast
from pathlib import Path


def test_single_whole_profile_delete_method():
    source = Path(
        "app/services/"
        "avatar_media_storage_service.py"
    ).read_text(
        encoding="utf-8"
    )

    tree = ast.parse(source)

    owner = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and node.name
        == "AvatarMediaStorageService"
    )

    matches = [
        node
        for node in owner.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name
        == "delete_profile_assets"
    ]

    assert len(matches) == 1


def test_profile_delete_reuses_bound_single_delete():
    source = Path(
        "app/services/"
        "avatar_media_storage_service.py"
    ).read_text(
        encoding="utf-8"
    )

    start = source.index(
        "def delete_profile_assets("
    )

    end = source.index(
        "def delete_profile_asset(",
        start,
    )

    method = source[
        start:
        end
    ]

    assert (
        "self.delete_profile_asset("
        in method
    )

    assert (
        "self._validate_profile_id("
        in method
    )

    assert (
        "self._is_within_storage_root("
        in method
    )


def test_untracked_private_files_fail_closed():
    source = Path(
        "app/services/"
        "avatar_media_storage_service.py"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "untracked private files"
        in source
    )
