"""Own CUDA in a disposable spawned process, never in the HTTP worker."""

import multiprocessing
import tempfile
import threading
import time
from functools import partial

from voice_runtime.engine import ChatterboxEngine, VoiceCapacityError


def _serve(connection, factory, temporary):
    tempfile.tempdir = temporary
    try:
        engine = factory()
        connection.send(("ready", engine.revision))
        while True:
            request = connection.recv()
            try:
                audio = engine.synthesize(request)
                connection.send(("audio", audio))
            except Exception:
                connection.send(("error", None))
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        # Never serialize provider errors, reference samples or request text.
        try:
            connection.send(("error", None))
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()


class SupervisedVoiceEngine:
    def __init__(self, model_directory=None, manifest_sha256=None, *, factory=None,
                 startup_timeout=120, inference_timeout=20):
        self.factory = factory or partial(ChatterboxEngine, model_directory, manifest_sha256)
        self.startup_timeout = startup_timeout
        self.inference_timeout = inference_timeout
        self.lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = False
        self._recovery_failures = 0
        self._next_recovery_at = 0.0
        self.process = self.connection = self.temporary = None
        self.revision = manifest_sha256
        self._start()

    @property
    def healthy(self):
        process = self.process
        try:
            return self._ready.is_set() and process is not None and process.is_alive()
        except ValueError:
            return False

    def _start(self):
        context = multiprocessing.get_context("spawn")
        self.temporary = tempfile.TemporaryDirectory(prefix="stay-voice-worker-")
        self.connection, child = context.Pipe()
        self.process = context.Process(target=_serve,
            args=(child, self.factory, self.temporary.name), daemon=True)
        try:
            self.process.start()
            child.close()
            if not self.connection.poll(self.startup_timeout):
                raise RuntimeError("Voice worker startup timed out")
            kind, revision = self.connection.recv()
            if kind != "ready" or (self.revision is not None and revision != self.revision):
                raise RuntimeError("Voice worker startup failed")
            self.revision = revision
            self._ready.set()
            self._recovery_failures = 0
            self._next_recovery_at = 0.0
        except BaseException:
            child.close()
            self._stop()
            raise

    def _stop(self):
        self._ready.clear()
        if self.process is not None:
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(2)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(2)
            if self.process.is_alive():
                raise RuntimeError("Voice worker could not be stopped")
            self.process.close()
            self.process = None
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def synthesize(self, request):
        if not self.lock.acquire(blocking=False):
            raise VoiceCapacityError()
        try:
            if self._closed:
                raise RuntimeError("Voice worker is closed")
            if not self.healthy:
                self._recover()
            try:
                self.connection.send(request)
                if not self.connection.poll(self.inference_timeout):
                    raise RuntimeError("Voice worker inference timed out")
                kind, audio = self.connection.recv()
                if kind != "audio" or not isinstance(audio, bytes) or len(audio) > 9_000_000:
                    raise RuntimeError("Voice worker generation failed")
                return audio
            except (EOFError, OSError, RuntimeError):
                # Kill the old CUDA owner before rebuilding. Never replay speech.
                self._stop()
                self._recover()
                raise RuntimeError("Voice worker recovered; retry the request") from None
        finally:
            self.lock.release()

    def recover_if_needed(self):
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self._closed:
                return
            if not self.healthy:
                if time.monotonic() >= self._next_recovery_at:
                    self._recover()
        finally:
            self.lock.release()

    def _recover(self):
        if time.monotonic() < self._next_recovery_at:
            raise VoiceCapacityError()
        try:
            self._stop()
            self._start()
        except Exception:
            self._recovery_failures = min(self._recovery_failures + 1, 6)
            self._next_recovery_at = time.monotonic() + min(2 ** self._recovery_failures, 60)
            raise

    def close(self):
        with self.lock:
            self._closed = True
            self._stop()
