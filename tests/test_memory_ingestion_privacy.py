from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.schemas.memory_ingestion import MemoryIngestionRequest
from app.services.memory_ingestion_service import MemoryIngestionService


@pytest.mark.parametrize("content_type", ["image/png", "audio/mp4", "video/mp4"])
def test_ingestion_rejects_foreign_media_before_reading_or_disclosing(monkeypatch, content_type):
    monkeypatch.setattr("app.services.memory_ingestion_service.require_profile_purposes",
        Mock(return_value=SimpleNamespace(revision=1)))
    service = MemoryIngestionService.__new__(MemoryIngestionService)
    service.client = Mock()
    service.media = Mock()
    service.media.get_metadata.return_value = SimpleNamespace(profile_id=uuid4(),
        storage_path="/must-not-be-read", content_type=content_type)
    request = MemoryIngestionRequest(profile_id=str(uuid4()), asset_id=str(uuid4()),
        asset_type="photo", title="Test")
    with pytest.raises(HTTPException) as caught:
        service.ingest(request)
    assert caught.value.status_code == 404
    service.client.responses.create.assert_not_called()
    service.client.audio.transcriptions.create.assert_not_called()
    service.client.close.assert_called_once()


def test_image_analysis_rechecks_permission_after_reading_media():
    service = MemoryIngestionService.__new__(MemoryIngestionService)
    service.client = Mock()
    service.vision_model = "test-only"
    authorize = Mock(side_effect=HTTPException(403, "Permission withdrawn"))
    image = Path(__file__).parent / "fixtures" / "astronaut.png"
    with pytest.raises(HTTPException):
        service._analyze_image(image, "image/png", "test", authorize=authorize)
    service.client.responses.create.assert_not_called()


def test_revoked_ingestion_closes_provider_client(monkeypatch):
    monkeypatch.setattr("app.services.memory_ingestion_service.require_profile_purposes",
        Mock(side_effect=HTTPException(403, "Permission withdrawn")))
    service = MemoryIngestionService.__new__(MemoryIngestionService)
    service.client = Mock()
    request = MemoryIngestionRequest(profile_id=str(uuid4()), asset_id=str(uuid4()),
        asset_type="text", title="Test", text="Private memory")
    with pytest.raises(HTTPException):
        service.ingest(request)
    service.client.responses.create.assert_not_called()
    service.client.close.assert_called_once()
