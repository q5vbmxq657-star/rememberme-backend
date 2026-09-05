from __future__ import annotations

import math

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

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


@dataclass(frozen=True)
class _VideoFrameSummary:
    frame_count: int
    face_frame_count: int
    frontal_face_frame_count: int
    clear_lighting_frame_count: int
    multiple_face_frame_count: int
    required_face_frame_count: int
    repeated_multiple_face_threshold: int

    @property
    def face_ratio(self) -> float:
        return self.face_frame_count / max(self.frame_count, 1)

    @property
    def frontal_face_ratio(self) -> float:
        return self.frontal_face_frame_count / max(self.frame_count, 1)

    @property
    def clear_lighting_ratio(self) -> float:
        return self.clear_lighting_frame_count / max(self.frame_count, 1)

    @property
    def multiple_face_ratio(self) -> float:
        return self.multiple_face_frame_count / max(self.frame_count, 1)

    @property
    def has_sustained_face_evidence(self) -> bool:
        return (
            self.frame_count > 0
            and self.face_frame_count >= self.required_face_frame_count
        )

    @property
    def repeatedly_shows_multiple_people(self) -> bool:
        return (
            self.frame_count > 0
            and self.multiple_face_frame_count
            >= self.repeated_multiple_face_threshold
        )

    @property
    def motion_usable(self) -> bool:
        return (
            self.has_sustained_face_evidence
            and not self.repeatedly_shows_multiple_people
        )


