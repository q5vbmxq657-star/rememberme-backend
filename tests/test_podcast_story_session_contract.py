from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.schemas.podcast import (
    PodcastInvitationRecord,
    PodcastInvitationStatus,
    PodcastResponseRecord,
)
from app.services.podcast_service import PodcastService, PodcastServiceError
from app.services import podcast_service


class PodcastRepositoryStub:
    def __init__(self, record, responses):
        self.record = record
        self.responses = responses

    def list_completed_responses(self, *, profile_id):
        assert profile_id == self.record.profile_id
        return [(self.record, response) for response in self.responses]

    def list_completed(self, *, profile_id):
        assert profile_id == self.record.profile_id
        return []

    def mark_voice_training_used(self, *, invitation_id, profile_id):
        assert invitation_id == self.record.invitation_id
        assert profile_id == self.record.profile_id
        self.record.voice_training_used_at = datetime.now(timezone.utc)
        return True

    def get_by_token_digest(self, token_digest, *, for_update=False):
        assert token_digest
        assert for_update is False
        return self.record

    def mark_status(
        self,
        *,
        invitation_id,
        expected_statuses,
        status,
        response_audio_asset_id=None,
        safe_error_code=None,
    ):
        assert invitation_id == self.record.invitation_id
        assert self.record.status in expected_statuses
        self.record.status = status
        self.record.safe_error_code = safe_error_code
        self.record.updated_at = datetime.now(timezone.utc)
        return self.record


class PodcastMediaStub:
    def sign_download_url(self, *, asset_id, base_url, expires_in_seconds):
        return SimpleNamespace(signed_url=f"{base_url}/signed/{asset_id}")

    def get_metadata(self, asset_id):
        return SimpleNamespace(content_type="audio/mp4")


def make_record(*, consent=True):
    now = datetime.now(timezone.utc)
    return PodcastInvitationRecord(
        invitation_id=uuid4(),
        profile_id=uuid4(),
        created_by_user_id=uuid4(),
        requester_name="Anna",
        subject_name="Peter",
        prompt="Tell me your story.",
        theme="childhood",
        prompt_sequence=[],
        prompt_audio_asset_id=None,
        response_audio_asset_id=None,
        memory_id=uuid4(),
        status=PodcastInvitationStatus.completed,
        transcript="A story",
        summary="A story",
        memory_payload={},
        safe_error_code=None,
        expires_at=now + timedelta(days=1),
        created_at=now,
        updated_at=now,
        completed_at=now,
        speaker_confirmed_subject=consent,
        voice_training_consent_granted=consent,
        voice_training_consented_at=now if consent else None,
    )


def make_response(record, index):
    now = datetime.now(timezone.utc)
    return PodcastResponseRecord(
        response_id=uuid4(),
        invitation_id=record.invitation_id,
        turn_index=index,
        prompt_id=f"childhood_{index}",
        category="childhood",
        question=f"Question {index}",
        audio_asset_id=uuid4(),
        memory_id=uuid4(),
        transcript=f"Answer {index}",
        summary=f"Summary {index}",
        memory_payload={
            "title": f"Story {index}",
            "avatar_memory_text": f"Grounded memory {index}",
            "extracted_topics": ["family"],
            "confidence_score": 0.9,
        },
        created_at=now,
    )


def test_story_session_prompt_selection_is_deterministic_and_bounded():
    service = object.__new__(PodcastService)

    first = service._session_prompts(
        theme="childhood", locale="de-DE", opening_prompt=None
    )
    second = service._session_prompts(
        theme="childhood", locale="de-DE", opening_prompt=None
    )

    assert first == second
    assert len(first) == PodcastService.SESSION_PROMPT_COUNT
    assert len({prompt["prompt_id"] for prompt in first}) == len(first)
    assert all(prompt["category"] == "childhood" for prompt in first)


def test_unknown_theme_uses_one_canonical_life_story_fallback():
    service = object.__new__(PodcastService)
    assert service._normalized_theme("not-a-real-theme") == "life_story"


