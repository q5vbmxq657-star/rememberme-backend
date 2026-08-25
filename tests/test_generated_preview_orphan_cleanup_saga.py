from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

from app.services.avatar_provider_service import (
    AvatarProviderService,
)


class FakeStorage:
    def __init__(
        self,
        *,
        should_fail: bool = False,
        allowed_profile_id=None,
    ) -> None:
        self.should_fail = should_fail
        self.allowed_profile_id = (
            allowed_profile_id
        )
        self.deleted: list[
            tuple[str, str]
        ] = []

    def delete_profile_asset(
        self,
        *,
        profile_id: str,
        asset_id: str,
    ) -> bool:
        self.deleted.append(
            (
                profile_id,
                asset_id,
            )
        )

        if self.should_fail:
            raise RuntimeError(
                "storage cleanup failed"
            )

        if (
            self.allowed_profile_id
            is not None
            and profile_id
            != str(
                self.allowed_profile_id
            )
        ):
            return False

        return True


class FakeEvidence:
    def __init__(
        self,
        *,
        should_fail: bool = False,
    ) -> None:
        self.should_fail = should_fail
        self.deleted: list[
            tuple[object, object]
        ] = []

    def delete_generated_preview_asset(
        self,
        *,
        profile_id,
        asset_id,
    ) -> bool:
        self.deleted.append(
            (
                profile_id,
                asset_id,
            )
        )

        if self.should_fail:
            raise RuntimeError(
                "evidence cleanup failed"
            )

        return True


def make_service(
    *,
    storage: FakeStorage,
    evidence: FakeEvidence,
) -> AvatarProviderService:
    service = AvatarProviderService()

    service._media_storage_service = (
        storage
    )

    service._evidence_repository = (
        evidence
    )

    return service


def test_compensation_uses_profile_bound_storage_delete():
    profile_id = uuid4()
    asset_id = uuid4()
    events: list[str] = []

    class OrderedEvidence(
        FakeEvidence
    ):
        def delete_generated_preview_asset(
            self,
            *,
            profile_id,
            asset_id,
        ) -> bool:
            events.append(
                "evidence"
            )

            return super().delete_generated_preview_asset(
                profile_id=profile_id,
                asset_id=asset_id,
            )

    class OrderedStorage(
        FakeStorage
    ):
        def delete_profile_asset(
            self,
            *,
            profile_id: str,
            asset_id: str,
        ) -> bool:
            events.append(
                "storage"
            )

            return super().delete_profile_asset(
                profile_id=profile_id,
                asset_id=asset_id,
            )

    evidence = OrderedEvidence()
    storage = OrderedStorage()

    errors = make_service(
        storage=storage,
        evidence=evidence,
    )._compensate_generated_preview_materialization(
        profile_id=profile_id,
        asset_id=asset_id,
        evidence_persisted=True,
    )

    assert errors == []

    assert events == [
        "evidence",
        "storage",
    ]

    assert storage.deleted == [
        (
            str(profile_id),
            str(asset_id),
        )
    ]


def test_storage_cleanup_receives_same_profile_binding():
    profile_id = uuid4()
    asset_id = uuid4()

    storage = FakeStorage(
        allowed_profile_id=profile_id
    )

    errors = make_service(
        storage=storage,
        evidence=FakeEvidence(),
    )._compensate_generated_preview_materialization(
        profile_id=profile_id,
        asset_id=asset_id,
        evidence_persisted=False,
    )

    assert errors == []

    assert storage.deleted == [
        (
            str(profile_id),
            str(asset_id),
        )
    ]


def test_cross_profile_storage_binding_fails_closed():
    actual_profile_id = uuid4()
    wrong_profile_id = uuid4()
    asset_id = uuid4()

    storage = FakeStorage(
        allowed_profile_id=(
            actual_profile_id
        )
    )

    errors = make_service(
        storage=storage,
        evidence=FakeEvidence(),
    )._compensate_generated_preview_materialization(
        profile_id=wrong_profile_id,
        asset_id=asset_id,
        evidence_persisted=False,
    )

    assert errors == []

    assert storage.deleted == [
        (
            str(wrong_profile_id),
            str(asset_id),
        )
    ]


def test_cleanup_failures_do_not_mask_each_other():
    profile_id = uuid4()
    asset_id = uuid4()

    evidence = FakeEvidence(
        should_fail=True
    )

    storage = FakeStorage(
        should_fail=True
    )

    errors = make_service(
        storage=storage,
        evidence=evidence,
    )._compensate_generated_preview_materialization(
        profile_id=profile_id,
        asset_id=asset_id,
        evidence_persisted=True,
    )

    assert errors == [
        "evidence_cleanup_failed:"
        "RuntimeError",
        "storage_cleanup_failed:"
        "RuntimeError",
    ]


def test_storage_method_requires_profile_and_asset():
    source = Path(
        "app/services/"
        "avatar_media_storage_service.py"
    ).read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    owner = next(
        node
        for node in tree.body
        if isinstance(
            node,
            ast.ClassDef,
        )
        and node.name
        == "AvatarMediaStorageService"
    )

    method = next(
        node
        for node in owner.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name
        == "delete_profile_asset"
    )

    lines = source.splitlines(
        keepends=True
    )

    body = "".join(
        lines[
            method.lineno - 1:
            method.end_lineno
        ]
    )

    assert (
        "metadata.profile_id != profile_id"
        in body
    )

    assert (
        "return False"
        in body
    )

    assert (
        "_is_within_storage_root("
        in body
    )

    assert (
        "_delete_file_if_exists("
        in body
    )


def test_provider_no_longer_uses_unbound_storage_delete():
    source = Path(
        "app/services/"
        "avatar_provider_service.py"
    ).read_text(
        encoding="utf-8"
    )

    tree = ast.parse(
        source
    )

    owner = next(
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
        for node in owner.body
        if isinstance(
            node,
            ast.FunctionDef,
        )
        and node.name
        == (
            "_compensate_generated_preview_"
            "materialization"
        )
    )

    lines = source.splitlines(
        keepends=True
    )

    body = "".join(
        lines[
            method.lineno - 1:
            method.end_lineno
        ]
    )

    assert (
        "delete_profile_asset("
        in body
    )

    assert (
        ".delete_asset("
        not in body
    )

    assert (
        "profile_id=str("
        in body
    )
