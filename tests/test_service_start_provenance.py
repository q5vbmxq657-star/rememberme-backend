import importlib.util
import json
from pathlib import Path

import pytest


def load_start_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "start_service.py"
    spec = importlib.util.spec_from_file_location("stay_start_service", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("role", ["web", "avatar-worker"])
def test_start_reports_provenance_before_existing_process_handoff(monkeypatch, capsys, role):
    module = load_start_module()
    revision = "a" * 40
    deployment = "60147cef-5a74-4472-a257-d05218155897"
    monkeypatch.setenv("STAY_SERVICE_ROLE", role)
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", revision)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", deployment)
    monkeypatch.setenv("TAVUS_API_KEY", "must-not-appear")
    calls = []

    def execvp(executable, arguments):
        calls.append((executable, arguments))
        output = capsys.readouterr().out
        assert "must-not-appear" not in output
        assert json.loads(output) == {
            "event": "stay_service_start", "role": role,
            "source_revision": revision, "deployment_id": deployment,
        }

    monkeypatch.setattr(module.os, "execvp", execvp)
    module.main()
    assert len(calls) == 1
    assert calls[0][0] == module.sys.executable
    expected = "uvicorn" if role == "web" else "app.workers.avatar_tavus_worker"
    assert calls[0][1][1:3] == ["-m", expected]


@pytest.mark.parametrize("value", ["", "unknown", "secret\nforged log", "a" * 41])
def test_missing_or_invalid_provenance_is_not_invented_or_logged(monkeypatch, capsys, value):
    module = load_start_module()
    monkeypatch.setenv("STAY_SERVICE_ROLE", "web")
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", value)
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", value)
    monkeypatch.setattr(module.os, "execvp", lambda *_: None)
    module.main()
    event = json.loads(capsys.readouterr().out)
    assert event["source_revision"] is None
    assert event["deployment_id"] is None


def test_invalid_role_does_not_launch_or_echo_untrusted_configuration(monkeypatch, capsys):
    module = load_start_module()
    monkeypatch.setenv("STAY_SERVICE_ROLE", "secret\nforged log")
    monkeypatch.setattr(module.os, "execvp", lambda *_: pytest.fail("Invalid role started"))
    with pytest.raises(RuntimeError, match="^Unsupported STAY_SERVICE_ROLE$"):
        module.main()
    assert capsys.readouterr().out == ""
