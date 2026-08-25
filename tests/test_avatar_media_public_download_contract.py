from pathlib import Path

from app.main import app
from app.routes.avatar_media import (
    public_router,
    router,
)


DOWNLOAD_PATH = (
    "/public/assets/{asset_id}"
)


def route_paths(target_router):
    return {
        route.path
        for route in target_router.routes
    }


def test_download_is_only_in_public_router():
    public_paths = route_paths(
        public_router
    )

    protected_paths = route_paths(
        router
    )

    assert DOWNLOAD_PATH in public_paths
    assert DOWNLOAD_PATH not in protected_paths


def test_protected_media_operations_remain_protected_router():
    protected_paths = route_paths(
        router
    )

    expected = {
        "/upload",
        "/sign",
        "/storage/health",
        "/assets/{asset_id}/metadata",
        "/profiles/{profile_id}/assets",
    }

    assert expected <= protected_paths


def test_public_download_is_in_openapi():
    schema = app.openapi()

    path = (
        "/v1/avatar-media/"
        "public/assets/{asset_id}"
    )

    assert path in schema["paths"]
    assert "get" in schema["paths"][path]


def test_main_registers_separate_public_router():
    source = Path(
        "app/main.py"
    ).read_text(
        encoding="utf-8"
    )

    assert (
        "avatar_media_public_router"
        in source
    )

    assert (
        'tags=["avatar-media-public"]'
        in source
    )
