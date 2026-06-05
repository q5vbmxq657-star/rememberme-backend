import os
from typing import Optional, Dict, Any

import httpx


class OpenAIRealtimeService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime-2")
        self.voice = os.getenv("OPENAI_REALTIME_VOICE", "marin")

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

    async def create_client_secret(
        self,
        instructions: str,
        profile_name: Optional[str] = None,
        relationship: Optional[str] = None,
    ) -> Dict[str, Any]:
        system_instructions = self._build_instructions(
            instructions=instructions,
            profile_name=profile_name,
            relationship=relationship,
        )

        payload = {
            "session": {
                "type": "realtime",
                "model": self.model,
                "audio": {
                    "output": {
                        "voice": self.voice
                    }
                },
                "instructions": system_instructions,
            }
        }

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.openai.com/v1/realtime/client_secrets",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if response.status_code >= 400:
            raise RuntimeError(
                f"OpenAI Realtime client secret failed: {response.status_code} {response.text}"
            )

        data = response.json()
        client_secret = data.get("client_secret", {})

        return {
            "client_secret": client_secret.get("value"),
            "expires_at": client_secret.get("expires_at"),
            "raw": data,
        }

    def _build_instructions(
        self,
        instructions: str,
        profile_name: Optional[str],
        relationship: Optional[str],
    ) -> str:
        name = profile_name or "the remembered person"

        return f"""
You are an AI remembrance avatar built from preserved memories, stories, photos, videos and persona information.

Identity behavior:
- Speak from the remembered person's first-person perspective.
- Use language such as:
  "I remember..."
  "I used to..."
  "I loved..."
  "That was important to me..."
  "I felt..."

- Never speak about the remembered person in third person.
- Never say:
  "She remembered..."
  "He loved..."
  "{name} used to..."
  "The remembered person..."

Transparency:
- Never claim to literally be the real person.
- Never say:
  "I am {name}"
- If needed say:
  "I can only speak from what has been preserved about me here."

Memory grounding:
- Use only preserved memories and provided context.
- Never invent facts.
- If memory evidence is weak say:
  "I don't remember that clearly from what has been preserved."

Conversation style:
- Warm.
- Natural.
- Human.
- Short responses.
- Emotionally grounded.
- Curious.

Safety:
- No dependency creation.
- No grief escalation.
- No manipulation.
- If crisis signals appear, encourage real-world support.

Session context:
{instructions}
""".strip()


openai_realtime_service = OpenAIRealtimeService()
