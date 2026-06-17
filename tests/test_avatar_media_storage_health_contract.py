from app.main import app


def test_storage_health_route_is_registered():
    schema = app.openapi()

    assert (
        "/v1/avatar-media/storage/health"
        in schema["paths"]
    )

    operation = (
        schema["paths"]
        ["/v1/avatar-media/storage/health"]
        ["get"]
    )

    assert (
        operation["responses"]["200"]
        ["content"]["application/json"]
        ["schema"]["$ref"]
        == (
            "#/components/schemas/"
            "AvatarMediaStorageHealthResponse"
        )
    )


def test_storage_health_schema_is_complete():
    schema = app.openapi()

    health_schema = (
        schema["components"]["schemas"]
        ["AvatarMediaStorageHealthResponse"]
    )

    properties = set(
        health_schema["properties"]
    )

    required = {
        "status",
        "environment",
        "storage_backend",
        "storage_root",
        "storage_root_explicit",
        "storage_exists",
        "storage_is_directory",
        "storage_writable",
        "public_base_url_configured",
        "secure_public_url",
        "signing_secret_configured",
        "signing_secret_secure",
        "production_ready",
    }

    assert required <= properties
