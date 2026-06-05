import os
from io import BytesIO
from typing import Optional

import httpx


class ElevenLabsVoiceService:
    def __init__(self):
        self.api_key = os.getenv("ELEVENLABS_API_KEY")
        if not self.api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is missing.")

        self.default_voice_id = os.getenv("ELEVENLABS_DEFAULT_VOICE_ID")
        if not self.default_voice_id:
            raise RuntimeError("ELEVENLABS_DEFAULT_VOICE_ID is missing.")

        self.model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

    async def list_voices(self):
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(
                "https://api.elevenlabs.io/v1/voices",
                headers={"xi-api-key": self.api_key},
            )

        response.raise_for_status()
        return response.json()

    async def synthesize(self, text: str, voice_id: Optional[str] = None):
        clean = text.strip()
        if not clean:
            raise RuntimeError("Text is empty.")

        selected_voice_id = voice_id or self.default_voice_id

        payload = {
            "text": clean,
            "model_id": self.model_id,
            "voice_settings": {
                "stability": 0.48,
                "similarity_boost": 0.82,
                "style": 0.22,
                "use_speaker_boost": True,
            },
        }

        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{selected_voice_id}",
                headers={
                    "xi-api-key": self.api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise RuntimeError(f"ElevenLabs TTS failed: {response.text}")

        return BytesIO(response.content)


elevenlabs_voice_service = ElevenLabsVoiceService()
