"""Internal transport only; callers must resolve ownership and consent first."""

import asyncio
import io
import json
import wave
from urllib.parse import urlsplit

import httpx

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest


class SelfHostedVoiceUnavailableError(Exception):
    pass


class SelfHostedVoiceClient:
    REQUEST_TIMEOUT_SECONDS = 30

    def __init__(self, base_url: str, token: str):
        url = urlsplit(base_url)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in {"", "/"} or len(token) < 32):
            raise ValueError("Private HTTPS endpoint and credential required")
        self.base_url = base_url.rstrip("/")
        self.token = token

    async def verify_readiness(self, model_revision: str) -> None:
        try:
            async with asyncio.timeout(10):
                async with httpx.AsyncClient(timeout=5, follow_redirects=False) as client:
                    async with client.stream("GET", self.base_url + "/health/ready",
                            headers={"Authorization": f"Bearer {self.token}"}) as response:
                        if response.status_code != 200:
                            raise SelfHostedVoiceUnavailableError("Voice runtime is not ready")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 4096:
                                raise SelfHostedVoiceUnavailableError("Invalid voice readiness response")
                        payload = json.loads(body)
                        if (not isinstance(payload, dict) or payload.get("status") != "ready"
                                or type(payload.get("contract_version")) is not int
                                or payload["contract_version"] != 1
                                or payload.get("model_revision") != model_revision):
                            raise SelfHostedVoiceUnavailableError("Voice runtime contract mismatch")
        except (httpx.HTTPError, TimeoutError, ValueError):
            raise SelfHostedVoiceUnavailableError("Voice readiness could not be verified") from None

    async def synthesize(self, request: SelfHostedVoiceRequest) -> bytes:
        try:
            async with asyncio.timeout(self.REQUEST_TIMEOUT_SECONDS):
                return await self._synthesize(request)
        except TimeoutError:
            raise SelfHostedVoiceUnavailableError("Voice generation timed out") from None

    async def _synthesize(self, request: SelfHostedVoiceRequest) -> bytes:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client:
            async with client.stream("POST", self.base_url + "/v1/synthesize",
                    headers={"Authorization": f"Bearer {self.token}"},
                    content=request.model_dump_json()) as response:
                expected = {"X-STAY-Request-ID": str(request.request_id),
                    "X-STAY-Profile-ID": str(request.profile_id),
                    "X-STAY-Voice-Version": str(request.voice_version),
                    "X-STAY-Model-Revision": request.model_revision}
                if (response.status_code != 200 or response.headers.get("content-type") != "audio/wav"
                        or any(response.headers.get(key) != value for key, value in expected.items())):
                    raise SelfHostedVoiceUnavailableError("Voice response could not be verified")
                audio = bytearray()
                async for chunk in response.aiter_bytes():
                    audio.extend(chunk)
                    if len(audio) > 9_000_000:
                        raise SelfHostedVoiceUnavailableError("Voice response exceeded its limit")
        try:
            with wave.open(io.BytesIO(audio), "rb") as decoded:
                if (decoded.getnchannels() != 1 or decoded.getsampwidth() != 2
                        or not 8000 <= decoded.getframerate() <= 48000
                        or not 0 < decoded.getnframes() <= decoded.getframerate() * 90):
                    raise ValueError("Invalid audio")
                pcm = decoded.readframes(decoded.getnframes())
                if len(pcm) != decoded.getnframes() * 2 or not any(pcm):
                    raise ValueError("Truncated or silent audio")
        except (wave.Error, EOFError, ValueError) as error:
            raise SelfHostedVoiceUnavailableError("Voice response is not valid audio") from error
        return bytes(audio)
