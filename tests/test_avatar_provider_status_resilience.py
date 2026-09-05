import asyncio
import threading
from dataclasses import asdict
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

import app.routes.avatar_provider as provider_routes
import app.services.avatar_provider_service as provider_module
from app.services.avatar_provider_service import (
    AvatarProviderJobState,
    AvatarProviderService,
    AvatarProviderStatusUnavailableError,
)


def install_client(monkeypatch, *, response=None, error=None):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            if error:
                raise error
            return response

    monkeypatch.setenv("TAVUS_API_KEY", "test-only")
    monkeypatch.setattr(provider_module.httpx, "AsyncClient", Client)


def state(status="training"):
    return AvatarProviderJobState(
        external_job_id="tavus:test-face",
        external_avatar_id="test-face",
        status=status,
        preview_url=None,
    )


@pytest.mark.parametrize("http_status", [401, 404, 429, 500, 503])
@pytest.mark.parametrize("training_status", ["training", "ready"])
def test_http_errors_never_overwrite_training_state(monkeypatch, http_status, training_status):
    install_client(monkeypatch, response=httpx.Response(http_status))
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = Mock()
    current = state(training_status)
    previous = asdict(current)
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service._fetch_tavus_status(current))
    assert asdict(current) == previous
    service._sync_tavus_status_to_profile.assert_not_called()


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b"not JSON"),
    httpx.Response(200, json=[]),
    httpx.Response(200, json={}),
    httpx.Response(200, json={"status": None}),
    httpx.Response(200, json={"status": " "}),
])
def test_invalid_provider_payload_never_changes_ready_state(monkeypatch, response):
    install_client(monkeypatch, response=response)
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = Mock()
    current = state("ready")
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service._fetch_tavus_status(current))
    assert current.status == "ready"
    service._sync_tavus_status_to_profile.assert_not_called()


def test_network_timeout_preserves_training_state(monkeypatch):
    install_client(monkeypatch, error=httpx.ReadTimeout("provider timeout"))
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = Mock()
    current = state()
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service._fetch_tavus_status(current))
    assert current.status == "training"
    service._sync_tavus_status_to_profile.assert_not_called()


def test_missing_provider_configuration_does_not_erase_ready_status(monkeypatch):
    monkeypatch.delenv("TAVUS_API_KEY", raising=False)
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = Mock()
    current = state("ready")
    with pytest.raises(AvatarProviderStatusUnavailableError):
        asyncio.run(service._fetch_tavus_status(current))
    assert current.status == "ready"
    service._sync_tavus_status_to_profile.assert_not_called()


@pytest.mark.parametrize("provider_status, expected", [("completed", "ready"), ("error", "failed"), ("started", "training")])
def test_explicit_provider_status_still_updates_the_job(monkeypatch, provider_status, expected):
    install_client(monkeypatch, response=httpx.Response(200, json={"status": provider_status}))
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = Mock()
    current = state()
    result = asyncio.run(service._fetch_tavus_status(current))
    assert result.status == expected
    service._sync_tavus_status_to_profile.assert_called_once()


def test_status_route_returns_retryable_503_without_raw_error(monkeypatch):
    profile_id = uuid4()
    monkeypatch.setattr(provider_routes, "require_profile_access", Mock())
    monkeypatch.setattr(provider_routes.avatar_provider_service, "require_training_job_profile_id", Mock(return_value=profile_id))
    monkeypatch.setattr(provider_routes.avatar_provider_service, "status", AsyncMock(side_effect=AvatarProviderStatusUnavailableError("private provider detail")))
    with pytest.raises(provider_routes.HTTPException) as caught:
        asyncio.run(provider_routes.get_avatar_provider_job_status("tavus:test-face", profile_id, object()))
    assert caught.value.status_code == 503
    assert caught.value.headers == {"Retry-After": "12"}
    assert "private provider detail" not in caught.value.detail