class AvatarMediaAnalysisService:
    """Server-authoritative technical validation for uploaded source media.

    This service proves that media is decodable and technically usable. It
    intentionally does not claim biometric identity. Identity comparison is
    owned by the separately licensed identity-verification boundary.
    """

    ANALYSIS_VERSION = "server-media-technical-v2"
    MIN_VISUAL_EDGE = 384
    MIN_AUDIO_SECONDS = 3.0
    MIN_VIDEO_SECONDS = 3.0
    MAX_VIDEO_SECONDS = 45.5
    MAX_VIDEO_SAMPLE_FRAMES = 12
    MIN_VIDEO_FACE_FRAME_RATIO = 0.45
    MULTIPLE_PEOPLE_FRAME_RATIO = 0.25

    def __init__(self) -> None:
        cascade_specs = (
            ("haarcascade_frontalface_default.xml", False),
            ("haarcascade_frontalface_alt2.xml", False),
            ("haarcascade_profileface.xml", True),
        )
        detectors = []
        mirrored_detector_ids: set[int] = set()
        for cascade_name, requires_mirror in cascade_specs:
            detector = cv2.CascadeClassifier(
                str(
                    Path(cv2.data.haarcascades)
                    / cascade_name
                )
            )
            if not detector.empty():
                detectors.append(detector)
                if requires_mirror:
                    mirrored_detector_ids.add(id(detector))

        if not detectors:
            raise AvatarMediaAnalysisError(
                "The server face detector is unavailable."
            )

        self._face_detectors = tuple(detectors)
        self._mirrored_face_detector_ids = frozenset(
            mirrored_detector_ids
        )
        # Kept as a compatibility alias for focused tests and diagnostics.
        self._face_detector = self._face_detectors[0]

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
        if normalized_type == "memory_image":
            return self._analyze_memory_image(path)
        if normalized_type == "memory_video":
            return self._analyze_memory_video(path)
        if normalized_type in {"voice", "audio"}:
            return self._analyze_audio(path)

        raise AvatarMediaAnalysisError(
            f"Unsupported avatar source type: {normalized_type or content_type}"
        )

    def _analyze_memory_image(
        self,
        path: Path,
    ) -> AvatarMediaEvidenceAnalysis:
        image = cv2.imread(str(path), cv2.IMREAD_COLOR)
        if image is None or image.size == 0:
            raise AvatarMediaAnalysisError(
                "The selected photo could not be decoded."
            )

        height, width = image.shape[:2]
        return AvatarMediaEvidenceAnalysis(
            quality_score=1.0,
            has_face=False,
            has_frontal_face=False,
            has_clear_lighting=False,
            recommended_for_avatar=False,
            analysis_version=self.ANALYSIS_VERSION,
            analysis_metadata={
                "analysis_kind": "memory_photo",
                "pixel_analysis_performed": True,
                "biometric_analysis_performed": False,
                "width": int(width),
                "height": int(height),
            },
        )

    def _analyze_memory_video(
        self,
        path: Path,
    ) -> AvatarMediaEvidenceAnalysis:
        try:
            with av.open(str(path)) as container:
                stream = next(
                    (candidate for candidate in container.streams if candidate.type == "video"),
                    None,
                )
                if stream is None:
                    raise AvatarMediaAnalysisError(
                        "The selected file contains no video track."
                    )

                frame = next(container.decode(stream), None)
                if frame is None:
                    raise AvatarMediaAnalysisError(
                        "The selected video could not be decoded."
                    )

                return AvatarMediaEvidenceAnalysis(
                    quality_score=1.0,
                    has_face=False,
                    has_frontal_face=False,
                    has_clear_lighting=False,
                    recommended_for_avatar=False,
                    analysis_version=self.ANALYSIS_VERSION,
                    analysis_metadata={
                        "analysis_kind": "memory_video",
                        "pixel_analysis_performed": True,
                        "biometric_analysis_performed": False,
                        "width": int(frame.width),
                        "height": int(frame.height),
                    },
                )
        except AvatarMediaAnalysisError:
            raise
        except Exception as error:
            raise AvatarMediaAnalysisError(
                "The selected video could not be decoded."
            ) from error

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
        summary = self._summarize_video_frames(
            analyses
        )
        if not summary.has_sustained_face_evidence:
            raise AvatarMediaAnalysisError(
                "A clear face could not be confirmed across enough of the video."
            )
        if summary.repeatedly_shows_multiple_people:
            raise AvatarMediaAnalysisError(
                "The video repeatedly shows more than one person."
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
                summary.frontal_face_ratio >= 0.45
            ),
            has_clear_lighting=(
                summary.clear_lighting_ratio >= 0.5
            ),
            motion_usable=True,
            motion_quality_score=self._clamp(average_quality),
            head_pose_stability_score=self._clamp(
                summary.face_ratio
            ),
            recommended_for_avatar=True,
            analysis_version=self.ANALYSIS_VERSION,
            analysis_metadata={
                "analysis_kind": "motion_video",
                "pixel_analysis_performed": True,
                "biometric_analysis_performed": False,
                "duration_seconds": round(duration, 3),
                "sampled_frames": len(analyses),
                "face_frame_ratio": round(summary.face_ratio, 3),
                "frontal_face_frame_ratio": round(
                    summary.frontal_face_ratio,
                    3,
                ),
                "clear_lighting_frame_ratio": round(
                    summary.clear_lighting_ratio,
                    3,
                ),
                "multiple_face_frame_ratio": round(
                    summary.multiple_face_ratio,
                    3,
                ),
                "required_face_frames": (
                    summary.required_face_frame_count
                ),
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
        primary_analysis = self._analyze_visual_frame_orientation(
            image
        )
        if primary_analysis.has_face:
            return primary_analysis

        analyses = [primary_analysis]
        analyses.extend(
            self._analyze_visual_frame_orientation(candidate)
            for candidate in self._rotated_orientation_candidates(
                image
            )
        )

        face_analyses = [
            analysis
            for analysis in analyses
            if analysis.has_face
        ]
        if face_analyses:
            return max(
                face_analyses,
                key=lambda analysis: analysis.quality_score,
            )

        return max(
            analyses,
            key=lambda analysis: analysis.quality_score,
        )

    def _analyze_visual_frame_orientation(
        self,
        image: np.ndarray,
    ) -> _VisualFrameAnalysis:
        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        equalized = cv2.equalizeHist(gray)
        faces = self._detect_faces(
            equalized
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

        lighting_region = gray
        if largest_face is not None:
            x, y, face_width, face_height = [
                int(value) for value in largest_face
            ]
            face_region = gray[
                max(y, 0):min(y + face_height, height),
                max(x, 0):min(x + face_width, width),
            ]
            if face_region.size > 0:
                lighting_region = face_region

        mean_luma = float(np.mean(lighting_region))
        contrast = float(np.std(lighting_region))
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

    def _detect_faces(
        self,
        equalized: np.ndarray,
    ) -> list[tuple[int, int, int, int]]:
        detectors = getattr(
            self,
            "_face_detectors",
            None,
        )
        if not detectors:
            detector = getattr(
                self,
                "_face_detector",
                None,
            )
            detectors = (detector,) if detector is not None else ()

        height, width = equalized.shape[:2]
        flipped = cv2.flip(equalized, 1)
        candidates: list[tuple[int, int, int, int]] = []
        mirrored_detector_ids = getattr(
            self,
            "_mirrored_face_detector_ids",
            frozenset(),
        )

        for detector in detectors:
            sources = [(equalized, False)]
            if id(detector) in mirrored_detector_ids:
                sources.append((flipped, True))

            for source, is_flipped in sources:
                detected = detector.detectMultiScale(
                    source,
                    scaleFactor=1.08,
                    minNeighbors=4,
                    minSize=(48, 48),
                )
                for raw_face in detected:
                    x, y, face_width, face_height = (
                        int(value) for value in raw_face
                    )
                    if is_flipped:
                        x = width - x - face_width
                    candidates.append(
                        (x, y, face_width, face_height)
                    )

        return self._merge_overlapping_faces(
            candidates,
            frame_width=width,
            frame_height=height,
        )

    @classmethod
    def _merge_overlapping_faces(
        cls,
        faces: Sequence[tuple[int, int, int, int]],
        *,
        frame_width: int,
        frame_height: int,
    ) -> list[tuple[int, int, int, int]]:
        normalized = [
            (
                max(0, min(x, frame_width - 1)),
                max(0, min(y, frame_height - 1)),
                max(
                    1,
                    min(
                        face_width,
                        frame_width - max(0, min(x, frame_width - 1)),
                    ),
                ),
                max(
                    1,
                    min(
                        face_height,
                        frame_height - max(0, min(y, frame_height - 1)),
                    ),
                ),
            )
            for x, y, face_width, face_height in faces
            if face_width > 0 and face_height > 0
        ]
        normalized.sort(
            key=lambda face: face[2] * face[3],
            reverse=True,
        )

        merged: list[tuple[int, int, int, int]] = []
        for candidate in normalized:
            if any(
                cls._same_face(candidate, existing)
                for existing in merged
            ):
                continue
            merged.append(candidate)

        return merged

    @staticmethod
    def _same_face(
        first: tuple[int, int, int, int],
        second: tuple[int, int, int, int],
    ) -> bool:
        first_x, first_y, first_width, first_height = first
        second_x, second_y, second_width, second_height = second

        intersection_left = max(first_x, second_x)
        intersection_top = max(first_y, second_y)
        intersection_right = min(
            first_x + first_width,
            second_x + second_width,
        )
        intersection_bottom = min(
            first_y + first_height,
            second_y + second_height,
        )
        intersection_width = max(
            intersection_right - intersection_left,
            0,
        )
        intersection_height = max(
            intersection_bottom - intersection_top,
            0,
        )
        intersection = intersection_width * intersection_height
        union = (
            first_width * first_height
            + second_width * second_height
            - intersection
        )
        overlap = intersection / max(union, 1)

        first_center = (
            first_x + first_width / 2.0,
            first_y + first_height / 2.0,
        )
        second_center = (
            second_x + second_width / 2.0,
            second_y + second_height / 2.0,
        )
        center_distance = math.hypot(
            first_center[0] - second_center[0],
            first_center[1] - second_center[1],
        )
        shared_scale = max(
            min(
                first_width,
                first_height,
                second_width,
                second_height,
            ),
            1,
        )

        return overlap >= 0.3 or center_distance <= shared_scale * 0.35

    @classmethod
    def _summarize_video_frames(
        cls,
        analyses: Sequence[_VisualFrameAnalysis],
    ) -> _VideoFrameSummary:
        frame_count = len(analyses)
        required_face_frames = min(
            frame_count,
            max(
                2,
                math.ceil(
                    frame_count
                    * cls.MIN_VIDEO_FACE_FRAME_RATIO
                ),
            ),
        )
        repeated_multiple_threshold = min(
            frame_count,
            max(
                2,
                math.ceil(
                    frame_count
                    * cls.MULTIPLE_PEOPLE_FRAME_RATIO
                ),
            ),
        )

        return _VideoFrameSummary(
            frame_count=frame_count,
            face_frame_count=sum(
                1 for item in analyses if item.has_face
            ),
            frontal_face_frame_count=sum(
                1 for item in analyses if item.has_frontal_face
            ),
            clear_lighting_frame_count=sum(
                1 for item in analyses if item.has_clear_lighting
            ),
            multiple_face_frame_count=sum(
                1 for item in analyses if item.multiple_faces
            ),
            required_face_frame_count=required_face_frames,
            repeated_multiple_face_threshold=(
                repeated_multiple_threshold
            ),
        )

    @staticmethod
    def _rotated_orientation_candidates(
        image: np.ndarray,
    ) -> tuple[np.ndarray, ...]:
        """Return alternate display orientations an iOS asset may encode.

        MOV/MP4 files commonly store portrait orientation in a display matrix
        instead of rotating encoded pixels. PyAV exposes decoded pixels, so a
        portrait face can otherwise reach the detector sideways even though it
        appears upright in Photos and in AVFoundation.
        """
        return (
            cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE),
            cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE),
            cv2.rotate(image, cv2.ROTATE_180),
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
    def _clamp(value: float) -> float:
        return min(max(float(value), 0.0), 1.0)
