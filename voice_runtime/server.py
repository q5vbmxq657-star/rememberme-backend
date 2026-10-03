"""Run behind private TLS ingress, one process per GPU replica."""

import hmac
import os
from contextlib import asynccontextmanager

import anyio
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import ValidationError

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest
from voice_runtime.engine import VoiceCapacityError
from voice_runtime.supervisor import SupervisedVoiceEngine


def create_app(*, engine_factory=None, token=None) -> FastAPI:
    secret = token if token is not None else os.environ.get("STAY_VOICE_RUNTIME_TOKEN", "")
    if len(secret) < 32:
        raise RuntimeError("A private runtime credential of at least 32 characters is required")

    @asynccontextmanager
    async def lifespan(app):
        factory = engine_factory or (lambda: SupervisedVoiceEngine(
            os.environ["STAY_VOICE_MODEL_DIRECTORY"], os.environ["STAY_VOICE_MODEL_REVISION"]))
        app.state.engine = await anyio.to_thread.run_sync(factory)
        app.state.capacity = anyio.CapacityLimiter(1)

        async def monitor_worker():
            recover = getattr(app.state.engine, "recover_if_needed", None)
            if recover is None:
                return
            while True:
                await anyio.sleep(2)
                try:
                    await anyio.to_thread.run_sync(recover)
                except Exception:
                    # Readiness stays unavailable; retry without exposing model diagnostics.
                    continue

        try:
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(monitor_worker)
                try:
                    yield
                finally:
                    tasks.cancel_scope.cancel()
        finally:
            close = getattr(app.state.engine, "close", None)
            if close:
                await anyio.to_thread.run_sync(close)
            app.state.engine = None

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authorize(request):
        authorization = request.headers.get("authorization", "")
        if not hmac.compare_digest(authorization.encode(), f"Bearer {secret}".encode()):
            raise HTTPException(401, "Unauthorized")

    @app.get("/health/ready")
    async def ready(request: Request):
        authorize(request)
        if app.state.capacity.borrowed_tokens or not getattr(app.state.engine, "healthy", True):
            raise HTTPException(503, "Voice capacity unavailable", headers={"Retry-After": "1"})
        return {"status": "ready", "contract_version": 1,
                "model_revision": app.state.engine.revision}

    @app.post("/v1/synthesize")
    async def synthesize(request: Request):
        authorize(request)
        try:
            app.state.capacity.acquire_nowait()
        except anyio.WouldBlock:
            raise HTTPException(503, "Voice capacity unavailable", headers={"Retry-After": "1"})
        try:
            data = bytearray()
            try:
                with anyio.fail_after(10):
                    async for chunk in request.stream():
                        data.extend(chunk)
                        if len(data) > 1_350_000:
                            raise HTTPException(413, "Request too large")
            except TimeoutError:
                raise HTTPException(408, "Request upload timed out") from None
            try:
                payload = SelfHostedVoiceRequest.model_validate_json(bytes(data))
                payload.reference_audio()
                if payload.model_revision != app.state.engine.revision:
                    raise ValueError("Model changed")
            except (ValidationError, ValueError):
                raise HTTPException(422, "Invalid voice request") from None
            try:
                # Cancellation must not release capacity while CUDA still owns the model.
                audio = await anyio.to_thread.run_sync(app.state.engine.synthesize, payload)
            except VoiceCapacityError:
                raise HTTPException(503, "Voice capacity unavailable") from None
            except Exception:
                raise HTTPException(503, "Voice generation failed") from None
            return Response(audio, media_type="audio/wav", headers={
                "Cache-Control": "no-store", "X-STAY-Request-ID": str(payload.request_id),
                "X-STAY-Profile-ID": str(payload.profile_id),
                "X-STAY-Voice-Version": str(payload.voice_version),
                "X-STAY-Model-Revision": payload.model_revision,
            })
        finally:
            app.state.capacity.release()

    return app
