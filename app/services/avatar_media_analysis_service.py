from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import av
import cv2
import numpy as np

from app.services.avatar_media_evidence_bridge_service import (
    AvatarMediaEvidenceAnalysis,
)


class AvatarMediaAnalysisError(RuntimeError):
    def __init__(self, user_message: str) -> None:
        self.user_message = user_message
        super().__init__(user_message)


@dataclass(frozen=True)
class _VisualFrameAnalysis:
    quality_score: float
    has_face: bool
    has_frontal_face: bool
    has_clear_lighting: bool
    multiple_faces: bool


class AvatarMediaAnalysisService:
    """Server-authoritative technical validation for uploaded source media.

    This service proves that media is decodable and technically usable. It
    intentionally does not claim biometric identity. Identity comparison is
    owned by the separately licensed identity-verification boundary.
    """

    ANALYSIS_VERSION = "server-media-technical-v1"
    MIN_VISUAL_EDGE = 384
    MIN_AUDIO_SECONDS = 3.0
    MIN_VIDEO_SECONDS = 3.0
    MAX_VIDEO_SECONDS = 45.5
    MAX_VIDEO_SAMPLE_FRAMES = 12

    def __init__(self) -> None:
        cascade_path = (
            Path(cv2.data.haarcascades)
            / "haarcascade_frontalface_default.xml"
        )
        self._face_detector = cv2.CascadeClassifier(
            str(cascade_path)
        )
        if self._face_detector.empty():
            raise AvatarMediaAnalysisError(
                "The server face detector is unavailable."
            )

    def analyze(
        self,
        *,
        storage_path: str,
        asset_type: str,
        content_type: str,
    ) -> AvatarMediaEvidenceAnalysis:
        path = Path(storage_path).expanduser().resolve()
        if not path.is_file():
            raise AvatarMediaAnalysisError(
                "Uploaded media is unavailable for analysis."
            )

        normalized_type = asset_type.strip().lower()
        if normalized_type in {"image", "reference"}:
            return self._analyze_image(path)
        if normalized_type in {"video", "training_sample"}:
            return self._analyze_video(path)
        if normalized_type in {"voice", "audio"}:
            return self._analyze_audio(path)

        raise AvatarMediaAnalysisError(
            f"Unsupported avatar source type: {normalized_type or content_type}"
        )

    def _analyze_image(
        self,
        path: Path,
    ) -> AvatarMediaEvidenceAnalysis:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise AvatarMediaAnalysisError(
                "The selected image could not be decoded."
            )

        frame = self._analyze_visual_frame(image)
        if not frame.has_face:
            raise AvatarMediaAnalysisError(
                "No clear face was detected in the selected image."
            )
        if frame.multiple_faces:
            raise AvatarMediaAnalysisError(
                "The selected image must show only one person."
            )
        if not frame.has_clear_lighting:
            raise AvatarMediaAnalysisError(
                "The selected image needs clearer, more even lighting."
            )

        return AvatarMediaEvidenceAnalysis(
            quality_score=frame.quality_score,
            has_face=True,
            has_frontal_face=frame.has_frontal_face,
            has_clear_lighting=True,
            recommended_for_avatar=(
                frame.quality_score >= 0.72
                and frame.has_frontal_face
            ),
            analysis_version=self.ANALYSIS_VERSION,
            analysis_metadata={
                "analysis_kind": "identity_photo",
                "pixel_analysis_performed": True,
                "biometric_analysis_performed": False,
            },
        )

    def _analyze_video(
        self,
        path: Path,
    ) -> AvatarMediaEvidenceAnalysis:
        try:
            with av.open(str(path)) as container:
                stream = next(
                    (
                        candidate
                        for candidate in container.streams
                        if candidate.type == "video"
                    ),
                    None,
                )
                if stream is None:
                    raise AvatarMediaAnalysisError(
                        "The selected file contains no video track."
                    )

                duration = self._stream_duration_seconds(
                    stream,
                    container.duration,
                )
                if duration < self.MIN_VIDEO_SECONDS:
                    raise AvatarMediaAnalysisError(
                        "The video is too short for motion training."
                    )
                if duration > self.MAX_VIDEO_SECONDS:
                    raise AvatarMediaAnalysisError(
                        "The video must be prepared to 45 seconds or less."
                    )

                frames = list(
                    self._sample_video_frames(
                        container,
                        stream,
                        duration,
                    )
                )
        except AvatarMediaAnalysisError:
            raise
        except Exception as error:
            raise AvatarMediaAnalysisError(
                "The selected video could not be decoded."
            ) from error

        if len(frames) < 3:
            raise AvatarMediaAnalysisError(
                "The video does not contain enough readable frames."
            )

        analyses = [
            self._analyze_visual_frame(frame)
            for frame in frames
        ]
        face_ratio = self._ratio(
            item.has_face for item in analyses
        )
        clear_ratio = self._ratio(
            item.has_clear_lighting for item in analyses
        )
        multiple_ratio = self._ratio(
            item.multiple_faces for item in analyses
        )

        motion_usable = (
            face_ratio >= 0.6
            and clear_ratio >= 0.6
            and multiple_ratio == 0.0
        )
        if not motion_usable:
            raise AvatarMediaAnalysisError(
                "Keep one clearly lit face visible throughout the video."
            )

        average_quality = float(
            np.mean(
                [item.quality_score for item in analyses]
            )
        )
        quality_score = max(0.72, average_quality)

        return AvatarMediaEvidenceAnalysis(
            quality_score=self._clamp(quality_score),
            has_face=True,
            has_frontal_face=(
                self._ratio(
                    item.has_frontal_face for item in analyses
                )
                >= 0.6
            ),
            has_clear_lighting=True,
            motion_usable=True,
            motion_quality_score=self._clamp(average_quality),
            head_pose_stability_score=self._clamp(face_ratio),
            recommended_for_avatar=True,
            analysis_version=self.ANALYSIS_VERSION,
            analysis_metadata={
                "analysis_kind": "motion_video",
                "pixel_analysis_performed": True,
                "biometric_analysis_performed": False,
                "duration_seconds": round(duration, 3),
                "sampled_frames": len(analyses),
                "face_frame_ratio": round(face_ratio, 3),
            },
        )

    def _analyze_audio(
        self,
        path: Path,
    ) -> AvatarMediaEvidenceAnalysis:
        sample_arrays: list[np.ndarray] = []
        sample_rate = 0
        decoded_samples = 0

        try:
            with av.open(str(path)) as container:
                stream = next(
                    (
                        candidate
                        for candidate in container.streams
                        if candidate.type == "audio"
                    ),
                    None,
                )
                if stream is None:
                    raise AvatarMediaAnalysisError(
                        "The selected file contains no audio track."
                    )

                sample_rate = int(
                    stream.codec_context.sample_rate or 0
                )
                for frame in container.decode(stream):
                    values = frame.to_ndarray()
                    if values.size == 0:
                        continue
                    normalized = self._normalized_audio(values)
                    sample_arrays.append(normalized.reshape(-1))
                    decoded_samples += normalized.size
                    if decoded_samples >= max(sample_rate, 48_000) * 120:
                        break
        except AvatarMediaAnalysisError:
            raise
        except Exception as error:
            raise AvatarMediaAnalysisError(
                "The selected recording could not be decoded."
            ) from error

        if sample_rate <= 0 or not sample_arrays:
            raise AvatarMediaAnalysisError(
                "The selected recording contains no readable audio."
            )

        samples = np.concatenate(sample_arrays)
        duration = samples.size / float(sample_rate)
        rms = float(np.sqrt(np.mean(np.square(samples))))
        clipping_ratio = float(np.mean(np.abs(samples) >= 0.985))

        voice_usable = (
            duration >= self.MIN_AUDIO_SECONDS
            and rms >= 0.008
            and clipping_ratio <= 0.03
        )
        if not voice_usable:
            raise AvatarMediaAnalysisError(
                "Use a clear recording of at least three seconds without clipping."
            )

        duration_score = min(duration / 20.0, 1.0)
        signal_score = min(rms / 0.08, 1.0)
        quality_score = self._clamp(
            0.72
            + (0.16 * duration_score)
            + (0.12 * signal_score)
            - (0.2 * clipping_ratio)
        )

        return AvatarMediaEvidenceAnalysis(
            quality_score=quality_score,
            has_voice=True,
            voice_usable=True,
            recommended_for_avatar=True,
            analysis_version=self.ANALYSIS_VERSION,
            analysis_metadata={
                "analysis_kind": "voice_sample",
                "signal_analysis_performed": True,
                "speaker_identity_verified": False,
                "duration_seconds": round(duration, 3),
                "sample_rate": sample_rate,
                "rms": round(rms, 6),
                "clipping_ratio": round(clipping_ratio, 6),
            },
        )

    def _sample_video_frames(
        self,
        container: av.container.InputContainer,
        stream: av.video.stream.VideoStream,
        duration: float,
    ) -> Iterable[np.ndarray]:
        interval = max(
            duration / float(self.MAX_VIDEO_SAMPLE_FRAMES),
            0.25,
        )
        next_time = 0.0
        emitted = 0

        for frame in container.decode(stream):
            frame_time = float(frame.time or 0.0)
            if frame_time + 0.001 < next_time:
                continue
            yield frame.to_ndarray(format="bgr24")
            emitted += 1
            next_time += interval
            if emitted >= self.MAX_VIDEO_SAMPLE_FRAMES:
                break

    def _analyze_visual_frame(
        self,
        image: np.ndarray,
    ) -> _VisualFrameAnalysis:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        equalized = cv2.equalizeHist(gray)
        faces = self._face_detector.detectMultiScale(
            equalized,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(48, 48),
        )

        has_face = len(faces) > 0
        largest_face = (
            max(faces, key=lambda rect: rect[2] * rect[3])
            if has_face
            else None
        )
        largest_face_area = (
            int(largest_face[2] * largest_face[3])
            if largest_face is not None
            else 0
        )
        significant_faces = [
            face
            for face in faces
            if int(face[2] * face[3])
            >= max(int(largest_face_area * 0.25), 48 * 48)
        ]
        multiple_faces = len(significant_faces) > 1

        mean_luma = float(np.mean(gray))
        contrast = float(np.std(gray))
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        has_clear_lighting = (
            38.0 <= mean_luma <= 220.0
            and contrast >= 18.0
        )

        face_score = 0.0
        centered_score = 0.0
        if largest_face is not None:
            x, y, face_width, face_height = [
                int(value) for value in largest_face
            ]
            face_ratio = (
                face_width * face_height
            ) / float(max(width * height, 1))
            face_score = min(face_ratio / 0.12, 1.0)
            face_center_x = x + (face_width / 2.0)
            face_center_y = y + (face_height / 2.0)
            offset = (
                abs(face_center_x - (width / 2.0)) / max(width, 1)
                + abs(face_center_y - (height / 2.0)) / max(height, 1)
            )
            centered_score = self._clamp(1.0 - offset)

        resolution_score = min(
            min(width, height) / float(self.MIN_VISUAL_EDGE),
            1.0,
        )
        lighting_score = 1.0 if has_clear_lighting else 0.35
        sharpness_score = min(sharpness / 120.0, 1.0)
        quality_score = self._clamp(
            (0.4 * face_score)
            + (0.2 * resolution_score)
            + (0.17 * lighting_score)
            + (0.13 * sharpness_score)
            + (0.1 * centered_score)
        )

        return _VisualFrameAnalysis(
            quality_score=quality_score,
            has_face=has_face,
            has_frontal_face=has_face and not multiple_faces,
            has_clear_lighting=has_clear_lighting,
            multiple_faces=multiple_faces,
        )

    @staticmethod
    def _stream_duration_seconds(
        stream: object,
        container_duration: int | None,
    ) -> float:
        duration = getattr(stream, "duration", None)
        time_base = getattr(stream, "time_base", None)
        if duration is not None and time_base is not None:
            return float(duration * time_base)
        if container_duration is not None:
            return float(container_duration / av.time_base)
        return 0.0

    @staticmethod
    def _normalized_audio(values: np.ndarray) -> np.ndarray:
        if np.issubdtype(values.dtype, np.integer):
            limit = float(max(abs(np.iinfo(values.dtype).min), np.iinfo(values.dtype).max))
            return values.astype(np.float32) / limit
        return np.clip(values.astype(np.float32), -1.0, 1.0)

    @staticmethod
    def _ratio(values: Iterable[bool]) -> float:
        materialized = list(values)
        if not materialized:
            return 0.0
        return sum(1 for value in materialized if value) / len(materialized)

    @staticmethod
    def _clamp(value: float) -> float:
        return min(max(float(value), 0.0), 1.0)
