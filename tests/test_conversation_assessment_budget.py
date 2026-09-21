from unittest.mock import Mock

import pytest

from app.schemas.emotional_reasoning import EmotionalReasoningRequest
from app.services import emotional_reasoning_service, memory_chat_openai_client


@pytest.mark.parametrize('raw, expected', [('nan',25),('inf',25),('-inf',25),('bad',25),('0',5),('120',60),('12.5',12.5)])
def test_model_wait_budget_is_finite_and_bounded(raw, expected):
    assert memory_chat_openai_client._bounded_timeout(raw) == expected


def test_safety_assessment_uses_canonical_bounded_client(monkeypatch):
    client = Mock()
    constructor = Mock(return_value=client)
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-only')
    monkeypatch.setenv('OPENAI_MEMORY_CHAT_TIMEOUT_SECONDS', '18')
    monkeypatch.setattr(memory_chat_openai_client, 'OpenAI', constructor)
    service = emotional_reasoning_service.EmotionalReasoningService()
    constructor.assert_called_once_with(api_key='synthetic-test-only', timeout=18.0, max_retries=0)
    service.close()
    client.close.assert_called_once()


def test_invalid_safety_result_does_not_disclose_raw_model_content():
    service = object.__new__(emotional_reasoning_service.EmotionalReasoningService)
    service.orchestration = Mock()
    service._call_model = Mock(return_value='PRIVATE-MODEL-OUTPUT-not-json')
    with pytest.raises(RuntimeError) as caught:
        service.assess(EmotionalReasoningRequest(user_message='Hello', recent_messages=[]))
    assert 'PRIVATE-MODEL-OUTPUT' not in str(caught.value)


def test_incomplete_setup_closes_assessment_client(monkeypatch):
    client = Mock()
    monkeypatch.setattr(emotional_reasoning_service, 'make_memory_chat_openai_client', lambda: client)
    monkeypatch.setattr(emotional_reasoning_service, 'AIOrchestrationService', Mock(side_effect=RuntimeError('configuration')))
    with pytest.raises(RuntimeError):
        emotional_reasoning_service.EmotionalReasoningService()
    client.close.assert_called_once()
