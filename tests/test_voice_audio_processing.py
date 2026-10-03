import io
import wave
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.services.voice_audio_processing import process_audio
from app.services.self_hosted_voice_provider import SelfHostedVoiceProvider
from app.schemas.voice_delivery import VoiceDelivery
from test_self_hosted_voice_runtime import audio_bytes


@pytest.mark.parametrize('noise,speed', [(True, 1.0), (False, .9), (False, 1.1), (True, 1.1)])
def test_filter_output_is_valid_pcm_with_bounded_duration(noise, speed):
    result = process_audio(audio_bytes(), remove_noise=noise, speed=speed)
    with wave.open(io.BytesIO(result), 'rb') as audio:
        assert audio.getnchannels() == 1
        assert audio.getsampwidth() == 2
        assert audio.getframerate() == 16000
        assert 2.5 < audio.getnframes() / 16000 < 3.6


def test_neutral_delivery_does_not_reencode():
    sample = audio_bytes()
    assert process_audio(sample) is sample


def test_long_speech_is_bounded_and_delivery_reaches_each_segment():
    references = SimpleNamespace(get=Mock(return_value=(audio_bytes(), 'a' * 64, 1)))
    client = SimpleNamespace(synthesize=AsyncMock(return_value=audio_bytes()))
    provider = SelfHostedVoiceProvider(object(), client=client, references=references)
    output = asyncio.run(provider.synthesize(profile_id=uuid4(), job_id=uuid4(),
        text='This is a longer spoken answer. ' * 30, language='en',
        delivery=VoiceDelivery(energy=.8, speaking_speed=.8, pause_length=.4)))
    assert client.synthesize.await_count > 1
    for call in client.synthesize.await_args_list:
        assert len(call.args[0].text) <= 240
        assert call.args[0].energy == .8
    with wave.open(output, 'rb') as audio:
        assert audio.getnframes() > 16000 * 3
    assert references.get.call_count == client.synthesize.await_count + 2
