from pathlib import Path

from app.main import app
from app.routes.realtime import RealtimeAvatarSessionResponse


REQUIRED_PRODUCT_PATHS = {
    "/health",
    "/v1/auth/apple/exchange",
    "/v1/auth/session/refresh",
    "/v1/auth/session/logout",
    "/v1/auth/account",
    "/v1/profiles",
    "/v1/memory/chat",
    "/v1/streaming-memory/chat",
    "/v1/memory-ingestion/ingest",
    "/v1/memory-retrieval/retrieve",
    "/v1/vector-memory/index",
    "/v1/vector-memory/search",
    "/v1/persona/extract",
    "/v1/voice/transcribe",
    "/v1/elevenlabs/tts",
    "/v1/realtime/avatar/session",
    "/v1/avatar-state/profiles/{profile_id}",
    "/v1/avatar-media/upload",
    "/v1/avatar-evidence/profiles/{profile_id}/assets",
    "/v1/avatar-provider/submit",
    "/v1/avatar-provider/status/{external_job_id}",
    "/v1/avatar-video/tavus/create",
    "/v1/avatar-runtime/sessions",
}


REMOVED_LEGACY_PATHS = {
    "/v1/avatar-training/readiness",
    "/v1/avatar-identity/blueprint",
    "/v1/avatar-identity-fusion/fuse",
    "/v1/avatar-motion/readiness",
    "/v1/avatar-generation/readiness",
    "/v1/avatar-runtime/plan",
    "/v1/realtime/client-secret",
    "/v1/conversation-memory/summarize",
    "/v1/emotional-reasoning/assess",
}


def test_product_api_exposes_the_canonical_surface():
    paths = set(app.openapi()["paths"])

    assert REQUIRED_PRODUCT_PATHS <= paths
    assert REMOVED_LEGACY_PATHS.isdisjoint(paths)


def test_realtime_session_never_exposes_raw_provider_payloads():
    assert "raw" not in RealtimeAvatarSessionResponse.model_fields


def test_user_routes_do_not_expose_raw_errors_or_memory_content_logs():
    routes_root = Path(__file__).parents[1] / "app" / "routes"
    route_source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in routes_root.glob("*.py")
    )

    assert "str(error)" not in route_source
    assert "traceback.print_exc" not in route_source
    assert "print(\"PROFILE:" not in route_source
    assert "print(\"QUERY:" not in route_source
    assert "print(\"EFFECTIVE QUERY:" not in route_source


def test_streaming_errors_are_sanitized():
    service_path = (
        Path(__file__).parents[1]
        / "app"
        / "services"
        / "streaming_memory_service.py"
    )
    source = service_path.read_text(encoding="utf-8")

    assert '"message": str(error)' not in source
