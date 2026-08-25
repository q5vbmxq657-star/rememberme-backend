# P31.12_CANONICAL_UNIFIED_AVATAR_STATE
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Iterable, Optional
from uuid import UUID

from app.schemas.avatar_state import (
    AvatarStateDimension,
    AvatarUnifiedStateResponse,
)


_READY = {"ready", "completed", "complete", "succeeded", "success", "available"}
_IN_PROGRESS = {"pending", "queued", "processing", "running", "submitted", "training", "in_progress"}
_BLOCKED = {"failed", "error", "blocked", "rejected", "cancelled", "canceled"}


class AvatarStateService:
    """Builds one evidence-bound state contract for the avatar experience.

    This service intentionally does not mark a dimension ready unless the
    existing backend state provides evidence for that dimension.
    """

    async def build_state(
        self,
        profile_id: str,
        *,
        repository: Optional[Any] = None,
    ) -> AvatarUnifiedStateResponse:
        profile = await self._load_profile(repository, profile_id)
        training_jobs = await self._load_training_jobs(repository, profile_id)

        source_integrity = {
            "profile_loaded": profile is not None,
            "training_job_count": len(training_jobs),
            "repository_attached": repository is not None,
            "readiness_policy": "evidence_bound_no_fake_ready",
        }

        face_video = self._face_video_state(profile, training_jobs)
        voice = self._voice_state(profile, training_jobs)
        memory = self._memory_state(profile)
        behavior = self._behavior_state(profile)
        consent = self._consent_state(profile)
        runtime = self._runtime_state(
            face_video=face_video,
            voice=voice,
            consent=consent,
        )
        identity_verification = (
            self._identity_verification_state(profile)
        )

        can_start_runtime = (
            face_video.status == "ready"
            and voice.status == "ready"
            and consent.status == "ready"
        )

        readiness_score = self._weighted_score(
            face_video=face_video,
            voice=voice,
            memory=memory,
            behavior=behavior,
            consent=consent,
            runtime=runtime,
        )

        overall_status = self._overall_status(
            can_start_runtime=can_start_runtime,
            face_video=face_video,
            voice=voice,
            consent=consent,
        )

        return AvatarUnifiedStateResponse(
            profile_id=profile_id,
            overall_status=overall_status,
            readiness_score=readiness_score,
            can_start_runtime=can_start_runtime,
            next_best_action=self._next_best_action(
                face_video=face_video,
                voice=voice,
                memory=memory,
                behavior=behavior,
                consent=consent,
                can_start_runtime=can_start_runtime,
            ),
            face_video=face_video,
            voice=voice,
            memory=memory,
            behavior=behavior,
            consent=consent,
            runtime=runtime,
            identity_verification=identity_verification,
            source_integrity=source_integrity,
        )

    async def _load_profile(self, repository: Optional[Any], profile_id: str) -> Optional[Any]:
        if repository is None:
            return None

        try:
            lookup_id: Any = UUID(profile_id)
        except (TypeError, ValueError):
            lookup_id = profile_id

        for method_name in (
            "get",
            "get_profile",
            "get_profile_by_id",
            "get_digital_human_profile",
            "fetch_profile",
            "read_profile",
        ):
            value = await self._call(
                repository,
                method_name,
                lookup_id,
            )
            if value is not None:
                return value

        return None

    async def _load_training_jobs(self, repository: Optional[Any], profile_id: str) -> list[Any]:
        if repository is None:
            return []

        try:
            lookup_id: Any = UUID(profile_id)
        except (TypeError, ValueError):
            lookup_id = profile_id

        for method_name in (
            "list_training_jobs",
            "get_training_jobs",
            "get_training_jobs_for_profile",
            "list_profile_training_jobs",
            "get_profile_training_jobs",
        ):
            value = await self._call(
                repository,
                method_name,
                lookup_id,
            )
            if value is not None:
                if isinstance(value, list):
                    return value
                if isinstance(value, tuple):
                    return list(value)
                if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
                    return list(value)
                return [value]

        return []

    async def _call(self, target: Any, method_name: str, *args: Any) -> Optional[Any]:
        method = getattr(target, method_name, None)
        if method is None:
            return None

        try:
            if inspect.iscoroutinefunction(method):
                return await method(*args)

            result = await asyncio.to_thread(
                method,
                *args,
            )

            if inspect.isawaitable(result):
                return await result

            return result
        except TypeError:
            return None

    def _face_video_state(self, profile: Optional[Any], training_jobs: list[Any]) -> AvatarStateDimension:
        profile_replica = self._first_attr(profile, "replica_id", "tavus_replica_id", "avatar_replica_id")
        profile_status = self._normalize_status(self._first_attr(profile, "avatar_training_status", "training_status", "status"))

        tavus_jobs = [
            job for job in training_jobs
            if "tavus" in str(self._first_attr(job, "provider", "provider_name", "job_provider", default="")).lower()
            or self._first_attr(job, "replica_id", "external_avatar_id", "avatar_id") is not None
        ]

        job_statuses = [
            self._normalize_status(self._first_attr(job, "status", "training_status", "provider_status"))
            for job in tavus_jobs
        ]

        job_replica = next(
            (
                self._first_attr(job, "replica_id", "external_avatar_id", "avatar_id")
                for job in tavus_jobs
                if self._first_attr(job, "replica_id", "external_avatar_id", "avatar_id") is not None
            ),
            None,
        )

        if profile_replica or job_replica or profile_status in _READY or any(status in _READY for status in job_statuses):
            return AvatarStateDimension(
                status="ready",
                score=1.0,
                label="Face / video avatar",
                reason="Durable Tavus/avatar training evidence exists.",
                evidence=["tavus_replica_or_ready_training_status"],
                next_action="Start runtime or explicit Tavus real preview.",
            )

        if profile_status in _IN_PROGRESS or any(status in _IN_PROGRESS for status in job_statuses):
            return AvatarStateDimension(
                status="in_progress",
                score=0.55,
                label="Face / video avatar",
                reason="Avatar video training is still in progress.",
                evidence=["avatar_training_in_progress"],
                next_action="Wait for provider training completion.",
            )

        if profile_status in _BLOCKED or any(status in _BLOCKED for status in job_statuses):
            return AvatarStateDimension(
                status="blocked",
                score=0.0,
                label="Face / video avatar",
                reason="Avatar video training failed or is blocked.",
                evidence=["avatar_training_failed"],
                next_action="Review provider error and retrain with valid evidence.",
            )

        return AvatarStateDimension(
            status="missing",
            score=0.0,
            label="Face / video avatar",
            reason="No durable Tavus/avatar training evidence was found.",
            evidence=[],
            next_action="Upload valid face/video evidence and start avatar training.",
        )

    def _voice_state(self, profile: Optional[Any], training_jobs: list[Any]) -> AvatarStateDimension:
        voice_id = self._first_attr(profile, "voice_id", "elevenlabs_voice_id", "voice_dna_id")
        status = self._normalize_status(self._first_attr(profile, "voice_status", "voice_training_status"))

        voice_jobs = [
            job for job in training_jobs
            if "eleven" in str(self._first_attr(job, "provider", "provider_name", "job_provider", default="")).lower()
            or "voice" in str(self._first_attr(job, "job_type", "training_type", default="")).lower()
        ]

        job_statuses = [
            self._normalize_status(self._first_attr(job, "status", "training_status", "provider_status"))
            for job in voice_jobs
        ]

        if voice_id or status in _READY or any(job_status in _READY for job_status in job_statuses):
            return AvatarStateDimension(
                status="ready",
                score=1.0,
                label="Voice",
                reason=(
                    "A profile-bound trained voice provider output exists. "
                    "Speaker identity verification has not been evaluated."
                ),
                evidence=["voice_id_or_ready_voice_status"],
                next_action="Use trained voice in runtime.",
            )

        if status in _IN_PROGRESS or any(job_status in _IN_PROGRESS for job_status in job_statuses):
            return AvatarStateDimension(
                status="in_progress",
                score=0.55,
                label="Voice",
                reason="Voice training is still in progress.",
                evidence=["voice_training_in_progress"],
                next_action="Wait for voice training completion.",
            )

        if status in _BLOCKED or any(job_status in _BLOCKED for job_status in job_statuses):
            return AvatarStateDimension(
                status="blocked",
                score=0.0,
                label="Voice",
                reason="Voice training failed or is blocked.",
                evidence=["voice_training_failed"],
                next_action="Review voice evidence and retrain.",
            )

        return AvatarStateDimension(
            status="missing",
            score=0.0,
            label="Voice",
            reason="No trained voice identity evidence was found.",
            evidence=[],
            next_action="Upload sufficient voice evidence.",
        )

    def _identity_verification_state(
        self,
        profile: Optional[Any],
    ) -> AvatarStateDimension:
        status = self._normalize_status(
            self._first_attr(
                profile,
                "identity_verification_status",
                default="not_evaluated",
            )
        )

        receipt_id = self._first_attr(
            profile,
            "current_identity_verification_receipt_id",
        )

        verified_at = self._first_attr(
            profile,
            "identity_verified_at",
        )

        evidence = []

        if receipt_id is not None:
            evidence.append(
                "identity_verification_receipt"
            )

        if status == "verified":
            if receipt_id is None or verified_at is None:
                return AvatarStateDimension(
                    status="blocked",
                    score=0.0,
                    label="Identity verification",
                    reason=(
                        "The profile claims verified identity without a "
                        "complete verification receipt projection."
                    ),
                    evidence=evidence,
                    next_action=(
                        "Re-evaluate generated face and voice output."
                    ),
                )

            return AvatarStateDimension(
                status="ready",
                score=1.0,
                label="Identity verification",
                reason=(
                    "A model-versioned identity verification receipt "
                    "approved the generated output."
                ),
                evidence=evidence,
                next_action=None,
            )

        if status == "evaluating":
            return AvatarStateDimension(
                status="in_progress",
                score=0.25,
                label="Identity verification",
                reason=(
                    "Generated face and voice output are being evaluated."
                ),
                evidence=evidence,
                next_action=(
                    "Wait for identity verification to complete."
                ),
            )

        if status in {
            "rejected",
            "error",
        }:
            return AvatarStateDimension(
                status="blocked",
                score=0.0,
                label="Identity verification",
                reason=(
                    "Generated output did not receive identity approval."
                ),
                evidence=evidence,
                next_action=(
                    "Review the verification result and regenerate "
                    "or retrain the affected identity dimension."
                ),
            )

        if status == "inconclusive":
            return AvatarStateDimension(
                status="unknown",
                score=0.0,
                label="Identity verification",
                reason=(
                    "Identity verification was inconclusive."
                ),
                evidence=evidence,
                next_action=(
                    "Collect stronger source evidence and evaluate again."
                ),
            )

        return AvatarStateDimension(
            status="missing",
            score=0.0,
            label="Identity verification",
            reason=(
                "Generated output has not been biometrically "
                "identity-verified."
            ),
            evidence=evidence,
            next_action=(
                "Evaluate generated face and voice output against "
                "the profile-bound source identity."
            ),
        )

    def _memory_state(self, profile: Optional[Any]) -> AvatarStateDimension:
        memory_score = self._number_attr(profile, "memory_score", "memory_quality_score", "memory_confidence")
        memory_count = self._number_attr(profile, "memory_count", "memories_count", "source_memory_count")

        if memory_score is not None:
            if memory_score >= 0.75:
                return AvatarStateDimension(
                    status="ready",
                    score=min(memory_score, 1.0),
                    label="Memory",
                    reason="Memory quality score is high enough for personalized recall.",
                    evidence=["memory_quality_score"],
                    next_action="Use memory retrieval in runtime.",
                )
            return AvatarStateDimension(
                status="in_progress",
                score=max(min(memory_score, 1.0), 0.25),
                label="Memory",
                reason="Memory quality exists but is not yet strong.",
                evidence=["memory_quality_score"],
                next_action="Add more specific memories and life-story evidence.",
            )

        if memory_count is not None and memory_count > 0:
            return AvatarStateDimension(
                status="in_progress",
                score=0.45,
                label="Memory",
                reason="Memory evidence exists but quality is not proven.",
                evidence=["memory_count"],
                next_action="Improve memory depth and source coverage.",
            )

        return AvatarStateDimension(
            status="missing",
            score=0.0,
            label="Memory",
            reason="No memory-readiness evidence was found.",
            evidence=[],
            next_action="Add memories, stories and source context.",
        )

    def _behavior_state(self, profile: Optional[Any]) -> AvatarStateDimension:
        behavior_score = self._number_attr(profile, "behavior_score", "persona_score", "persona_confidence", "style_confidence")

        if behavior_score is not None and behavior_score >= 0.7:
            return AvatarStateDimension(
                status="ready",
                score=min(behavior_score, 1.0),
                label="Behavior / persona",
                reason="Behavior/persona quality score is strong enough.",
                evidence=["persona_or_behavior_score"],
                next_action="Use behavior profile in runtime.",
            )

        if behavior_score is not None:
            return AvatarStateDimension(
                status="in_progress",
                score=max(min(behavior_score, 1.0), 0.25),
                label="Behavior / persona",
                reason="Behavior/persona evidence exists but needs more specificity.",
                evidence=["persona_or_behavior_score"],
                next_action="Add examples of tone, reactions, phrases and habits.",
            )

        return AvatarStateDimension(
            status="unknown",
            score=0.0,
            label="Behavior / persona",
            reason="No explicit behavior/persona readiness evidence was found.",
            evidence=[],
            next_action="Collect behavior examples and persona evidence.",
        )

    def _consent_state(self, profile: Optional[Any]) -> AvatarStateDimension:
        consent_value = self._first_attr(
            profile,
            "consent_verified",
            "consent_granted",
            "has_consent",
            "training_consent",
            "media_consent",
        )

        if consent_value is True or str(consent_value).lower() in {"true", "granted", "approved", "accepted"}:
            return AvatarStateDimension(
                status="ready",
                score=1.0,
                label="Consent / evidence safety",
                reason="Consent evidence is present.",
                evidence=["consent_granted"],
                next_action="Provider training and runtime may proceed.",
            )

        if consent_value is False or str(consent_value).lower() in {"false", "denied", "revoked"}:
            return AvatarStateDimension(
                status="blocked",
                score=0.0,
                label="Consent / evidence safety",
                reason="Consent is missing, denied or revoked.",
                evidence=["consent_denied_or_missing"],
                next_action="Collect explicit consent before provider/runtime use.",
            )

        return AvatarStateDimension(
            status="unknown",
            score=0.25,
            label="Consent / evidence safety",
            reason="Consent state is not explicitly proven in the profile snapshot.",
            evidence=[],
            next_action="Verify consent before external provider generation.",
        )

    def _runtime_state(
        self,
        *,
        face_video: AvatarStateDimension,
        voice: AvatarStateDimension,
        consent: AvatarStateDimension,
    ) -> AvatarStateDimension:
        if consent.status == "blocked":
            return AvatarStateDimension(
                status="blocked",
                score=0.0,
                label="Runtime",
                reason="Runtime is blocked by consent state.",
                evidence=["consent_blocked"],
                next_action="Resolve consent before starting runtime.",
            )

        if face_video.status == "ready" and voice.status == "ready" and consent.status == "ready":
            return AvatarStateDimension(
                status="ready",
                score=1.0,
                label="Runtime",
                reason="Face/video, voice and consent are ready.",
                evidence=["face_video_ready", "voice_ready", "consent_ready"],
                next_action="Start avatar runtime.",
            )

        return AvatarStateDimension(
            status="missing",
            score=0.0,
            label="Runtime",
            reason="Runtime requires ready face/video, voice and consent.",
            evidence=[],
            next_action="Complete missing training and consent requirements.",
        )

    def _overall_status(
        self,
        *,
        can_start_runtime: bool,
        face_video: AvatarStateDimension,
        voice: AvatarStateDimension,
        consent: AvatarStateDimension,
    ) -> str:
        if consent.status == "blocked" or face_video.status == "blocked" or voice.status == "blocked":
            return "blocked"
        if can_start_runtime:
            return "ready_for_runtime"
        if face_video.status in {"missing", "in_progress"} or voice.status in {"missing", "in_progress"}:
            return "needs_training"
        return "unknown"

    def _next_best_action(
        self,
        *,
        face_video: AvatarStateDimension,
        voice: AvatarStateDimension,
        memory: AvatarStateDimension,
        behavior: AvatarStateDimension,
        consent: AvatarStateDimension,
        can_start_runtime: bool,
    ) -> str:
        if consent.status == "blocked":
            return "Resolve consent before starting avatar generation or runtime."
        if can_start_runtime:
            return "Start avatar runtime."
        if face_video.status == "missing":
            return face_video.next_action or "Complete face/video avatar training."
        if voice.status == "missing":
            return voice.next_action or "Complete voice training."
        if memory.status in {"missing", "unknown"}:
            return memory.next_action or "Add memories."
        if behavior.status in {"missing", "unknown"}:
            return behavior.next_action or "Add behavior/persona examples."
        return "Continue improving avatar evidence quality."

    def _weighted_score(self, **dimensions: AvatarStateDimension) -> float:
        weights = {
            "face_video": 0.26,
            "voice": 0.24,
            "memory": 0.16,
            "behavior": 0.13,
            "consent": 0.16,
            "runtime": 0.05,
        }

        score = sum(dimensions[name].score * weight for name, weight in weights.items())
        return round(max(0.0, min(score, 1.0)), 4)

    def _normalize_status(self, value: Any) -> str:
        return str(value or "").strip().lower()

    def _first_attr(self, obj: Optional[Any], *names: str, default: Any = None) -> Any:
        if obj is None:
            return default

        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj.get(name)
            if hasattr(obj, name):
                return getattr(obj, name)

        return default

    def _number_attr(self, obj: Optional[Any], *names: str) -> Optional[float]:
        value = self._first_attr(obj, *names)
        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None
