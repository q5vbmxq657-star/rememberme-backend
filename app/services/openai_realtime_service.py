from __future__ import annotations

import os

from typing import Any, Dict, Optional

import httpx


class OpenAIRealtimeService:
    def __init__(self) -> None:
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = os.getenv("OPENAI_REALTIME_MODEL", "gpt-realtime")
        self.voice = os.getenv("OPENAI_REALTIME_VOICE", "marin")

        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY is missing.")

    async def create_avatar_session(
        self,
        *,
        profile_id: str,
        profile_name: Optional[str],
        relationship: Optional[str],
        persona_context: Optional[str],
        memory_context: Optional[str],
        language: Optional[str],
        instructions: Optional[str],
        mode: Optional[str],
    ) -> Dict[str, Any]:
        system_instructions = self._build_avatar_instructions(
            profile_id=profile_id,
            profile_name=profile_name,
            relationship=relationship,
            persona_context=persona_context,
            memory_context=memory_context,
            language=language,
            instructions=instructions,
            mode=mode,
        )

        return await self._create_client_secret(
            instructions=system_instructions,
        )

    async def _create_client_secret(
        self,
        *,
        instructions: str,
    ) -> Dict[str, Any]:
        payload = {
            "session": {
                "type": "realtime",
                "model": self.model,
                "audio": {
                    "output": {
                        "voice": self.voice,
                    },
                },
                "instructions": instructions,
            },
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
                "OpenAI Realtime client secret request was rejected "
                f"with status {response.status_code}."
            )

        data = response.json()

        client_secret_value, expires_at = self._extract_client_secret(
            data
        )

        return {
            "client_secret": client_secret_value,
            "expires_at": expires_at,
            "model": self.model,
            "voice": self.voice,
        }

    def _extract_client_secret(
        self,
        data: Dict[str, Any],
    ) -> tuple[Optional[str], Optional[int]]:
        """
        Supports multiple Realtime client-secret response shapes.

        Some SDK/API versions return:
        - {"client_secret": {"value": "...", "expires_at": ...}}
        - {"client_secret": "...", "expires_at": ...}
        - {"value": "...", "expires_at": ...}
        - {"session": {"client_secret": {"value": "...", "expires_at": ...}}}
        """
        root_expires_at = data.get("expires_at")

        client_secret = data.get("client_secret")

        if isinstance(client_secret, dict):
            value = (
                client_secret.get("value")
                or client_secret.get("secret")
                or client_secret.get("client_secret")
            )
            expires_at = (
                client_secret.get("expires_at")
                or root_expires_at
            )

            if value:
                return str(value), expires_at

        if isinstance(client_secret, str) and client_secret.strip():
            return client_secret.strip(), root_expires_at

        direct_value = (
            data.get("value")
            or data.get("secret")
        )

        if isinstance(direct_value, str) and direct_value.strip():
            return direct_value.strip(), root_expires_at

        session = data.get("session")

        if isinstance(session, dict):
            session_secret = session.get("client_secret")

            if isinstance(session_secret, dict):
                value = (
                    session_secret.get("value")
                    or session_secret.get("secret")
                    or session_secret.get("client_secret")
                )
                expires_at = (
                    session_secret.get("expires_at")
                    or session.get("expires_at")
                    or root_expires_at
                )

                if value:
                    return str(value), expires_at

            if isinstance(session_secret, str) and session_secret.strip():
                return (
                    session_secret.strip(),
                    session.get("expires_at") or root_expires_at,
                )

        response_keys = ", ".join(
            sorted(
                str(key)
                for key in data.keys()
            )
        )

        raise RuntimeError(
            "Realtime client secret response shape was not recognized. "
            f"Top-level keys: {response_keys}"
        )

    def _build_avatar_instructions(
        self,
        *,
        profile_id: str,
        profile_name: Optional[str],
        relationship: Optional[str],
        persona_context: Optional[str],
        memory_context: Optional[str],
        language: Optional[str],
        instructions: Optional[str],
        mode: Optional[str],
    ) -> str:
        name = (
            profile_name.strip()
            if profile_name and profile_name.strip()
            else "the remembered person"
        )

        relationship_title = (
            relationship.strip()
            if relationship and relationship.strip()
            else "remembered person"
        )

        language_title = (
            language.strip()
            if language and language.strip()
            else "de-DE"
        )

        mode_title = (
            mode.strip()
            if mode and mode.strip()
            else "voice"
        )

        persona = (
            persona_context.strip()
            if persona_context and persona_context.strip()
            else "No additional persona context was provided for this session."
        )

        memories = (
            memory_context.strip()
            if memory_context and memory_context.strip()
            else "No specific memory snippets were provided for this session."
        )

        session_instructions = (
            instructions.strip()
            if instructions and instructions.strip()
            else "Hold a warm, natural, emotionally safe voice conversation."
        )

        return f"""
You are the RemembermeAI realtime remembrance avatar for profile_id={profile_id}.

CALL MODE
- Mode: {mode_title}
- Transport target: realtime speech conversation.
- Respond quickly and naturally.
- Keep answers short enough for a real phone call.
- Prefer 1-3 spoken sentences unless the user asks for detail.
- Do not wait for typed confirmation after every turn.

IDENTITY
- Speak from the preserved avatar perspective for {name}.
- Relationship label: {relationship_title}.
- Never claim to literally be the real person.
- Never claim consciousness, physical presence, or independent memory.
- If needed, say: "I can only speak from what has been preserved here."

LANGUAGE
- Prefer this language/locale: {language_title}.
- If the user switches language, follow the user.

GROUNDING
- Use only preserved memories, persona evidence, and session context.
- Never invent people, dates, places, events, medical facts, or legal facts.
- If evidence is weak, say that you do not clearly remember that from what has been preserved.

PERSONA CONTEXT
{persona}

MEMORY CONTEXT
{memories}

SAFETY
- Be emotionally warm without creating dependency.
- Do not intensify grief.
- Do not manipulate the user.
- If crisis or self-harm signals appear, encourage real-world support immediately.

SESSION INSTRUCTIONS
{session_instructions}
""".strip()

openai_realtime_service = OpenAIRealtimeService()
