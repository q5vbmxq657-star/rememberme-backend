import os
import json
import base64
import io
from pathlib import Path
from typing import Optional
from openai import OpenAI

from app.schemas.memory_ingestion import MemoryIngestionRequest, MemoryIngestionResponse
from app.services.avatar_media_storage_service import AvatarMediaStorageService


class MemoryIngestionService:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

        self.client = OpenAI(api_key=api_key)
        self.media = AvatarMediaStorageService()
        self.vision_model = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")
        self.reasoning_model = os.getenv("OPENAI_MEMORY_ANALYSIS_MODEL", "gpt-4o-mini")
        self.transcribe_model = os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")

    def ingest(self, request: MemoryIngestionRequest) -> MemoryIngestionResponse:
        transcript = None
        visual_description = None

        if request.text and request.text.strip():
            transcript = request.text.strip()
        else:
            metadata = self.media.get_metadata(request.asset_id)
            path = Path(metadata.storage_path)

            if metadata.content_type.startswith("audio/"):
                transcript = self._transcribe(path)

            elif metadata.content_type.startswith("image/"):
                visual_description = self._analyze_image(
                    path=path,
                    content_type=metadata.content_type,
                    user_context=request.user_context or ""
                )

            elif metadata.content_type.startswith("video/"):
                visual_description = self._analyze_video(
                    path=path,
                    user_context=request.user_context or "",
                )

        original_text = self._build_original_text(
            transcript=transcript,
            visual_description=visual_description,
            user_context=request.user_context
        )

        analysis = self._analyze_memory(
            title=request.title,
            asset_type=request.asset_type,
            user_context=request.user_context or "",
            transcript=transcript,
            visual_description=visual_description,
            original_text=original_text,
        )

        avatar_memory_text = self._clean_string(
            analysis.get("avatar_memory_text")
        )

        summary = self._clean_string(
            analysis.get("summary")
        )

        avatar_memory_text = self._conservative_avatar_text(
            value=avatar_memory_text,
            source=original_text
        )

        summary = self._conservative_avatar_text(
            value=summary,
            source=original_text
        )

        if not avatar_memory_text:
            avatar_memory_text = summary

        if not summary:
            summary = avatar_memory_text or self._fallback_first_person_memory(original_text) or "I remember this."

        return MemoryIngestionResponse(
            profile_id=request.profile_id,
            asset_id=request.asset_id,
            title=request.title,
            asset_type=request.asset_type,
            summary=summary,
            original_text=original_text,
            avatar_memory_text=avatar_memory_text,
            memory_type=analysis.get("memory_type", request.asset_type),
            emotional_tags=analysis.get("emotional_tags", []),
            extracted_topics=analysis.get("extracted_topics", []),
            persona_signals=analysis.get("persona_signals", []),
            timeline_date_hint=analysis.get("timeline_date_hint"),
            confidence_score=float(analysis.get("confidence_score", 0.45)),
            transcript=transcript,
            visual_description=visual_description,
            readiness_signals=analysis.get("readiness_signals", {}),
        )

    def _build_original_text(
        self,
        transcript: Optional[str],
        visual_description: Optional[str],
        user_context: Optional[str]
    ) -> str:
        parts = []

        if user_context and user_context.strip():
            parts.append(user_context.strip())

        if transcript and transcript.strip():
            parts.append(transcript.strip())

        if visual_description and visual_description.strip():
            parts.append(visual_description.strip())

        return "\n\n".join(parts).strip()

    def _clean_string(self, value) -> str:
        if not isinstance(value, str):
            return ""

        return value.strip()

    def _conservative_avatar_text(
        self,
        value: str,
        source: str
    ) -> str:
        clean = self._clean_string(value)
        source_clean = self._clean_string(source)

        if not clean:
            return ""

        lower = clean.lower()

        forbidden_fragments = [
            "cherished",
            "important",
            "meaningful",
            "special",
            "joyful",
            "beautiful",
            "brought everyone together",
            "bring everyone together",
            "family together",
            "made everyone feel",
            "made me feel important",
            "made me feel loved",
            "felt loved",
            "felt celebrated",
            "filled with laughter",
            "sweet smell",
            "delicious aromas",
            "comfort",
            "warmth",
            "care",
            "love and care",
            "meant the world",
            "looked up to",
            "best person in the world",
            "being viewed as the best person",
            "being described as the best person",
            "people looked up to",
            "noticed melanie's curiosity",
            "felt uplifted",
            "feeling uplifted",
            "significance",
            "significant",
            "highlights my significance",
            "deep curiosity about me",
            "i loved it",
            "loved it",
        ]

        if any(fragment in lower for fragment in forbidden_fragments):
            return self._fallback_first_person_memory(source_clean)

        if self._third_person_leak_detected(clean):
            return self._fallback_first_person_memory(source_clean)

        return clean

    def _third_person_leak_detected(self, value: str) -> bool:
        lower = value.lower()

        leaks = [
            "my grandmother",
            "my grandfather",
            "my mother",
            "my father",
            "your grandmother",
            "your grandfather",
            "your mother",
            "your father",
            "the user",
            "she was",
            "he was",
            "she loved",
            "he loved",
            "she remembered",
            "he remembered",
        ]

        return any(leak in lower for leak in leaks)

    def _fallback_first_person_memory(
        self,
        source: str
    ) -> str:
        lower = (source or "").lower()

        if any(token in lower for token in ["cake", "cakes", "kuchen", "baked", "baking", "cooked", "cooking", "gebacken"]):
            return "I remember baking cakes."

        if any(token in lower for token in ["football", "soccer", "fußball", "fussball"]):
            return "I remember playing football."

        if any(token in lower for token in ["birthday", "geburtstag"]):
            return "I remember a birthday, but the preserved detail is incomplete."

        if any(token in lower for token in ["sister", "schwester"]):
            return "I remember my sister, but the preserved detail is incomplete."

        if any(token in lower for token in ["brother", "bruder"]):
            return "I remember my brother, but the preserved detail is incomplete."

        return "I remember this, but the preserved detail is incomplete."

    def _transcribe(self, path: Path) -> str:
        with path.open("rb") as audio_file:
            result = self.client.audio.transcriptions.create(
                model=self.transcribe_model,
                file=audio_file
            )

        return result.text.strip()

    def _analyze_image(self, path: Path, content_type: str, user_context: str) -> str:
        encoded = base64.b64encode(path.read_bytes()).decode("utf-8")

        response = self.client.responses.create(
            model=self.vision_model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You analyze personal memory photos for a private remembrance app. "
                        "Be factual, careful and non-invasive. Do not identify unknown people. "
                        "Describe only visible context and emotionally relevant but grounded details."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": f"Describe this image as memory context. User context: {user_context}"
                        },
                        {
                            "type": "input_image",
                            "image_url": f"data:{content_type};base64,{encoded}"
                        }
                    ]
                }
            ],
            max_output_tokens=450
        )

        return response.output_text.strip()

    def _analyze_video(self, path: Path, user_context: str) -> str:
        import av

        frames = []
        with av.open(str(path)) as container:
            stream = next(
                (candidate for candidate in container.streams if candidate.type == "video"),
                None,
            )
            if stream is None:
                raise RuntimeError("The selected file contains no video track.")

            decoded = []
            for index, frame in enumerate(container.decode(stream)):
                if index % 30 == 0:
                    decoded.append(frame)
                if len(decoded) >= 6:
                    break

            if not decoded:
                raise RuntimeError("The selected video could not be decoded.")

            for frame in decoded:
                image = frame.to_image()
                buffer = io.BytesIO()
                image.save(buffer, format="JPEG", quality=82)
                frames.append(base64.b64encode(buffer.getvalue()).decode("utf-8"))

        content = [{
            "type": "input_text",
            "text": (
                "Describe the visible events, people, places and actions in these "
                "sampled frames as factual memory context. Do not identify unknown "
                f"people or infer unsupported emotions. User context: {user_context}"
            ),
        }]
        content.extend(
            {
                "type": "input_image",
                "image_url": f"data:image/jpeg;base64,{encoded}",
            }
            for encoded in frames
        )

        response = self.client.responses.create(
            model=self.vision_model,
            input=[
                {
                    "role": "system",
                    "content": (
                        "You analyze private personal videos conservatively. "
                        "Describe only visible evidence."
                    ),
                },
                {"role": "user", "content": content},
            ],
            max_output_tokens=500,
        )
        return response.output_text.strip()

    def _analyze_memory(
        self,
        title: str,
        asset_type: str,
        user_context: str,
        transcript: Optional[str],
        visual_description: Optional[str],
        original_text: str,
    ) -> dict:
        prompt = f"""
You are the AI ingestion engine for RememberMeAI.

The product goal:
The user preserves memories, photos, voice notes and stories so an AI remembrance avatar can later speak from the remembered person's perspective.

Critical rule:
Both "summary" and "avatar_memory_text" MUST be written from the remembered person's first-person perspective.

Correct:
"I loved baking cakes on Sundays."
"I remember the kitchen feeling warm when I made cakes."
"I used to bring everyone together with food."

Incorrect:
"My grandmother cooked cakes."
"I remember my grandmother's cakes."
"Maria cooked cakes."
"She cooked cakes."
"The user remembers..."

Identity transformation:
- Preserve the raw source evidence separately as original_text.
- Create summary as a short first-person avatar memory.
- Create avatar_memory_text as a richer first-person avatar recall.
- Convert user-perspective statements into avatar-perspective only when grounded.
- If the user says "my grandmother cooked cakes", the avatar memory becomes "I used to cook cakes."
- If the user says "my father loved football", the avatar memory becomes "I loved football."
- If the source is ambiguous, use cautious first-person wording.

Safety and grounding:
- Do not invent biographical facts.
- Do not add unsupported names, dates, places or events.
- Do not convert vague, incomplete or low-quality text into emotional meaning.
- Do not add "cherished", "important", "loved", "meaningful", "special", "joyful", "beautiful", "family togetherness", "care", "warmth" or "comfort" unless those exact meanings are explicitly present in the source.
- If the source text is incomplete, preserve uncertainty.
- Prefer plain factual first-person recall over poetic interpretation.
- If only one factual detail exists, preserve only that detail.
- Bad: "I remember feeling cherished."
- Bad: "I remember being important to everyone."
- Bad: "I brought the family together with my cakes."
- Good: "I remember baking cakes."
- Good: "I remember this, but the preserved detail is incomplete."
- Persona signals are soft behavioral patterns only.
- If evidence is weak, keep confidence low.
- Return only valid JSON.

Title: {title}
Asset type: {asset_type}
User context: {user_context}
Transcript: {transcript or ""}
Visual description: {visual_description or ""}
Original text:
{original_text}

Return JSON:
{{
  "summary": "short first-person avatar memory, never user-perspective",
  "avatar_memory_text": "richer first-person avatar recall, grounded only in evidence",
  "memory_type": "story|voice|photo|video|mixed",
  "emotional_tags": [],
  "extracted_topics": [],
  "persona_signals": [],
  "timeline_date_hint": null,
  "confidence_score": 0.0,
  "readiness_signals": {{
    "memory_depth": 0.0,
    "voice_value": 0.0,
    "visual_value": 0.0,
    "persona_value": 0.0
  }}
}}
"""

        response = self.client.responses.create(
            model=self.reasoning_model,
            input=[
                {"role": "system", "content": "You return only strict JSON. No markdown. No commentary."},
                {"role": "user", "content": prompt}
            ],
            max_output_tokens=900
        )

        raw = response.output_text.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        return json.loads(raw)
