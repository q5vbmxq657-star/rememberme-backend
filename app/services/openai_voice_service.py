import os
import tempfile
from io import BytesIO
from openai import OpenAI


class OpenAIVoiceService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.transcribe_model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")
        self.tts_model = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")

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

        content = await file.read()

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

        with tempfile.NamedTemporaryFile(delete=True, suffix=rememberme_stt_suffix) as temp:
            temp.write(content)
            temp.flush()

            with open(temp.name, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model=self.transcribe_model,
                    file=audio_file,
                    language="de"
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

    def synthesize(self, text: str):
        if not text.strip():
            raise RuntimeError("Text is empty.")

        response = self.client.audio.speech.create(
            model=self.tts_model,
            voice="coral",
            input=text
        )

        return BytesIO(response.content)
