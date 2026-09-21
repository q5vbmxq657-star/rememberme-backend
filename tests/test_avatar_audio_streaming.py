from types import SimpleNamespace
import wave
import weakref

import numpy as np
import pytest

from app.services import avatar_media_analysis_service as module


@pytest.mark.parametrize('channels', [1, 2])
@pytest.mark.parametrize('seconds', [2, 4])
def test_real_wav_duration_is_per_channel(tmp_path, channels, seconds):
    path = tmp_path / 'recording.wav'
    values = (np.sin(np.arange(16000 * seconds) * 0.1) * 8000).astype('<i2')
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(channels)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(np.repeat(values[:, None], channels, axis=1).tobytes())
    service = module.AvatarMediaAnalysisService()
    if seconds < 3:
        with pytest.raises(module.AvatarMediaAnalysisError, match='three seconds'):
            service.analyze(storage_path=str(path), asset_type='voice', content_type='audio/wav')
    else:
        result = service.analyze(storage_path=str(path), asset_type='voice', content_type='audio/wav')
        assert result.analysis_metadata['duration_seconds'] == seconds
        assert result.analysis_metadata['channel_counts'] == [channels]


def install_container(monkeypatch, frames):
    class Container:
        streams = [SimpleNamespace(type='audio')]
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def decode(self, stream): return frames()
    monkeypatch.setattr(module.av, 'open', lambda path: Container())


@pytest.mark.parametrize('planar', [True, False])
def test_streaming_rate_changes_and_bounded_array_retention(monkeypatch, tmp_path, planar):
    def frames():
        for rate in [8000, 16000, 24000, 48000]:
            values = np.full((2, rate) if planar else (1, rate * 2), 0.1, dtype=np.float32)
            reference = weakref.ref(values)
            frame = SimpleNamespace(sample_rate=rate, samples=rate,
                layout=SimpleNamespace(channels=[0, 1]), format=SimpleNamespace(is_planar=planar),
                to_ndarray=lambda: values)
            yield frame
            del values, frame
            assert reference() is None
    install_container(monkeypatch, frames)
    result = module.AvatarMediaAnalysisService()._analyze_audio(tmp_path / 'test')
    assert result.analysis_metadata['duration_seconds'] == 4
    assert result.analysis_metadata['sample_rate'] is None
    assert result.analysis_metadata['sample_rates'] == [8000, 16000, 24000, 48000]
    assert result.analysis_metadata['rms'] == 0.1


@pytest.mark.parametrize('error', [RuntimeError('decoder offline'), PermissionError(), MemoryError(), FileNotFoundError()])
def test_runtime_failures_are_unavailable(monkeypatch, tmp_path, error):
    def fail(path): raise error
    monkeypatch.setattr(module.av, 'open', fail)
    with pytest.raises(module.AvatarMediaAnalysisUnavailableError):
        module.AvatarMediaAnalysisService()._analyze_audio(tmp_path / 'test')


def test_corrupt_media_remains_material_error(monkeypatch, tmp_path):
    def fail(path): raise module.av.error.InvalidDataError(1094995529, 'invalid data')
    monkeypatch.setattr(module.av, 'open', fail)
    with pytest.raises(module.AvatarMediaAnalysisError) as caught:
        module.AvatarMediaAnalysisService()._analyze_audio(tmp_path / 'test')
    assert not isinstance(caught.value, module.AvatarMediaAnalysisUnavailableError)


def test_analysis_window_is_seconds_not_channels(monkeypatch, tmp_path):
    consumed = []
    def frames():
        for index in range(130):
            consumed.append(index)
            yield SimpleNamespace(sample_rate=100, samples=100,
                layout=SimpleNamespace(channels=[0, 1]), format=SimpleNamespace(is_planar=True),
                to_ndarray=lambda: np.full((2, 100), 0.1, dtype=np.float32))
    install_container(monkeypatch, frames)
    result = module.AvatarMediaAnalysisService()._analyze_audio(tmp_path / 'test')
    assert len(consumed) == 120
    assert result.analysis_metadata['duration_seconds'] == 120


@pytest.mark.parametrize('value', [0.0, 1.0, float('nan')])
def test_invalid_signal_remains_material_error(monkeypatch, tmp_path, value):
    def frames():
        yield SimpleNamespace(sample_rate=100, samples=400,
            layout=SimpleNamespace(channels=[0, 1]), format=SimpleNamespace(is_planar=False),
            to_ndarray=lambda: np.full((1, 800), value, dtype=np.float32))
    install_container(monkeypatch, frames)
    with pytest.raises(module.AvatarMediaAnalysisError) as caught:
        module.AvatarMediaAnalysisService()._analyze_audio(tmp_path / 'test')
    assert not isinstance(caught.value, module.AvatarMediaAnalysisUnavailableError)


def test_empty_audio_remains_material_error(monkeypatch, tmp_path):
    install_container(monkeypatch, lambda: iter(()))
    with pytest.raises(module.AvatarMediaAnalysisError, match='no readable audio') as caught:
        module.AvatarMediaAnalysisService()._analyze_audio(tmp_path / 'test')
    assert not isinstance(caught.value, module.AvatarMediaAnalysisUnavailableError)
