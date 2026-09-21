from types import SimpleNamespace

import numpy as np
import pytest

from app.services import avatar_media_analysis_service as module
from app.services.avatar_media_analysis_service import (
    AvatarMediaAnalysisService, AvatarMediaAnalysisError, AvatarMediaAnalysisUnavailableError,
    _VisualFrameAnalysis,
)


def frames(offset):
    return [SimpleNamespace(time=offset + index / 30,
        to_ndarray=lambda format, index=index: np.full((2, 2, 3), index, dtype=np.int32))
        for index in range(120)]


@pytest.mark.parametrize('offset', [10.0, -10.0, 0.0])
def test_video_sampling_is_independent_of_stream_timestamp_origin(offset):
    service = AvatarMediaAnalysisService()
    container = SimpleNamespace(decode=lambda stream: iter(frames(offset)))
    sampled = list(service._sample_video_frames(container, object(), 4.0))
    assert [int(frame[0, 0, 0]) for frame in sampled] == list(range(0, 120, 10))


def test_missing_timestamps_are_analysis_unavailable_not_insufficient_frames():
    service = AvatarMediaAnalysisService()
    frame = SimpleNamespace(time=None, to_ndarray=lambda format: np.zeros((2, 2, 3)))
    container = SimpleNamespace(decode=lambda stream: iter([frame] * 120))
    with pytest.raises(AvatarMediaAnalysisUnavailableError):
        list(service._sample_video_frames(container, object(), 4.0))


@pytest.mark.parametrize('asset_type', ['image', 'video'])
def test_missing_stored_upload_is_not_rejected_as_bad_material(tmp_path, asset_type):
    with pytest.raises(AvatarMediaAnalysisUnavailableError):
        AvatarMediaAnalysisService().analyze(storage_path=str(tmp_path / 'missing'),
            asset_type=asset_type, content_type='application/octet-stream')


@pytest.mark.parametrize('method', ['_analyze_video', '_analyze_memory_video'])
@pytest.mark.parametrize('failure', [PermissionError, FileNotFoundError, MemoryError])
def test_video_infrastructure_failure_is_not_decode_rejection(monkeypatch, tmp_path, method, failure):
    def unavailable(*args, **kwargs):
        raise failure('internal path must not be exposed')
    monkeypatch.setattr(module.av, 'open', unavailable)
    with pytest.raises(AvatarMediaAnalysisUnavailableError) as error:
        getattr(AvatarMediaAnalysisService(), method)(tmp_path / 'test.mp4')
    assert 'internal path' not in str(error.value)


def test_video_without_video_track_remains_material_rejection(monkeypatch, tmp_path):
    class Container:
        streams = []
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
    monkeypatch.setattr(module.av, 'open', lambda path: Container())
    with pytest.raises(AvatarMediaAnalysisError, match='no video track') as error:
        AvatarMediaAnalysisService()._analyze_video(tmp_path / 'test.mp4')
    assert not isinstance(error.value, AvatarMediaAnalysisUnavailableError)


@pytest.mark.parametrize('offset', [10.0, -10.0])
def test_full_video_analysis_samples_sustained_face_after_brief_intro(monkeypatch, tmp_path, offset):
    class Container:
        streams = [SimpleNamespace(type='video', duration=60, time_base=1, average_rate=30,
            codec_context=SimpleNamespace(width=1920, height=1080, name='h264')),
            SimpleNamespace(type='audio', codec_context=SimpleNamespace(name='aac'))]
        format = SimpleNamespace(name='mov,mp4,m4a,3gp,3g2,mj2')
        metadata = {'major_brand': 'isom'}
        duration = 60 * module.av.time_base
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def decode(self, stream):
            return iter([SimpleNamespace(time=offset + index / 30,
                to_ndarray=lambda format, index=index: np.full((2, 2, 3), index, dtype=np.int32))
                for index in range(1800)])
    monkeypatch.setattr(module.av, 'open', lambda path: Container())
    service = AvatarMediaAnalysisService()
    def synthetic_detection(frame):
        face = int(frame[0, 0, 0]) >= 225
        return _VisualFrameAnalysis(0.8 if face else 0.2, face, face, True, False)
    monkeypatch.setattr(service, '_analyze_visual_frame', synthetic_detection)
    result = service._analyze_video(tmp_path / 'synthetic.mp4')
    assert result.motion_usable
    assert result.analysis_metadata['sampled_frames'] == 12
    assert result.analysis_metadata['face_frame_ratio'] == 0.833
