from __future__ import annotations

import wave
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import cv2
import numpy as np
import pytest

from app.services.avatar_media_analysis_service import (
    AvatarMediaAnalysisError,
    AvatarMediaAnalysisUnavailableError,
    AvatarMediaAnalysisService,
    _VisualFrameAnalysis,
)


def test_small_portrait_is_rejected_before_face_inference(tmp_path: Path, monkeypatch):
    path = tmp_path / "small.jpg"
    assert cv2.imwrite(str(path), np.full((295, 316, 3), 180, dtype=np.uint8))
    service = AvatarMediaAnalysisService()
    detector = Mock(side_effect=AssertionError("Small source must fail before inference"))
    monkeypatch.setattr(service, "_analyze_visual_frame", detector)
    with pytest.raises(AvatarMediaAnalysisError, match="316 x 295.*512 x 512"):
        service.analyze(storage_path=str(path), asset_type="image", content_type="image/jpeg")
    detector.assert_not_called()
    gallery = service.analyze(storage_path=str(path), asset_type="memory_image", content_type="image/jpeg")
    assert gallery.recommended_for_avatar is False


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
    service._detect_faces = PortraitOnlyFaceDetector().detectMultiScale

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
    service._detect_faces = CenterFaceDetector().detectMultiScale

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


def _portrait() -> np.ndarray:
    image = cv2.imread(str(Path(__file__).parent / "fixtures/astronaut.png"))
    assert image is not None
    return image


@pytest.mark.parametrize("rotation", [None, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_180])
def test_yunet_single_person_is_not_counted_twice(rotation):
    image = _portrait()
    if rotation is not None:
        image = cv2.rotate(image, rotation)
    analysis = AvatarMediaAnalysisService()._analyze_visual_frame(image)
    assert analysis.has_face
    assert not analysis.multiple_faces


def test_yunet_close_portrait_and_high_resolution_are_accepted():
    image = _portrait()[45:200, 170:285]
    image = cv2.resize(image, (1150, 1550))
    analysis = AvatarMediaAnalysisService()._analyze_visual_frame(image)
    assert analysis.has_face
    assert not analysis.multiple_faces


def test_yunet_still_detects_two_people():
    image = _portrait()
    analysis = AvatarMediaAnalysisService()._analyze_visual_frame(np.hstack([image, image]))
    assert analysis.has_face
    assert analysis.multiple_faces


def test_missing_model_is_service_failure_not_bad_photo(tmp_path):
    service = AvatarMediaAnalysisService()
    service.FACE_MODEL_PATH = tmp_path / "missing.onnx"
    with pytest.raises(AvatarMediaAnalysisUnavailableError):
        service._analyze_visual_frame(_portrait())


def test_tampered_model_is_not_loaded(tmp_path):
    service = AvatarMediaAnalysisService()
    service.FACE_MODEL_PATH = tmp_path / "model.onnx"
    service.FACE_MODEL_PATH.write_bytes(b"invalid model")
    with pytest.raises(AvatarMediaAnalysisUnavailableError):
        service._analyze_visual_frame(_portrait())


@pytest.mark.parametrize("unavailable,expected_status", [(True, 503), (False, 422)])
@pytest.mark.parametrize("was_existing", [True, False])
def test_upload_distinguishes_analysis_outage_from_invalid_source(monkeypatch, unavailable, expected_status, was_existing):
    from fastapi import HTTPException
    from app.routes import avatar_media

    response = SimpleNamespace(asset_id="asset", was_existing=was_existing, content_type="image/png")
    storage = Mock()
    storage.upload = AsyncMock(return_value=response)
    storage.get_metadata.return_value = SimpleNamespace(storage_path="/unused")
    analyzer = Mock()
    error_type = AvatarMediaAnalysisUnavailableError if unavailable else AvatarMediaAnalysisError
    analyzer.analyze.side_effect = error_type("Photo check unavailable" if unavailable else "No clear face")
    monkeypatch.setattr(avatar_media, "AvatarMediaStorageService", lambda: storage)
    monkeypatch.setattr(avatar_media, "AvatarMediaAnalysisService", lambda: analyzer)
    monkeypatch.setattr(avatar_media, "AvatarMediaEvidenceBridgeService", Mock())
    monkeypatch.setattr(avatar_media, "require_profile_access", Mock())
    with pytest.raises(HTTPException) as raised:
        asyncio.run(avatar_media.upload_avatar_media(
            request=SimpleNamespace(base_url="https://stay.test"),
            profile_id=str(uuid4()), asset_type="image", title="Photo",
            upload_id=None, file=Mock(), principal=Mock(),
        ))
    assert raised.value.status_code == expected_status
    assert storage.delete_asset.call_count == (0 if was_existing else 1)
