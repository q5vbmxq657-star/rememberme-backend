"""Real spawned-process recovery tests; no neural model or GPU is simulated as accepted."""
import os
import tempfile
import time
from pathlib import Path

import pytest

from voice_runtime.supervisor import SupervisedVoiceEngine
from voice_runtime.engine import VoiceCapacityError
from voice_runtime.server import create_app
from fastapi.testclient import TestClient


class ProcessFixture:
    revision = "a" * 64

    def synthesize(self, request):
        Path(tempfile.gettempdir(), "reference.wav").write_bytes(b"private fixture")
        if request == "hang":
            time.sleep(60)
        if request == "crash":
            os._exit(3)
        if request == "error":
            raise RuntimeError("private diagnostic")
        return b"audio fixture"


@pytest.mark.parametrize("failure", ["hang", "crash", "error"])
def test_worker_replaced_after_failure_and_private_files_removed(failure):
    engine = SupervisedVoiceEngine(factory=ProcessFixture, startup_timeout=10, inference_timeout=.2)
    try:
        old_pid = engine.process.pid
        old_directory = Path(engine.temporary.name)
        with pytest.raises(RuntimeError, match="recovered"):
            engine.synthesize(failure)
        assert engine.healthy
        assert engine.process.pid != old_pid
        assert not old_directory.exists()
        assert engine.synthesize("ok") == b"audio fixture"
        directory = Path(engine.temporary.name)
    finally:
        engine.close()
    assert not engine.healthy
    assert not directory.exists()


def test_worker_rejects_wrong_model_revision():
    with pytest.raises(RuntimeError, match="startup failed"):
        SupervisedVoiceEngine(manifest_sha256="b" * 64, factory=ProcessFixture, startup_timeout=10)


def test_closed_worker_cannot_be_restarted_by_late_request_or_monitor():
    engine = SupervisedVoiceEngine(factory=ProcessFixture, startup_timeout=10)
    directory = Path(engine.temporary.name)
    engine.close()
    engine.recover_if_needed()
    with pytest.raises(RuntimeError, match="closed"):
        engine.synthesize("ok")
    engine.close()
    assert not engine.healthy
    assert engine.process is None
    assert engine.connection is None
    assert engine.temporary is None
    assert not directory.exists()


def test_failed_recovery_backs_off_and_requests_cannot_bypass_it(monkeypatch):
    engine = SupervisedVoiceEngine(factory=ProcessFixture, startup_timeout=10)
    start = engine._start
    attempts = []
    now = [100.0]
    monkeypatch.setattr("voice_runtime.supervisor.time.monotonic", lambda: now[0])

    def unavailable():
        attempts.append(now[0])
        raise RuntimeError("Model unavailable")

    try:
        engine._stop()
        monkeypatch.setattr(engine, "_start", unavailable)
        with pytest.raises(RuntimeError, match="Model unavailable"):
            engine.recover_if_needed()
        engine.recover_if_needed()
        with pytest.raises(VoiceCapacityError):
            engine.synthesize("ok")
        assert attempts == [100.0]
        now[0] = 102.0
        with pytest.raises(RuntimeError):
            engine.recover_if_needed()
        assert engine._next_recovery_at == 106.0
        monkeypatch.setattr(engine, "_start", start)
        now[0] = 106.0
        engine.recover_if_needed()
        assert engine.healthy
        assert engine._recovery_failures == 0
        assert engine._next_recovery_at == 0.0
    finally:
        engine.close()


def test_idle_crash_recovers_without_a_synthesis_request():
    engine = SupervisedVoiceEngine(factory=ProcessFixture, startup_timeout=10)
    token = "test-only-credential-" * 3
    with TestClient(create_app(engine_factory=lambda: engine, token=token)) as client:
        old_pid = engine.process.pid
        engine.process.kill()
        engine.process.join(2)
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/health/ready", headers=headers).status_code == 503
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if client.get("/health/ready", headers=headers).status_code == 200:
                break
            time.sleep(.05)
        else:
            pytest.fail("Idle worker was not recovered")
        assert engine.process.pid != old_pid
    assert not engine.healthy
