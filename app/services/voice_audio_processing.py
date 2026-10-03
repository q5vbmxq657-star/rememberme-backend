"""Bounded PCM processing using the backend's existing FFmpeg/PyAV dependency."""

import io
import wave

import av


def process_audio(audio: bytes, *, remove_noise=False, speed=1.0) -> bytes:
    if not .85 <= speed <= 1.15:
        raise ValueError('Unsupported speech speed')
    if not remove_noise and speed == 1.0:
        return audio
    with wave.open(io.BytesIO(audio), 'rb') as source:
        rate = source.getframerate()
    graph = None
    converted = bytearray()
    resampler = av.AudioResampler(format='s16', layout='mono', rate=rate)

    def drain():
        while True:
            try:
                frame = graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                return
            for pcm in resampler.resample(frame):
                converted.extend(pcm.to_ndarray().astype('<i2', copy=False).tobytes())

    with av.open(io.BytesIO(audio)) as source:
        for frame in source.decode(audio=0):
            if graph is None:
                graph = av.filter.Graph()
                nodes = [graph.add_abuffer(sample_rate=frame.sample_rate, format=frame.format.name,
                    layout=frame.layout.name, time_base=frame.time_base)]
                if remove_noise:
                    nodes.append(graph.add('afftdn', 'nr=6:nf=-50'))
                if speed != 1.0:
                    nodes.append(graph.add('atempo', str(speed)))
                nodes.append(graph.add('abuffersink'))
                graph.link_nodes(*nodes)
                graph.configure()
            graph.push(frame)
            drain()
    if graph is None:
        raise ValueError('Empty audio')
    graph.push(None)
    drain()
    for pcm in resampler.resample(None):
        converted.extend(pcm.to_ndarray().astype('<i2', copy=False).tobytes())
    output = io.BytesIO()
    with wave.open(output, 'wb') as target:
        target.setnchannels(1)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(converted)
    return output.getvalue()
