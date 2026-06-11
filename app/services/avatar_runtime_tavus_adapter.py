from __future__ import annotations

import asyncio
import inspect
import io
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Iterable, Optional
from uuid import UUID

import av
from livekit import api, rtc
from livekit.agents import utils as agent_utils
from livekit.agents.voice.avatar import DataStreamAudioOutput

from app.schemas.avatar_runtime import AvatarLiveKitSessionDescriptor
from app.services.avatar_runtime_livekit_token_service import (
    AvatarRuntimeLiveKitTokenService,
)


class TavusRuntimeError(RuntimeError):
    pass


class TavusRuntimeConfigurationError(TavusRuntimeError):
    pass


class TavusRuntimeConnectionError(TavusRuntimeError):
    pass


class TavusRuntimeAudioError(TavusRuntimeError):
    pass


@dataclass(frozen=True)
class TavusRemoteSession:
    session_id: str
    provider_avatar_id: str
    descriptor: AvatarLiveKitSessionDescriptor
    room_name: str
    avatar_identity: str
    dispatch_id: Optional[str]
    metadata: Dict[str, str]


@dataclass
class TavusRuntimeHandle:
    session_id: str
    room_name: str
    avatar_identity: str
    dispatch_id: Optional[str]
    room: rtc.Room
    audio_output: DataStreamAudioOutput
    audio_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock
    )
    close_lock: asyncio.Lock = field(
        default_factory=asyncio.Lock
    )
    closing: bool = False
    closed: bool = False


