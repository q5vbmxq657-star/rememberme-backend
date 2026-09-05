from __future__ import annotations

import wave
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services.avatar_media_analysis_service import (
    AvatarMediaAnalysisError,
    AvatarMediaAnalysisService,
    _VisualFrameAnalysis,
)


def test_blank_image_is_rejected(
    tmp_path: Path,
):
    path = tmp_path / "blank.jpg"
    image = np.full(
        (800, 800, 3),
        180,
        dtype=np.uint8,
    )
    assert cv2.imwrite(str(path), image)

    with pytest.raises(
        AvatarMediaAnalysisError,
        match="No clear face",
    ):
        AvatarMediaAnalysisService().analyze(
            storage_path=str(path),
            asset_type="image",
            content_type="image/jpeg",
        )


def test_memory_image_accepts_non_portrait_scene(
    tmp_path: Path,
):
    path = tmp_path / "memory-scene.jpg"
    image = np.full(
        (640, 960, 3),
        180,
        dtype=np.uint8,
    )
    assert cv2.imwrite(str(path), image)

    analysis = AvatarMediaAnalysisService().analyze(
        storage_path=str(path),
        asset_type="memory_image",
        content_type="image/jpeg",
    )

    assert analysis.recommended_for_avatar is False
    assert analysis.analysis_metadata is not None
    assert analysis.analysis_metadata["analysis_kind"] == "memory_photo"
    assert analysis.analysis_metadata["biometric_analysis_performed"] is False


def test_clear_audio_is_voice_usable(
    tmp_path: Path,
):
    sample_rate = 16_000
    seconds = 4
    timeline = np.arange(
        sample_rate * seconds,
        dtype=np.float32,
    ) / sample_rate
    samples = (
        np.sin(2.0 * np.pi * 220.0 * timeline)
        * 0.15
        * np.iinfo(np.int16).max
    ).astype(np.int16)

    path = tmp_path / "voice.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(samples.tobytes())

    analysis = AvatarMediaAnalysisService().analyze(
        storage_path=str(path),
        asset_type="audio",
        content_type="audio/wav",
    )

    assert analysis.has_voice is True
    assert analysis.voice_usable is True
    assert analysis.quality_score >= 0.72
    assert analysis.analysis_metadata is not None
    assert (
        analysis.analysis_metadata[
            "speaker_identity_verified"
        ]
        is False
    )


def test_silent_audio_is_rejected(
    tmp_path: Path,
):
    sample_rate = 16_000
    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(sample_rate)
        output.writeframes(
            np.zeros(
                sample_rate * 4,
                dtype=np.int16,
            ).tobytes()
        )

    with pytest.raises(
        AvatarMediaAnalysisError,
        match="clear recording",
    ):
        AvatarMediaAnalysisService().analyze(
            storage_path=str(path),
            asset_type="audio",
            content_type="audio/wav",
        )


def test_visual_analysis_recovers_portrait_video_rotation():
    class PortraitOnlyFaceDetector:
        def detectMultiScale(
            self,
            image: np.ndarray,
            **_: object,
        ) -> np.ndarray:
            height, width = image.shape[:2]
            if height <= width:
                return np.empty((0, 4), dtype=np.int32)
            return np.array(
                [[width // 4, height // 4, 96, 112]],
                dtype=np.int32,
            )

    service = AvatarMediaAnalysisService.__new__(
        AvatarMediaAnalysisService
    )
    service._face_detector = PortraitOnlyFaceDetector()

    horizontal_gradient = np.tile(
        np.linspace(60, 200, 640, dtype=np.uint8),
        (360, 1),
    )
    landscape_encoded_portrait = cv2.merge(
        [horizontal_gradient] * 3
    )

    analysis = service._analyze_visual_frame(
        landscape_encoded_portrait
    )

    assert analysis.has_face is True
    assert analysis.multiple_faces is False


def test_visual_lighting_is_measured_on_face_not_dark_background():
    class CenterFaceDetector:
        def detectMultiScale(
            self,
            image: np.ndarray,
            **_: object,
        ) -> np.ndarray:
            return np.array(
                [[120, 90, 160, 180]],
                dtype=np.int32,
            )

    service = AvatarMediaAnalysisService.__new__(
        AvatarMediaAnalysisService
    )
    service._face_detector = CenterFaceDetector()

    image = np.full((360, 480, 3), 8, dtype=np.uint8)
    face_gradient = np.tile(
        np.linspace(70, 190, 160, dtype=np.uint8),
        (180, 1),
    )
    image[90:270, 120:280] = cv2.merge(
        [face_gradient] * 3
    )

    analysis = service._analyze_visual_frame(image)

    assert analysis.has_face is True
    assert analysis.has_clear_lighting is True


def _frame_analysis(
    *,
    has_face: bool,
    has_frontal_face: bool | None = None,
    has_clear_lighting: bool = True,
    multiple_faces: bool = False,
) -> _VisualFrameAnalysis:
    return _VisualFrameAnalysis(
        quality_score=0.8 if has_face else 0.2,
        has_face=has_face,
        has_frontal_face=(
            has_face
            if has_frontal_face is None
            else has_frontal_face
        ),
        has_clear_lighting=has_clear_lighting,
        multiple_faces=multiple_faces,
    )


def test_video_summary_tolerates_detector_misses_and_one_false_multiple_face():
    analyses = [
        _frame_analysis(has_face=True)
        for _ in range(6)
    ]
    analyses.extend(
        _frame_analysis(has_face=False)
        for _ in range(5)
    )
    analyses.append(
        _frame_analysis(
            has_face=True,
            multiple_faces=True,
        )
    )

    summary = AvatarMediaAnalysisService._summarize_video_frames(
        analyses
    )

    assert summary.face_frame_count == 7
    assert summary.multiple_face_frame_count == 1
    assert summary.motion_usable is True


def test_video_summary_rejects_repeated_multiple_people():
    analyses = [
        _frame_analysis(has_face=True)
        for _ in range(9)
    ]
    analyses.extend(
        _frame_analysis(
            has_face=True,
            multiple_faces=True,
        )
        for _ in range(3)
    )

    summary = AvatarMediaAnalysisService._summarize_video_frames(
        analyses
    )

    assert summary.repeatedly_shows_multiple_people is True
    assert summary.motion_usable is False


def test_video_summary_keeps_uneven_lighting_as_quality_signal():
    analyses = [
        _frame_analysis(
            has_face=True,
            has_clear_lighting=False,
        )
        for _ in range(12)
    ]

    summary = AvatarMediaAnalysisService._summarize_video_frames(
        analyses
    )

    assert summary.clear_lighting_ratio == 0.0
    assert summary.motion_usable is True


def test_face_detector_results_are_deduplicated_across_cascades():
    merged = AvatarMediaAnalysisService._merge_overlapping_faces(
        [
            (100, 80, 180, 200),
            (108, 86, 172, 194),
            (360, 90, 150, 180),
        ],
        frame_width=640,
        frame_height=480,
    )

    assert len(merged) == 2
