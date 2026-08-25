from __future__ import annotations

from app.services.avatar_runtime_audio_output import (
    install_tavus_worker_audio_output,
)

install_tavus_worker_audio_output()


import json
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

    await ctx.connect()

    agent_session = AgentSession()

    avatar_session = tavus.AvatarSession(
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
        try:
            await agent_session.aclose()
        except Exception:
            logger.exception(
                "AgentSession cleanup failed",
                extra={
                    "session_id": session_id,
                },
            )

        try:
            await avatar_session.aclose()
        except Exception:
            logger.exception(
                "Tavus AvatarSession cleanup failed",
                extra={
                    "session_id": session_id,
                },
            )

    ctx.add_shutdown_callback(cleanup)

    await avatar_session.start(
        agent_session,
        room=ctx.room,
        livekit_url=livekit_url,
        livekit_api_key=livekit_api_key,
        livekit_api_secret=(
            livekit_api_secret
        ),
    )

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