class AvatarRuntimeTavusAdapter:
    """
    Production Tavus adapter for the RememberMeAI runtime.

    The LiveKit Agent worker owns the Tavus AvatarSession. The FastAPI process
    joins the same room as a restricted VoiceDNA bridge and sends decoded PCM
    frames directly to the verified Tavus avatar participant.

    Runtime shutdown is strictly ordered:

    1. Stop accepting new audio.
    2. Acquire the per-session audio lock.
    3. Complete or cancel the clear-buffer RPC.
    4. Drain DataStream background tasks.
    5. Disconnect the bridge Room.
    6. Delete dispatch and Room resources.
    """

    output_sample_rate = 24_000
    output_channels = 1

    def __init__(
        self,
        *,
        token_service: Optional[
            AvatarRuntimeLiveKitTokenService
        ] = None,
    ) -> None:
        self.token_service = (
            token_service
            or AvatarRuntimeLiveKitTokenService()
        )

        self.livekit_url = self._clean(
            os.getenv("LIVEKIT_URL")
        )
        self.livekit_api_key = self._clean(
            os.getenv("LIVEKIT_API_KEY")
        )
        self.livekit_api_secret = self._clean(
            os.getenv("LIVEKIT_API_SECRET")
        )
        self.tavus_api_key = self._clean(
            os.getenv("TAVUS_API_KEY")
        )
        self.replica_id = self._clean(
            os.getenv("TAVUS_REPLICA_ID")
        )
        self.persona_id = self._clean(
            os.getenv("TAVUS_PERSONA_ID")
        )
        self.worker_name = (
            self._clean(
                os.getenv(
                    "AVATAR_RUNTIME_TAVUS_WORKER_NAME"
                )
            )
            or "rememberme-tavus-avatar"
        )

        self.connection_timeout_seconds = max(
            10,
            min(
                int(
                    os.getenv(
                        "AVATAR_RUNTIME_TAVUS_CONNECT_TIMEOUT_SECONDS",
                        "45",
                    )
                ),
                180,
            ),
        )

        self.interrupt_timeout_seconds = max(
            1,
            min(
                int(
                    os.getenv(
                        "AVATAR_RUNTIME_TAVUS_INTERRUPT_TIMEOUT_SECONDS",
                        "5",
                    )
                ),
                30,
            ),
        )

        self.cleanup_timeout_seconds = max(
            2,
            min(
                int(
                    os.getenv(
                        "AVATAR_RUNTIME_TAVUS_CLEANUP_TIMEOUT_SECONDS",
                        "8",
                    )
                ),
                60,
            ),
        )

        self.room_empty_timeout_seconds = max(
            60,
            min(
                int(
                    os.getenv(
                        "AVATAR_RUNTIME_ROOM_EMPTY_TIMEOUT_SECONDS",
                        "300",
                    )
                ),
                3600,
            ),
        )

        self.room_departure_timeout_seconds = max(
            20,
            min(
                int(
                    os.getenv(
                        "AVATAR_RUNTIME_ROOM_DEPARTURE_TIMEOUT_SECONDS",
                        "60",
                    )
                ),
                600,
            ),
        )

        self._handles: Dict[
            str,
            TavusRuntimeHandle,
        ] = {}

        self._handles_lock = asyncio.Lock()

    @property
    def is_configured(self) -> bool:
        return all(
            (
                self.livekit_url,
                self.livekit_api_key,
                self.livekit_api_secret,
                self.tavus_api_key,
                self.replica_id,
                self.persona_id,
                self.token_service.is_configured,
            )
        )

    def missing_configuration(self) -> list[str]:
        values = {
            "LIVEKIT_URL": self.livekit_url,
            "LIVEKIT_API_KEY": self.livekit_api_key,
            "LIVEKIT_API_SECRET": self.livekit_api_secret,
            "TAVUS_API_KEY": self.tavus_api_key,
            "TAVUS_REPLICA_ID": self.replica_id,
            "TAVUS_PERSONA_ID": self.persona_id,
        }

        return [
            key
            for key, value in values.items()
            if value is None
        ]

    async def start_session(
        self,
        *,
        session_id: str,
        profile_id: UUID,
        display_name: str,
    ) -> TavusRemoteSession:
        self._require_configuration()

        room_name = (
            self.token_service
            .room_name_for_session(session_id)
        )

        avatar_identity = (
            self.token_service
            .avatar_identity_for_session(session_id)
        )

        client_token = (
            self.token_service.create_client_token(
                session_id=session_id,
                profile_id=profile_id,
                display_name=display_name,
                room_name=room_name,
            )
        )

        bridge_token = (
            self.token_service.create_bridge_token(
                session_id=session_id,
                room_name=room_name,
            )
        )

        room = rtc.Room()
        dispatch_id: Optional[str] = None

        try:
            await self._create_room(
                room_name
            )

            try:
                await asyncio.wait_for(
                    room.connect(
                        self.livekit_url,
                        bridge_token.token,
                    ),
                    timeout=self.connection_timeout_seconds,
                )
            except asyncio.TimeoutError as error:
                raise TavusRuntimeConnectionError(
                    "Tavus activation timed out while connecting backend bridge to LiveKit room."
                ) from error

            dispatch_id = await self._dispatch_worker(
                session_id=session_id,
                profile_id=profile_id,
                room_name=room_name,
                avatar_identity=avatar_identity,
            )

            try:
                await asyncio.wait_for(
                    agent_utils.wait_for_participant(
                        room=room,
                        identity=avatar_identity,
                    ),
                    timeout=self.connection_timeout_seconds,
                )
            except asyncio.TimeoutError as error:
                raise TavusRuntimeConnectionError(
                    "Tavus activation timed out waiting for avatar participant. "
                    f"Expected identity: {avatar_identity}. "
                    f"Worker name: {self.worker_name}. "
                    f"Dispatch id: {dispatch_id or 'none'}."
                ) from error

            try:
                await asyncio.wait_for(
                    agent_utils.wait_for_track_publication(
                        room=room,
                        identity=avatar_identity,
                        kind=rtc.TrackKind.KIND_VIDEO,
                    ),
                    timeout=self.connection_timeout_seconds,
                )
            except asyncio.TimeoutError as error:
                raise TavusRuntimeConnectionError(
                    "Tavus activation timed out waiting for avatar video track. "
                    f"Expected identity: {avatar_identity}. "
                    f"Worker name: {self.worker_name}. "
                    f"Dispatch id: {dispatch_id or 'none'}."
                ) from error

            audio_output = DataStreamAudioOutput(
                room=room,
                destination_identity=avatar_identity,
                sample_rate=self.output_sample_rate,
                wait_remote_track=rtc.TrackKind.KIND_VIDEO,
                clear_buffer_timeout=2.0,
                wait_playback_start=False,
            )

            handle = TavusRuntimeHandle(
                session_id=session_id,
                room_name=room_name,
                avatar_identity=avatar_identity,
                dispatch_id=dispatch_id,
                room=room,
                audio_output=audio_output,
            )

            async with self._handles_lock:
                previous = self._handles.pop(
                    session_id,
                    None,
                )

                self._handles[session_id] = handle

            if previous is not None:
                await self._close_handle(
                    previous
                )

            descriptor = AvatarLiveKitSessionDescriptor(
                session_id=session_id,
                server_url=self.livekit_url,
                token=client_token.token,
                room_name=room_name,
                avatar_participant_identity=avatar_identity,
                expires_at=client_token.expires_at,
            )

            return TavusRemoteSession(
                session_id=session_id,
                provider_avatar_id=self.replica_id,
                descriptor=descriptor,
                room_name=room_name,
                avatar_identity=avatar_identity,
                dispatch_id=dispatch_id,
                metadata={
                    "runtime_version": "4",
                    "session_mode": "tavus_livekit",
                    "remote_session_verified": "true",
                    "avatar_participant_verified": "true",
                    "avatar_video_track_verified": "true",
                    "audio_ownership": "ios_voice_dna_pipeline",
                    "voice_bridge": "livekit_datastream",
                    "worker_name": self.worker_name,
                    "interrupt_mode": "deterministic_rpc_drain",
                    "cleanup_mode": "atomic_room_shutdown",
                },
            )

        except Exception as error:
            await self._disconnect_room(
                room
            )

            await self._delete_remote_resources(
                room_name=room_name,
                dispatch_id=dispatch_id,
            )

            if isinstance(
                error,
                TavusRuntimeError,
            ):
                raise

            raise TavusRuntimeConnectionError(
                "Tavus LiveKit session activation failed: "
                f"{type(error).__name__}: {error}"
            ) from error

    async def stream_audio(
        self,
        *,
        session_id: str,
        audio_data: bytes,
    ) -> int:
        handle = await self._require_handle(
            session_id
        )

        if handle.closing or handle.closed:
            raise TavusRuntimeConnectionError(
                "The Tavus session is closing and cannot accept audio."
            )

        try:
            frames = await asyncio.to_thread(
                self._decode_audio_frames_sync,
                audio_data,
            )

        except TavusRuntimeAudioError:
            raise

        except Exception as error:
            raise TavusRuntimeAudioError(
                "VoiceDNA audio decoding failed: "
                f"{type(error).__name__}: {error}"
            ) from error

        if not frames:
            raise TavusRuntimeAudioError(
                "VoiceDNA audio did not contain decodable audio frames."
            )

        async with handle.audio_lock:
            if handle.closing or handle.closed:
                raise TavusRuntimeConnectionError(
                    "The Tavus session closed before audio streaming began."
                )

            try:
                for frame in frames:
                    await handle.audio_output.capture_frame(
                        frame
                    )

                handle.audio_output.flush()

            except Exception as error:
                raise TavusRuntimeAudioError(
                    "VoiceDNA audio streaming to Tavus failed: "
                    f"{type(error).__name__}: {error}"
                ) from error

        return len(frames)

    async def interrupt_session(
        self,
        session_id: str,
    ) -> None:
        handle = await self._require_handle(
            session_id
        )

        async with handle.audio_lock:
            if handle.closing or handle.closed:
                return

            await self._await_output_interrupt(
                handle.audio_output
            )

    async def close_session(
        self,
        session_id: str,
    ) -> None:
        async with self._handles_lock:
            handle = self._handles.pop(
                session_id,
                None,
            )

        if handle is None:
            return

        await self._close_handle(
            handle
        )

    async def has_active_session(
        self,
        session_id: str,
    ) -> bool:
        async with self._handles_lock:
            handle = self._handles.get(
                session_id
            )

        return (
            handle is not None
            and not handle.closing
            and not handle.closed
            and handle.room.isconnected()
        )

    async def _require_handle(
        self,
        session_id: str,
    ) -> TavusRuntimeHandle:
        clean_session_id = session_id.strip()

        if not clean_session_id:
            raise TavusRuntimeConnectionError(
                "Runtime session identifier is missing."
            )

        async with self._handles_lock:
            handle = self._handles.get(
                clean_session_id
            )

        if handle is None:
            raise TavusRuntimeConnectionError(
                "The Tavus media bridge is not active for this session."
            )

        if handle.closing or handle.closed:
            raise TavusRuntimeConnectionError(
                "The Tavus media bridge is closing."
            )

        if not handle.room.isconnected():
            raise TavusRuntimeConnectionError(
                "The Tavus LiveKit room is no longer connected."
            )

        return handle

    async def _create_room(
        self,
        room_name: str,
    ) -> None:
        client = self._make_api_client()

        try:
            await client.room.create_room(
                api.CreateRoomRequest(
                    name=room_name,
                    empty_timeout=(
                        self.room_empty_timeout_seconds
                    ),
                    departure_timeout=(
                        self.room_departure_timeout_seconds
                    ),
                    max_participants=8,
                )
            )
        finally:
            await client.aclose()

    async def _dispatch_worker(
        self,
        *,
        session_id: str,
        profile_id: UUID,
        room_name: str,
        avatar_identity: str,
    ) -> Optional[str]:
        client = self._make_api_client()

        metadata = json.dumps(
            {
                "runtime": "rememberme-avatar",
                "runtime_version": 4,
                "session_id": session_id,
                "profile_id": str(profile_id),
                "avatar_identity": avatar_identity,
            },
            separators=(",", ":"),
            sort_keys=True,
        )

        try:
            dispatch = (
                await client.agent_dispatch.create_dispatch(
                    api.CreateAgentDispatchRequest(
                        agent_name=self.worker_name,
                        room=room_name,
                        metadata=metadata,
                    )
                )
            )

            dispatch_id = getattr(
                dispatch,
                "id",
                None,
            )

            return (
                str(dispatch_id)
                if dispatch_id
                else None
            )

        finally:
            await client.aclose()

    async def _close_handle(
        self,
        handle: TavusRuntimeHandle,
    ) -> None:
        async with handle.close_lock:
            if handle.closed:
                return

            handle.closing = True

            try:
                async with handle.audio_lock:
                    await self._prepare_output_for_disconnect(
                        handle.audio_output
                    )

                await self._disconnect_room(
                    handle.room
                )

                await self._delete_remote_resources(
                    room_name=handle.room_name,
                    dispatch_id=handle.dispatch_id,
                )

            finally:
                handle.closed = True
                handle.closing = False

    async def _await_output_interrupt(
        self,
        audio_output: DataStreamAudioOutput,
    ) -> None:
        method = getattr(
            audio_output,
            "clear_buffer_and_wait",
            None,
        )

        if callable(method):
            await method(
                timeout_seconds=(
                    float(
                        self.interrupt_timeout_seconds
                    )
                )
            )
            return

        audio_output.clear_buffer()

        await asyncio.sleep(
            min(
                2.5,
                float(
                    self.interrupt_timeout_seconds
                ),
            )
        )

    async def _prepare_output_for_disconnect(
        self,
        audio_output: DataStreamAudioOutput,
    ) -> None:
        method = getattr(
            audio_output,
            "prepare_for_disconnect",
            None,
        )

        if callable(method):
            await method(
                timeout_seconds=(
                    float(
                        self.cleanup_timeout_seconds
                    )
                )
            )
            return

        await self._await_output_interrupt(
            audio_output
        )

    async def _delete_remote_resources(
        self,
        *,
        room_name: str,
        dispatch_id: Optional[str],
    ) -> None:
        if not self.token_service.is_configured:
            return

        client = self._make_api_client()

        try:
            if dispatch_id:
                try:
                    await client.agent_dispatch.delete_dispatch(
                        dispatch_id=dispatch_id,
                        room_name=room_name,
                    )
                except Exception:
                    pass

            try:
                await client.room.delete_room(
                    api.DeleteRoomRequest(
                        room=room_name
                    )
                )
            except Exception:
                pass

        finally:
            await client.aclose()

    async def _disconnect_room(
        self,
        room: rtc.Room,
    ) -> None:
        if not room.isconnected():
            return

        try:
            result = room.disconnect()

            if inspect.isawaitable(result):
                await asyncio.wait_for(
                    result,
                    timeout=float(
                        self.cleanup_timeout_seconds
                    ),
                )

        except asyncio.TimeoutError:
            pass

        except Exception:
            pass

    def _make_api_client(
        self,
    ) -> api.LiveKitAPI:
        return api.LiveKitAPI(
            url=self.livekit_url,
            api_key=self.livekit_api_key,
            api_secret=self.livekit_api_secret,
        )

    def _require_configuration(
        self,
    ) -> None:
        missing = self.missing_configuration()

        if missing:
            raise TavusRuntimeConfigurationError(
                "Missing Tavus/LiveKit configuration: "
                + ", ".join(missing)
            )

    def _decode_audio_frames_sync(
        self,
        audio_data: bytes,
    ) -> list[rtc.AudioFrame]:
        if not audio_data:
            raise TavusRuntimeAudioError(
                "VoiceDNA audio is empty."
            )

        result: list[
            rtc.AudioFrame
        ] = []

        try:
            with av.open(
                io.BytesIO(audio_data),
                mode="r",
            ) as container:
                audio_stream = next(
                    (
                        stream
                        for stream in container.streams
                        if stream.type == "audio"
                    ),
                    None,
                )

                if audio_stream is None:
                    raise TavusRuntimeAudioError(
                        "Uploaded media does not contain an audio stream."
                    )

                resampler = (
                    av.audio.resampler.AudioResampler(
                        format="s16",
                        layout="mono",
                        rate=self.output_sample_rate,
                    )
                )

                for packet in container.demux(
                    audio_stream
                ):
                    for decoded_frame in packet.decode():
                        converted = resampler.resample(
                            decoded_frame
                        )

                        for frame in self._normalize_frames(
                            converted
                        ):
                            livekit_frame = (
                                self._make_livekit_frame(
                                    frame
                                )
                            )

                            if livekit_frame is not None:
                                result.append(
                                    livekit_frame
                                )

                try:
                    flushed = resampler.resample(
                        None
                    )
                except Exception:
                    flushed = []

                for frame in self._normalize_frames(
                    flushed
                ):
                    livekit_frame = (
                        self._make_livekit_frame(
                            frame
                        )
                    )

                    if livekit_frame is not None:
                        result.append(
                            livekit_frame
                        )

        except TavusRuntimeAudioError:
            raise

        except Exception as error:
            raise TavusRuntimeAudioError(
                "Uploaded VoiceDNA media could not be decoded: "
                f"{type(error).__name__}: {error}"
            ) from error

        return result

    def _make_livekit_frame(
        self,
        frame: av.AudioFrame,
    ) -> Optional[rtc.AudioFrame]:
        samples = int(
            frame.samples
        )

        if samples <= 0 or not frame.planes:
            return None

        pcm_data = bytes(
            frame.planes[0]
        )

        expected_bytes = (
            samples
            * self.output_channels
            * 2
        )

        if len(pcm_data) < expected_bytes:
            raise TavusRuntimeAudioError(
                "Decoded PCM frame is smaller than expected."
            )

        return rtc.AudioFrame(
            data=pcm_data[:expected_bytes],
            sample_rate=self.output_sample_rate,
            num_channels=self.output_channels,
            samples_per_channel=samples,
        )

    @staticmethod
    def _normalize_frames(
        frames: object,
    ) -> Iterable[av.AudioFrame]:
        if frames is None:
            return ()

        if isinstance(
            frames,
            av.AudioFrame,
        ):
            return (
                frames,
            )

        if isinstance(
            frames,
            Iterable,
        ):
            return tuple(
                frame
                for frame in frames
                if isinstance(
                    frame,
                    av.AudioFrame,
                )
            )

        return ()

    @staticmethod
    def _clean(
        value: Optional[str],
    ) -> Optional[str]:
        if value is None:
            return None

        cleaned = value.strip()

        return cleaned or None