def test_read_only_service_construction_does_not_require_openai(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    service = PodcastService(
        repository=PodcastRepositoryStub(make_record(), []),
        media=PodcastMediaStub(),
    )

    assert service._openai_client is None


def test_expired_processing_lease_recovers_the_same_invitation_for_retry():
    record = make_record()
    record.status = PodcastInvitationStatus.processing
    record.updated_at = datetime.now(timezone.utc) - timedelta(minutes=16)
    repository = PodcastRepositoryStub(record, [])
    service = PodcastService(repository=repository, media=PodcastMediaStub())

    recovered = service._active_record("a" * 32)

    assert recovered.invitation_id == record.invitation_id
    assert recovered.status == PodcastInvitationStatus.retryable_failed
    assert recovered.safe_error_code == "processing_lease_expired"


def test_imports_are_profile_bound_categorized_and_offer_voice_only_once():
    record = make_record(consent=True)
    responses = [make_response(record, index) for index in range(3)]
    responses[1].transcript = "A substantially longer answer with enough natural speech for voice preparation"
    repository = PodcastRepositoryStub(record, responses)
    service = PodcastService(
        repository=repository,
        media=PodcastMediaStub(),
        openai_client=object(),
    )

    imports = service.list_imports(
        profile_id=record.profile_id,
        backend_base_url="https://api.stay.example",
    )

    assert [item.profile_id for item in imports] == [record.profile_id] * 3
    assert [item.category for item in imports] == ["childhood"] * 3
    assert all("childhood" in item.extracted_topics for item in imports)
    assert [item.voice_training_eligible for item in imports] == [False, True, False]

    assert service.mark_voice_training_used(
        invitation_id=record.invitation_id,
        profile_id=record.profile_id,
    )
    refreshed = service.list_imports(
        profile_id=record.profile_id,
        backend_base_url="https://api.stay.example",
    )
    assert not any(item.voice_training_eligible for item in refreshed)


def test_voice_training_is_never_offered_without_source_consent():
    record = make_record(consent=False)
    response = make_response(record, 0)
    service = PodcastService(
        repository=PodcastRepositoryStub(record, [response]),
        media=PodcastMediaStub(),
        openai_client=object(),
    )

    imports = service.list_imports(
        profile_id=record.profile_id,
        backend_base_url="https://api.stay.example",
    )
    assert imports[0].voice_training_eligible is False


@pytest.mark.parametrize("failure", [None, "index", "complete"])
def test_interview_publishes_one_batch_and_rolls_back_only_confirmed_publication(monkeypatch, failure):
    record = make_record(consent=False)
    record.status = PodcastInvitationStatus.pending
    record.prompt_sequence = [
        {"prompt_id": f"question-{index}", "category": "childhood", "question": f"Question {index}"}
        for index in range(3)
    ]
    repository = PodcastRepositoryStub(record, [])
    vector = Mock()
    generation = (7, uuid4())
    vector.index_external_memories.return_value = generation
    if failure == "index":
        vector.index_external_memories.side_effect = RuntimeError("Synthetic publication failure")

    def claim(**kwargs):
        record.status = PodcastInvitationStatus.recording
        return record

    def complete(**kwargs):
        vector.index_external_memories.assert_called_once()
        assert len(kwargs["responses"]) == 3
        assert kwargs["voice_training_consent_granted"] is False
        if failure == "complete":
            raise RuntimeError("Synthetic session completion failure")
        record.status = PodcastInvitationStatus.completed
        return record

    repository.claim_recording_upload = Mock(side_effect=claim)
    repository.complete_session = Mock(side_effect=complete)
    media = PodcastMediaStub()
    media.upload = AsyncMock(side_effect=[SimpleNamespace(asset_id=str(uuid4())) for _ in range(3)])
    media.delete_asset = Mock()
    ingestion = SimpleNamespace(
        title="A story", summary="Summary", transcript="A sufficiently long interview answer",
        emotional_tags=[], confidence_score=0.9, model_dump=lambda **kwargs: {},
    )
    monkeypatch.setattr(podcast_service, "PGVectorMemoryService", lambda: vector)
    monkeypatch.setattr(podcast_service, "MemoryIngestionService", lambda: SimpleNamespace(ingest=lambda request: ingestion))
    service = PodcastService(repository=repository, media=media, openai_client=object())
    request = service.complete_session(token="a" * 32, files=[object() for _ in range(3)],
        speaker_confirmed_subject=False, voice_training_consent_granted=False,
        backend_base_url="https://api.stay.example")
    if failure:
        with pytest.raises(PodcastServiceError):
            asyncio.run(request)
        assert record.status == PodcastInvitationStatus.retryable_failed
        assert media.delete_asset.call_count == 3
    else:
        result = asyncio.run(request)
        assert result.status == PodcastInvitationStatus.completed
        media.delete_asset.assert_not_called()
    vector.index_external_memories.assert_called_once()
    batch = vector.index_external_memories.call_args.args[0]
    assert batch.profile_id == str(record.profile_id)
    assert len(batch.memories) == 3
    assert len({item.id for item in batch.memories}) == 3
    assert all(item.profile_id == batch.profile_id for item in batch.memories)
    if failure == "complete":
        vector.delete_external_memories.assert_called_once_with(
            profile_id=str(record.profile_id), memory_ids=[item.id for item in batch.memories],
            expected_generation=generation,
        )
    else:
        vector.delete_external_memories.assert_not_called()
    if failure == "index":
        repository.complete_session.assert_not_called()
