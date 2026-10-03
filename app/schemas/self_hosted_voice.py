"""Internal, versioned contract for STAY-owned speech synthesis."""

import base64
import binascii
import io
import wave
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

SUPPORTED_VOICE_LANGUAGES = frozenset("ar da de el en es fi fr he hi it ja ko ms nl no pl pt ru sv sw tr zh".split())


def normalize_voice_language(value: str) -> str:
    language = value.strip().lower().replace("_", "-").split("-")[0]
    language = {"nb": "no", "nn": "no"}.get(language, language)
    if language not in SUPPORTED_VOICE_LANGUAGES:
        raise ValueError("Unsupported voice language")
    return language


class SelfHostedVoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    contract_version: int = Field(default=1, ge=1, le=1)
    request_id: UUID
    profile_id: UUID
    voice_version: UUID
    consent_revision: int = Field(gt=0)
    model_revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    text: str = Field(min_length=1, max_length=600)
    language: str
    energy: float = Field(default=.5, ge=0, le=1, allow_inf_nan=False)
    reference_wav_base64: str = Field(min_length=1, max_length=1_300_000, repr=False)

    @field_validator("language")
    @classmethod
    def supported_language(cls, value: str) -> str:
        return normalize_voice_language(value)

    @field_validator("text")
    @classmethod
    def nonblank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Empty speech text")
        return value

    def reference_audio(self) -> bytes:
        try:
            data = base64.b64decode(self.reference_wav_base64, validate=True)
            with wave.open(io.BytesIO(data), "rb") as audio:
                if (audio.getnchannels() != 1 or audio.getsampwidth() != 2
                        or audio.getframerate() != 16000 or audio.getcomptype() != "NONE"
                        or not 3 * 16000 <= audio.getnframes() <= 30 * 16000):
                    raise ValueError("Reference must be 3-30 seconds of mono PCM16 at 16 kHz")
                if len(audio.readframes(audio.getnframes())) != audio.getnframes() * 2:
                    raise ValueError("Truncated reference")
            return data
        except (binascii.Error, wave.Error, EOFError) as error:
            raise ValueError("Invalid reference audio") from error