def test_training_job_owner_is_loaded_from_durable_repository():
    profile_id = uuid4()
    lookup = Mock(return_value={"profile_id": profile_id, "training_type": "avatar"})
    service = AvatarProviderService()
    service._profile_repository = SimpleNamespace(get_training_job_by_provider_job_id=lookup)
    assert service.require_training_job_profile_id("tavus:test-face") == profile_id
    lookup.assert_called_once_with(provider="tavus", provider_job_id="tavus:test-face")


@pytest.mark.parametrize("job", [None, {"training_type": "avatar"}, {"profile_id": uuid4(), "training_type": "voice"}])
def test_missing_or_wrong_type_training_job_is_not_authorized(job):
    service = AvatarProviderService()
    service._profile_repository = SimpleNamespace(get_training_job_by_provider_job_id=Mock(return_value=job))
    with pytest.raises(provider_module.DigitalHumanProfileNotFoundError):
        service.require_training_job_profile_id("tavus:test-face")


def test_preview_owner_uses_preview_repository():
    profile_id = uuid4()
    lookup = Mock(return_value={"profile_id": str(profile_id)})
    service = AvatarProviderService()
    service._profile_repository = SimpleNamespace(get_generated_preview_job_by_external_id=lookup)
    assert service.require_training_job_profile_id("tavus:video:test-video") == profile_id
    lookup.assert_called_once_with(provider="tavus", external_job_id="tavus:video:test-video")


def test_canonical_preview_identifier_routes_to_preview_status():
    service = AvatarProviderService()
    service.fetch_tavus_video_status = AsyncMock(return_value=state())
    service._load_tavus_training_state = Mock()
    asyncio.run(service.status("tavus:video:test-video"))
    service.fetch_tavus_video_status.assert_awaited_once_with("tavus:video:test-video")
    service._load_tavus_training_state.assert_not_called()


def test_foreign_profile_job_is_rejected_before_provider_polling(monkeypatch):
    service = AvatarProviderService()
    service._profile_repository = SimpleNamespace(get_training_job_by_provider_job_id=Mock(return_value={
        "profile_id": uuid4(), "training_type": "avatar",
    }))
    service.status = AsyncMock()
    monkeypatch.setattr(provider_routes, "avatar_provider_service", service)
    monkeypatch.setattr(provider_routes, "require_profile_access", Mock())
    with pytest.raises(provider_routes.HTTPException) as caught:
        asyncio.run(provider_routes.get_avatar_provider_job_status("tavus:test-face", uuid4(), object()))
    assert caught.value.status_code == 404
    service.status.assert_not_awaited()


def test_provider_projection_is_fenced_to_its_own_job():
    service = AvatarProviderService()
    profile_id = uuid4()
    repository = SimpleNamespace(
        get_training_job_by_provider_job_id=Mock(return_value={"profile_id": profile_id, "job_id": uuid4()}),
        update_training_job=Mock(),
        set_avatar_training=Mock(),
    )
    service._profile_repository = repository
    service._sync_tavus_status_to_profile(state=state("ready"), provider_payload={"status": "completed"})
    assert repository.set_avatar_training.call_args.kwargs["expected_provider_job_id"] == "tavus:test-face"


def test_training_lookup_runs_off_the_event_loop():
    main_thread = threading.get_ident()
    workers = []
    service = AvatarProviderService()

    def lookup(**kwargs):
        workers.append(threading.get_ident())
        return state()

    service._load_tavus_training_state = lookup
    service._fetch_tavus_status = AsyncMock(return_value=state())
    asyncio.run(service.status("tavus:test-face"))
    assert len(workers) == 1 and workers[0] != main_thread


def test_status_persistence_runs_off_the_event_loop(monkeypatch):
    install_client(monkeypatch, response=httpx.Response(200, json={"status": "completed"}))
    main_thread = threading.get_ident()
    workers = []
    service = AvatarProviderService()
    service._sync_tavus_status_to_profile = lambda **kwargs: workers.append(threading.get_ident())
    asyncio.run(service._fetch_tavus_status(state()))
    assert len(workers) == 1 and workers[0] != main_thread
