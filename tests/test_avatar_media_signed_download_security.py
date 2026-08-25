from pathlib import Path

from app.routes.avatar_media import (
    public_router,
)


def test_public_router_contains_only_signed_download():
    paths = {
        route.path
        for route in public_router.routes
    }

    assert paths == {
        "/public/assets/{asset_id}"
    }


def test_signed_download_requires_expiry_and_signature():
    download_routes = [
        route
        for route in public_router.routes
        if route.path
        == "/public/assets/{asset_id}"
    ]

    assert len(download_routes) == 1

    route = download_routes[0]

    parameter_names = {
        parameter.name
        for parameter
        in route.dependant.query_params
    }

    assert "expires" in parameter_names
    assert "signature" in parameter_names


def test_evidence_archive_does_not_delete_binary():
    source = Path(
        "app/routes/avatar_evidence.py"
    ).read_text(
        encoding="utf-8"
    )

    assert "delete_asset" not in source
