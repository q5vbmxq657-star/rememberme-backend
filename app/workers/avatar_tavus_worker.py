from __future__ import annotations

from app.services.avatar_runtime_audio_output import (
    install_tavus_worker_audio_output,
)

install_tavus_worker_audio_output()


import json
import asyncio
import logging
import os
from typing import Any, Dict

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    RoomOutputOptions,
    WorkerOptions,
    cli,
)
from livekit.plugins import tavus
from app.services.avatar_provider_service import AvatarProviderService
from app.services.runtime_cleanup_repository import RuntimeCleanupRepository
from app.services.tavus_runtime_correlation import CorrelatedAvatarSession


load_dotenv()

logger = logging.getLogger(
    "rememberme.avatar.tavus_worker"
)

WORKER_NAME = (
    os.getenv(
        "AVATAR_RUNTIME_TAVUS_WORKER_NAME",
        "rememberme-tavus-avatar",
    ).strip()
    or "rememberme-tavus-avatar"
)


def _required_environment_value(
    key: str,
) -> str:
    value = os.getenv(key)

    if value is None or not value.strip():
        raise RuntimeError(
            f"Missing required worker configuration: {key}"
        )

    return value.strip()


def _job_metadata(
    ctx: JobContext,
) -> Dict[str, Any]:
    raw_metadata = getattr(
        ctx.job,
        "metadata",
        "",
    )

    if not raw_metadata:
        return {}

    try:
        parsed = json.loads(raw_metadata)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            "Avatar worker metadata is invalid JSON."
        ) from error

    if not isinstance(parsed, dict):
        raise RuntimeError(
            "Avatar worker metadata must be a JSON object."
        )

    return parsed


async def entrypoint(
    ctx: JobContext,
) -> None:
    metadata = _job_metadata(ctx)

    session_id = str(
        metadata.get("session_id") or ""
    ).strip()
    avatar_identity = str(
        metadata.get("avatar_identity") or ""
    ).strip()
    profile_id = str(
        metadata.get("profile_id") or ""
    ).strip()
    face_id = str(
        metadata.get("face_id") or ""
    ).strip()
    pal_id = str(
        metadata.get("pal_id") or ""
    ).strip()

    if not session_id:
        raise RuntimeError(
            "Avatar worker metadata is missing session_id."
        )

    if not avatar_identity:
        raise RuntimeError(
            "Avatar worker metadata is missing avatar_identity."
        )

    if not profile_id:
        raise RuntimeError(
            "Avatar worker metadata is missing profile_id."
        )

    if not face_id:
        raise RuntimeError(
            "Avatar worker metadata is missing face_id."
        )

    tavus_api_key = _required_environment_value(
        "TAVUS_API_KEY"
    )
    livekit_url = _required_environment_value(
        "LIVEKIT_URL"
    )
    livekit_api_key = _required_environment_value(
        "LIVEKIT_API_KEY"
    )
    livekit_api_secret = _required_environment_value(
        "LIVEKIT_API_SECRET"
    )

    registry = RuntimeCleanupRepository()
    await asyncio.to_thread(registry.authorize, session_id)
    await asyncio.to_thread(registry.begin_worker, session_id, profile_id, ctx.room.name)
    async def request_cleanup():
        await asyncio.to_thread(registry.request, session_id)
    ctx.add_shutdown_callback(request_cleanup)
    try:
        await ctx.connect()
    except BaseException:
        await asyncio.to_thread(registry.request, session_id)
        raise

    agent_session = AgentSession()

    avatar_session = CorrelatedAvatarSession(
        repository=registry,
        session_id=session_id,
        face_id=face_id,
        pal_id=pal_id or None,
        api_key=tavus_api_key,
        avatar_participant_identity=(
            avatar_identity
        ),
        avatar_participant_name=(
            "RememberMeAI Avatar"
        ),
    )

    async def cleanup() -> None:
        await asyncio.to_thread(registry.request, session_id)
        failures: list[Exception] = []
        try:
            await agent_session.aclose()
        except Exception as error:
            failures.append(error)

        try:
            if not avatar_session.conversation_id:
                row = await asyncio.to_thread(registry.get, session_id)
                if row['provider_create_started']:
                    raise RuntimeError("The remote conversation identity is unavailable for cleanup.")
            else:
                await AvatarProviderService().end_tavus_conversation(
                    conversation_id=avatar_session.conversation_id
                )
                await asyncio.to_thread(registry.ended, session_id, avatar_session.conversation_id)
        except Exception as error:
            failures.append(error)

        try:
            await avatar_session.aclose()
        except Exception as error:
            failures.append(error)
        if failures:
            logger.error("Avatar cleanup requires verification; not all cleanup steps succeeded.")
            raise RuntimeError("Avatar cleanup could not be verified.") from None

    ctx.add_shutdown_callback(cleanup)

    try:
        await asyncio.to_thread(registry.authorize, session_id)
        await avatar_session.start(
            agent_session, room=ctx.room, livekit_url=livekit_url,
            livekit_api_key=livekit_api_key, livekit_api_secret=livekit_api_secret,
        )
        await asyncio.to_thread(registry.conversation, session_id, avatar_session.conversation_id)
        await asyncio.to_thread(registry.authorize, session_id)
    except BaseException:
        if avatar_session.conversation_id:
            await asyncio.to_thread(registry.conversation, session_id, avatar_session.conversation_id)
        await asyncio.to_thread(registry.request, session_id)
        raise

    agent = Agent(
        instructions=(
            "You are the media host for a RememberMeAI "
            "Tavus avatar. Voice output is supplied by the "
            "external VoiceDNA bridge. Do not independently "
            "generate speech."
        )
    )

    await agent_session.start(
        agent=agent,
        room=ctx.room,
        record=False,
        room_input_options=RoomInputOptions(
            text_enabled=False,
            audio_enabled=False,
            video_enabled=False,
            close_on_disconnect=True,
        ),
        room_output_options=RoomOutputOptions(
            transcription_enabled=False,
            audio_enabled=False,
        ),
    )

    logger.info(
        "Tavus avatar worker ready",
        extra={
            "session_id": session_id,
            "profile_id": profile_id,
            "room_name": ctx.room.name,
            "avatar_identity": avatar_identity,
            "face_id": face_id,
            "pal_id": pal_id or None,
        },
    )


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=WORKER_NAME,
        )
    )
