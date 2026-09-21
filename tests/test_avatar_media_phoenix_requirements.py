from types import SimpleNamespace
import weakref

import numpy as np
import pytest

from app.services import avatar_media_analysis_service as module


@pytest.fixture
def video(monkeypatch):
    stream = SimpleNamespace(type='video', duration=60.0, time_base=1, average_rate=25,
        codec_context=SimpleNamespace(width=1920, height=1080, name='h264'))
    audio = SimpleNamespace(type='audio', codec_context=SimpleNamespace(name='aac'))
    class Container:
        streams = [stream, audio]
        duration = 60 * module.av.time_base
        format = SimpleNamespace(name='mov,mp4,m4a,3gp,3g2,mj2')
        metadata = {'major_brand': 'isom'}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def decode(self, stream):
            yield SimpleNamespace(width=320,height=240)
    container = Container()
    monkeypatch.setattr(module.av, 'open', lambda path: container)
    service = module.AvatarMediaAnalysisService()
    monkeypatch.setattr(service, '_sample_video_frames', lambda *args: [np.zeros((2,2,3))]*12)
    monkeypatch.setattr(service, '_analyze_visual_frame', lambda frame:
        module._VisualFrameAnalysis(0.8, True, True, True, False))
    return service, stream, audio, container


@pytest.mark.parametrize('duration', [3,45.5,59,59.95])
def test_short_avatar_video_is_rejected(video, tmp_path, duration):
    service, stream, _, _ = video
    stream.duration = duration
    with pytest.raises(module.AvatarMediaAnalysisError, match='60 seconds'):
        service._analyze_video(tmp_path/'video.mp4')


@pytest.mark.parametrize('duration', [59.96,60,90,121])
def test_one_frame_rounding_and_longer_recordings_are_allowed(video,tmp_path,duration):
    service,stream,_,_=video
    stream.duration=duration
    result=service._analyze_video(tmp_path/'video.mp4')
    assert result.analysis_metadata['duration_tolerance_seconds']==0.04
    assert result.analysis_metadata['speaking_segment_verified'] is False
    assert result.analysis_metadata['still_segment_verified'] is False
    assert result.analysis_metadata['continuous_shot_verified'] is False


@pytest.mark.parametrize('width,height', [(1280,720),(720,1280),(1080,1080)])
def test_low_resolution_is_rejected(video,tmp_path,width,height):
    service,stream,_,_=video
    stream.codec_context.width=width
    stream.codec_context.height=height
    with pytest.raises(module.AvatarMediaAnalysisError, match='1920 x 1080'):
        service._analyze_video(tmp_path/'video.mp4')


def test_portrait_1080p_is_accepted(video,tmp_path):
    service,stream,_,_=video
    stream.codec_context.width=1080
    stream.codec_context.height=1920
    assert service._analyze_video(tmp_path/'video.mp4').motion_usable


@pytest.mark.parametrize('fps',[24,23.976])
def test_low_framerate_is_rejected(video,tmp_path,fps):
    service,stream,_,_=video
    stream.average_rate=fps
    with pytest.raises(module.AvatarMediaAnalysisError,match='25 FPS'):
        service._analyze_video(tmp_path/'video.mp4')


@pytest.mark.parametrize('fps',[None,0,float('nan')])
def test_unknown_framerate_is_analysis_unavailable(video,tmp_path,fps):
    service,stream,_,_=video
    stream.average_rate=fps
    with pytest.raises(module.AvatarMediaAnalysisUnavailableError):
        service._analyze_video(tmp_path/'video.mp4')


@pytest.mark.parametrize('field,value,message', [('video','hevc','video codec'),('audio','opus','audio format'),
    ('format','avi','MP4 or WebM'),('audio_missing',None,'no audio track')])
def test_actual_stream_formats_are_checked(video,tmp_path,field,value,message):
    service,stream,audio,container=video
    if field=='video': stream.codec_context.name=value
    elif field=='audio': audio.codec_context.name=value
    elif field=='format': container.format.name=value
    else: container.streams=[stream]
    with pytest.raises(module.AvatarMediaAnalysisError,match=message):
        service._analyze_video(tmp_path/'misleading.mp4')


def test_gallery_keeps_short_low_resolution_silent_video(video,tmp_path):
    service,stream,_,container=video
    stream.duration=2
    stream.average_rate=15
    stream.codec_context.width=320
    stream.codec_context.height=240
    stream.codec_context.name='hevc'
    container.streams=[stream]
    result=service._analyze_memory_video(tmp_path/'gallery.mov')
    assert result.recommended_for_avatar is False


@pytest.mark.parametrize('brand', ['qt  ', '3gp4', '', None])
@pytest.mark.parametrize('filename', ['video.mov', 'renamed.mp4'])
def test_non_mp4_brand_requires_preparation_despite_shared_demuxer(video, tmp_path, brand, filename):
    service, _, _, container = video
    container.metadata = {} if brand is None else {'major_brand': brand}
    with pytest.raises(module.AvatarMediaAnalysisError, match='container needs preparation'):
        service._analyze_video(tmp_path / filename)


@pytest.mark.parametrize('brand', ['isom', 'mp41', 'mp42', 'iso6'])
def test_mp4_brand_not_extension_determines_acceptance(video, tmp_path, brand):
    service, _, _, container = video
    container.metadata = {'major_brand': brand}
    assert service._analyze_video(tmp_path / 'persisted-without-extension').motion_usable


def test_sample_frames_are_analyzed_and_released_before_next_sample(video, tmp_path, monkeypatch):
    service, _, _, _ = video
    analyzed = []

    def samples(*args):
        for index in range(12):
            frame = np.zeros((8, 8, 3))
            reference = weakref.ref(frame)
            yield frame
            del frame
            assert analyzed == list(range(index + 1))
            assert reference() is None, 'Full-resolution sample was retained'

    def analyze(frame):
        analyzed.append(len(analyzed))
        return module._VisualFrameAnalysis(0.8, True, True, True, False)

    monkeypatch.setattr(service, '_sample_video_frames', samples)
    monkeypatch.setattr(service, '_analyze_visual_frame', analyze)
    result = service._analyze_video(tmp_path / 'video.mp4')
    assert result.analysis_metadata['sampled_frames'] == 12


def test_streaming_analysis_preserves_transient_failure(video, tmp_path, monkeypatch):
    service, _, _, _ = video

    def unavailable(frame):
        raise module.AvatarMediaAnalysisUnavailableError('temporarily unavailable')

    monkeypatch.setattr(service, '_analyze_visual_frame', unavailable)
    with pytest.raises(module.AvatarMediaAnalysisUnavailableError):
        service._analyze_video(tmp_path / 'video.mp4')
