from __future__ import annotations

import wave
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.services.avatar_media_analysis_service import (
    AvatarMediaAnalysisError,
    AvatarMediaAnalysisService,
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
