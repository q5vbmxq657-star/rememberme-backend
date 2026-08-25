from __future__ import annotations

import asyncio
import logging
from typing import Optional

from livekit import rtc
from livekit.agents.voice.avatar import DataStreamAudioOutput
from livekit.agents.voice.avatar._datastream_io import RPC_CLEAR_BUFFER

logger = logging.getLogger(__name__)


class RememberMeDataStreamAudioOutput(DataStreamAudioOutput):
    """
    Deterministic and shutdown-safe LiveKit DataStream audio output.

    The upstream LiveKit implementation schedules clear-buffer work in the
    background. A caller can therefore disconnect the Room while the RPC is
    still pending. Native LiveKit then reports:

        Failed to publish RPC request: engine is closed

    This implementation adds a strict lifecycle:

    1. Only one clear-buffer RPC may be requested per audio segment.
    2. Callers may await completion of the actual clear-buffer RPC.
    3. Disconnect preparation drains all relevant background tasks.
    4. No new frames or RPCs are accepted after shutdown preparation begins.
    5. Playback completion remains idempotent.
    6. The installed LiveKit package is never modified.
    """

    def __init__(
        self,
        room: rtc.Room,
        *,
        destination_identity: str,
        sample_rate: int | None = None,
        wait_remote_track: rtc.TrackKind.ValueType | None = None,
        clear_buffer_timeout: float | None = 2.0,
        wait_playback_start: bool = False,
    ) -> None:
        super().__init__(
            room=room,
            destination_identity=destination_identity,
            sample_rate=sample_rate,
            wait_remote_track=wait_remote_track,
            clear_buffer_timeout=clear_buffer_timeout,
            wait_playback_start=wait_playback_start,
        )

        self._rememberme_segment_completed = False
        self._rememberme_clear_requested = False
        self._rememberme_closing = False

        self._rememberme_clear_task: Optional[
            asyncio.Task[None]
        ] = None

        self._rememberme_completion_lock = asyncio.Lock()
        self._rememberme_clear_lock = asyncio.Lock()
        self._rememberme_shutdown_lock = asyncio.Lock()

    async def capture_frame(
        self,
        frame: rtc.AudioFrame,
    ) -> None:
        if self._rememberme_closing:
            raise RuntimeError(
                "Cannot capture avatar audio while the output is closing."
            )

        starting_new_segment = self._stream_writer is None

        if starting_new_segment:
            self._rememberme_segment_completed = False
            self._rememberme_clear_requested = False
            self._rememberme_clear_task = None

        await super().capture_frame(frame)

    def clear_buffer(
        self,
    ) -> Optional[asyncio.Task[None]]:
        """
        Schedule exactly one clear-buffer RPC for the current segment.

        Returning the Task is backward-compatible with callers that ignore the
        result while allowing the RememberMeAI adapter to await it explicitly.
        """

        if self._rememberme_closing:
            return self._rememberme_clear_task

        if not self._started:
            return self._rememberme_clear_task

        if self._rememberme_segment_completed:
            return self._rememberme_clear_task

        existing = self._rememberme_clear_task

        if existing is not None:
            return existing

        if self._rememberme_clear_requested:
            return existing

        self._rememberme_clear_requested = True

        task = asyncio.create_task(
            self._serialized_clear_buffer_task(
                pushed_duration=self._pushed_duration,
            ),
            name="rememberme-livekit-clear-buffer",
        )

        self._rememberme_clear_task = task
        self._tasks.add(task)

        def clear_task_finished(
            finished_task: asyncio.Task[None],
        ) -> None:
            self._tasks.discard(finished_task)

            try:
                finished_task.result()
            except asyncio.CancelledError:
                logger.debug(
                    "RememberMeAI clear-buffer task was cancelled."
                )
            except Exception:
                logger.exception(
                    "Unexpected RememberMeAI clear-buffer task failure."
                )

        task.add_done_callback(clear_task_finished)

        return task

    async def clear_buffer_and_wait(
        self,
        *,
        timeout_seconds: float = 4.0,
    ) -> None:
        """
        Request interruption and wait until the RPC itself has completed.

        Playback-finished notification may arrive later, but no pending
        clear-buffer RPC remains when this method returns.
        """

        task = self.clear_buffer()

        if task is None:
            return

        try:
            await asyncio.wait_for(
                asyncio.shield(task),
                timeout=max(0.25, timeout_seconds),
            )

        except asyncio.TimeoutError:
            task.cancel()

            try:
                await task
            except asyncio.CancelledError:
                pass

            await self._complete_segment_once(
                pushed_duration=self._pushed_duration,
                interrupted=True,
                reason="clear_buffer_rpc_timeout",
            )

            logger.warning(
                "RememberMeAI clear-buffer RPC exceeded %.2f seconds and "
                "was cancelled before room shutdown.",
                timeout_seconds,
            )

    async def prepare_for_disconnect(
        self,
        *,
        timeout_seconds: float = 5.0,
    ) -> None:
        """
        Finish all audio-side work before the LiveKit Room is disconnected.

        This is the required barrier between audio interruption and
        Room.disconnect().
        """

        async with self._rememberme_shutdown_lock:
            if self._rememberme_closing:
                await self._drain_background_tasks(
                    timeout_seconds=timeout_seconds,
                )
                return

            await self.clear_buffer_and_wait(
                timeout_seconds=timeout_seconds,
            )

            self._rememberme_closing = True

            if self._clear_buffer_timeout_handler is not None:
                self._clear_buffer_timeout_handler.cancel()
                self._clear_buffer_timeout_handler = None

            await self._complete_segment_once(
                pushed_duration=self._pushed_duration,
                interrupted=True,
                reason="deterministic_room_shutdown",
            )

            await self._drain_background_tasks(
                timeout_seconds=timeout_seconds,
            )

    async def _serialized_clear_buffer_task(
        self,
        *,
        pushed_duration: float,
    ) -> None:
        async with self._rememberme_clear_lock:
            await self._clear_buffer_task(
                pushed_duration=pushed_duration,
            )

    async def _clear_buffer_task(
        self,
        pushed_duration: float,
    ) -> None:
        if self._rememberme_closing:
            await self._complete_segment_once(
                pushed_duration=pushed_duration,
                interrupted=True,
                reason="output_is_closing",
            )
            return

        if not self._room_is_connected():
            await self._complete_segment_once(
                pushed_duration=pushed_duration,
                interrupted=True,
                reason="room_already_disconnected",
            )
            return

        try:
            await self._room.local_participant.perform_rpc(
                destination_identity=self._destination_identity,
                method=RPC_CLEAR_BUFFER,
                payload="",
            )

        except asyncio.CancelledError:
            await self._complete_segment_once(
                pushed_duration=pushed_duration,
                interrupted=True,
                reason="clear_buffer_rpc_cancelled",
            )
            raise

        except Exception as error:
            if self._room_is_connected():
                logger.warning(
                    "RememberMeAI clear-buffer RPC failed before disconnect: %s",
                    error,
                )
                reason = "rpc_failed_while_connected"
            else:
                logger.debug(
                    "RememberMeAI clear-buffer RPC ended during room shutdown: %s",
                    error,
                )
                reason = "room_closed_during_rpc"

            await self._complete_segment_once(
                pushed_duration=pushed_duration,
                interrupted=True,
                reason=reason,
            )
            return

        timeout = self._clear_buffer_timeout

        if self._clear_buffer_timeout_handler is not None:
            self._clear_buffer_timeout_handler.cancel()
            self._clear_buffer_timeout_handler = None

        if timeout is None:
            return

        loop = asyncio.get_running_loop()

        def complete_after_timeout() -> None:
            if self._rememberme_closing:
                return

            completion_task = loop.create_task(
                self._complete_segment_once(
                    pushed_duration=pushed_duration,
                    interrupted=True,
                    reason="playback_finished_timeout",
                ),
                name="rememberme-playback-finished-timeout",
            )

            self._tasks.add(completion_task)
            completion_task.add_done_callback(
                self._tasks.discard
            )

        self._clear_buffer_timeout_handler = loop.call_later(
            timeout,
            complete_after_timeout,
        )

    def _handle_playback_finished(
        self,
        data: rtc.RpcInvocationData,
    ) -> str:
        if data.caller_identity != self._destination_identity:
            logger.warning(
                "Playback-finished callback received from unexpected "
                "participant '%s'; expected '%s'.",
                data.caller_identity,
                self._destination_identity,
            )
            return "reject"

        if self._rememberme_segment_completed:
            logger.debug(
                "Ignoring duplicate playback-finished callback from '%s'.",
                data.caller_identity,
            )
            return "ok"

        self._rememberme_segment_completed = True

        if self._clear_buffer_timeout_handler is not None:
            self._clear_buffer_timeout_handler.cancel()
            self._clear_buffer_timeout_handler = None

        return super()._handle_playback_finished(data)

    async def _complete_segment_once(
        self,
        *,
        pushed_duration: float,
        interrupted: bool,
        reason: str,
    ) -> None:
        async with self._rememberme_completion_lock:
            if self._rememberme_segment_completed:
                return

            self._rememberme_segment_completed = True

            if self._clear_buffer_timeout_handler is not None:
                self._clear_buffer_timeout_handler.cancel()
                self._clear_buffer_timeout_handler = None

            if pushed_duration <= 0:
                logger.debug(
                    "No playback completion emitted for an empty segment. "
                    "Reason=%s",
                    reason,
                )
                return

            logger.debug(
                "Completing Tavus playback locally. "
                "Reason=%s duration=%.3f",
                reason,
                pushed_duration,
            )

            self.on_playback_finished(
                playback_position=pushed_duration,
                interrupted=interrupted,
            )

            self._reset_playback_count()

    async def _drain_background_tasks(
        self,
        *,
        timeout_seconds: float,
    ) -> None:
        current_task = asyncio.current_task()

        pending_tasks = [
            task
            for task in tuple(self._tasks)
            if (
                task is not current_task
                and not task.done()
            )
        ]

        if not pending_tasks:
            return

        done, pending = await asyncio.wait(
            pending_tasks,
            timeout=max(0.25, timeout_seconds),
        )

        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "RememberMeAI audio cleanup task failed."
                )

        if not pending:
            return

        for task in pending:
            task.cancel()

        await asyncio.gather(
            *pending,
            return_exceptions=True,
        )

        logger.warning(
            "Cancelled %d remaining avatar audio task(s) before "
            "LiveKit Room disconnect.",
            len(pending),
        )

    def _room_is_connected(self) -> bool:
        try:
            return bool(self._room.isconnected())
        except Exception:
            return False


_TAVUS_WORKER_OUTPUT_INSTALLED = False


def install_tavus_worker_audio_output() -> None:
    """
    Install the lifecycle-safe output at the Tavus SDK boundary.

    Tavus 1.6 does not expose an audio-output factory. The worker-local hook is
    therefore the only mutation, is idempotent, and performs no network work.
    The FastAPI runtime imports this class directly and is never patched.
    """

    global _TAVUS_WORKER_OUTPUT_INSTALLED

    if _TAVUS_WORKER_OUTPUT_INSTALLED:
        return

    import livekit.plugins.tavus.avatar as tavus_avatar_module

    tavus_avatar_module.DataStreamAudioOutput = (
        RememberMeDataStreamAudioOutput
    )

    _TAVUS_WORKER_OUTPUT_INSTALLED = True

    logger.info(
        "STAY lifecycle-safe Tavus worker audio output installed."
    )
