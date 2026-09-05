import os
from openai import AsyncOpenAI


class VoiceRecordingTooLargeError(ValueError):
    pass


class OpenAIVoiceService:
    MAX_RECORDING_BYTES = 25 * 1024 * 1024

    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.api_key = api_key
        self.transcribe_model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")

    async def transcribe(self, file):
        original_filename = getattr(file, "filename", "") or "recording.m4a"
        raw_suffix = os.path.splitext(original_filename)[1].lower().strip()
        rememberme_stt_suffix = raw_suffix if raw_suffix in {
            ".wav",
            ".m4a",
            ".mp3",
            ".mp4",
            ".mpeg",
            ".mpga",
            ".webm",
            ".ogg",
            ".oga",
            ".flac"
        } else ".m4a"

        content = await file.read(self.MAX_RECORDING_BYTES + 1)
        if len(content) > self.MAX_RECORDING_BYTES:
            raise VoiceRecordingTooLargeError("Recording exceeds the transcription size limit.")

        if not content:
            return {
                "text": "",
                "diagnostic": {
                    "filename": original_filename,
                    "suffix": rememberme_stt_suffix,
                    "bytes": 0,
                    "reason": "empty_upload"
                }
            }

        async with AsyncOpenAI(
            api_key=self.api_key,
            timeout=45.0,
            max_retries=1,
        ) as client:
            transcript = await client.audio.transcriptions.create(
                model=self.transcribe_model,
                file=(f"recording{rememberme_stt_suffix}", content),
            )

        return {
            "text": transcript.text,
            "diagnostic": {
                "filename": original_filename,
                "suffix": rememberme_stt_suffix,
                "bytes": len(content),
                "model": self.transcribe_model
            }
        }
