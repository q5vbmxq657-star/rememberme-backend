import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.emotional_reasoning_service import EmotionalReasoningService
from app.services.openai_memory_service import OpenAIMemoryService
from app.services.openai_persona_service import OpenAIPersonaService
from app.services.memory_ingestion_service import MemoryIngestionService


@pytest.mark.parametrize("service_type", [
    EmotionalReasoningService, OpenAIMemoryService, OpenAIPersonaService,
])
def test_model_requests_disable_response_storage(service_type):
    service = service_type.__new__(service_type)
    service.client = Mock()
    service.client.responses.create.return_value = SimpleNamespace(output_text="test response")
    arguments = {"model": "test-only", "temperature": 0.5, "max_output_tokens": 200}
    if service_type is OpenAIMemoryService:
        arguments.update(system_prompt="Test instructions", user_message="Hello")
    else:
        arguments["prompt"] = "Test prompt"
    assert service._call_model(**arguments) == "test response"
    assert service.client.responses.create.call_args.kwargs["store"] is False


def test_image_analysis_disables_response_storage():
    service = MemoryIngestionService.__new__(MemoryIngestionService)
    service.vision_model = "test-only"
    service.client = Mock()
    service.client.responses.create.return_value = SimpleNamespace(output_text="Test image")
    path = Path(__file__).parent / "fixtures" / "astronaut.png"
    assert service._analyze_image(path, "image/png", "Test context", authorize=Mock()) == "Test image"
    assert service.client.responses.create.call_args.kwargs["store"] is False


def test_all_responses_call_sites_explicitly_disable_storage():
    # Guard new call sites as well as the current runtime-tested services.
    root = Path(__file__).resolve().parents[1] / "app"
    checked = 0
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(function):
                if not (isinstance(call, ast.Call)
                        and isinstance(call.func, ast.Attribute)
                        and call.func.attr == "create"
                        and isinstance(call.func.value, ast.Attribute)
                        and call.func.value.attr == "responses"):
                    continue
                checked += 1
                direct = next((entry.value for entry in call.keywords if entry.arg == "store"), None)
                if direct is None:
                    unpacked = [entry.value.id for entry in call.keywords
                                if entry.arg is None and isinstance(entry.value, ast.Name)]
                    dictionaries = [node.value for node in ast.walk(function)
                                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict)
                                    and any(isinstance(target, ast.Name) and target.id in unpacked
                                            for target in node.targets)]
                    values = [value for dictionary in dictionaries
                              for key, value in zip(dictionary.keys, dictionary.values)
                              if isinstance(key, ast.Constant) and key.value == "store"]
                    assert len(values) == 1, f"Missing explicit storage policy: {path}:{call.lineno}"
                    direct = values[0]
                assert isinstance(direct, ast.Constant) and direct.value is False, f"Stored provider response: {path}:{call.lineno}"
    assert checked >= 7
