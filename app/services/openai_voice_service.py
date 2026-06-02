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
        with tempfile.NamedTemporaryFile(delete=True, suffix=".m4a") as temp:
            content = await file.read()
            temp.write(content)
            temp.flush()

            with open(temp.name, "rb") as audio_file:
                transcript = self.client.audio.transcriptions.create(
                    model=self.transcribe_model,
                    file=audio_file
                )

        return {"text": transcript.text}

    def synthesize(self, text: str):
        if not text.strip():
            raise RuntimeError("Text is empty.")

        response = self.client.audio.speech.create(
            model=self.tts_model,
            voice="coral",
            input=text
        )

        return BytesIO(response.content)
