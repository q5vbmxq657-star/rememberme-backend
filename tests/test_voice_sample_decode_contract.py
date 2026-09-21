import io
from pathlib import Path
import wave

import numpy as np
import pytest

from app.services.avatar_media_analysis_service import AvatarMediaAnalysisUnavailableError
from app.services.elevenlabs_voice_service import (
    ElevenLabsVoiceService, ElevenLabsVoiceValidationError, VoiceCloneSample,
)


def wav_sample(*, seconds=4, amplitude=0.2, channels=1):
    rate = 16000
    signal = amplitude * np.sin(2 * np.pi * 220 * np.arange(int(seconds * rate)) / rate)
    pcm = (np.repeat(signal[:, None], channels, axis=1) * 32767).astype("<i2")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(pcm.tobytes())
    return VoiceCloneSample("sample.wav", "audio/wav", buffer.getvalue())


def test_real_audio_decodes_through_existing_media_analyzer():
    ElevenLabsVoiceService._validate_sample_audio([wav_sample()])


@pytest.mark.parametrize("sample", [
    VoiceCloneSample("fake.wav", "audio/wav", b"not audio" * 100),
    wav_sample(amplitude=0),
    wav_sample(seconds=1),
])
def test_filename_and_mime_do_not_make_invalid_audio_usable(sample):
    with pytest.raises(ElevenLabsVoiceValidationError):
        ElevenLabsVoiceService._validate_sample_audio([sample])


def test_temporary_validation_copy_is_removed_after_infrastructure_failure(monkeypatch):
    paths = []

    def unavailable(self, *, storage_path, **kwargs):
        path = Path(storage_path)
        assert path.is_file()
        paths.append(path)
        raise AvatarMediaAnalysisUnavailableError("Temporary analysis failure")

    monkeypatch.setattr("app.services.elevenlabs_voice_service.AvatarMediaAnalysisService.analyze", unavailable)
    with pytest.raises(AvatarMediaAnalysisUnavailableError):
        ElevenLabsVoiceService._validate_sample_audio([wav_sample()])
    assert paths and all(not path.exists() for path in paths)
