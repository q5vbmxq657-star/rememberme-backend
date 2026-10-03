"""Inference adapter; training selection and activation stay in the existing gateway."""

import asyncio
import base64
import io
import os
import textwrap
import wave
from uuid import UUID, uuid4

import av

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest
from app.security.purpose_authorization import require_profile_purposes
from app.services.self_hosted_voice_client import SelfHostedVoiceClient
from app.services.voice_reference_repository import VoiceReferenceRepository
from app.services.voice_audio_processing import process_audio


class SelfHostedVoiceProvider:
    def __init__(self, repository, *, client=None, references=None):
        self.repository = repository
        self.client = client or SelfHostedVoiceClient(os.environ.get('STAY_VOICE_RUNTIME_URL', ''),
                                                     os.environ.get('STAY_VOICE_RUNTIME_TOKEN', ''))
        self.references = references or VoiceReferenceRepository(repository)
        self.model_revision = os.environ.get('STAY_VOICE_MODEL_REVISION', '')

    @staticmethod
    def prepare_reference(samples):
        pcm = bytearray()
        budget = 16000 * 30 // len(samples)
        for sample in samples:
            selected = bytearray()
            with av.open(io.BytesIO(sample.data)) as container:
                stream = next(s for s in container.streams if s.type == 'audio')
                resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
                for frame in container.decode(stream):
                    for converted in resampler.resample(frame):
                        selected.extend(converted.to_ndarray().astype('<i2', copy=False).tobytes())
                    if len(selected) >= budget * 2:
                        break
                else:
                    for converted in resampler.resample(None):
                        selected.extend(converted.to_ndarray().astype('<i2', copy=False).tobytes())
            pcm.extend(selected[:budget * 2])
        output = io.BytesIO()
        with wave.open(output, 'wb') as result:
            result.setnchannels(1)
            result.setsampwidth(2)
            result.setframerate(16000)
            result.writeframes(pcm)
        return output.getvalue()

    async def prepare(self, *, profile_id, job_id, revision, samples, remove_background_noise=False):
        audio = await asyncio.to_thread(self.prepare_reference, samples)
        audio = await asyncio.to_thread(process_audio, audio, remove_noise=remove_background_noise)
        request = SelfHostedVoiceRequest(request_id=uuid4(), profile_id=profile_id,
            voice_version=job_id, consent_revision=revision,
            model_revision=self.model_revision, text='Hello. It is good to hear from you.', language='en',
            reference_wav_base64=base64.b64encode(audio).decode())
        await asyncio.to_thread(self.references.put, request)
        await asyncio.to_thread(require_profile_purposes, profile_id, {'voice_synthesis'}, expected_revision=revision)
        await self.client.synthesize(request)
        await asyncio.to_thread(require_profile_purposes, profile_id, {'voice_synthesis'}, expected_revision=revision)
        return str(job_id), False

    async def synthesize(self, *, profile_id: UUID, job_id: UUID, text: str, language: str, delivery=None):
        if not text.strip() or len(text) > 8000:
            raise ValueError('Invalid utterance')
        audio, model, revision = await asyncio.to_thread(self.references.get, profile_id=profile_id, job_id=job_id)
        segments = textwrap.wrap(text, width=240, break_on_hyphens=False)
        rate = None
        pcm = bytearray()
        for index, segment in enumerate(segments):
            await asyncio.to_thread(self.references.get, profile_id=profile_id, job_id=job_id)
            request = SelfHostedVoiceRequest(request_id=uuid4(), profile_id=profile_id,
                voice_version=job_id, consent_revision=revision, model_revision=model,
                text=segment, language=language, reference_wav_base64=base64.b64encode(audio).decode(),
                energy=delivery.energy if delivery else .5)
            result = await self.client.synthesize(request)
            speed = .9 + .2 * delivery.speaking_speed if delivery else 1.0
            result = await asyncio.to_thread(process_audio, result, speed=speed)
            with wave.open(io.BytesIO(result), 'rb') as decoded:
                if rate is not None and rate != decoded.getframerate():
                    raise ValueError('Voice output format changed')
                rate = decoded.getframerate()
                if index and delivery:
                    pcm.extend(b'\0\0' * int(rate * .2 * delivery.pause_length))
                pcm.extend(decoded.readframes(decoded.getnframes()))
            if len(pcm) > 64_000_000:
                raise ValueError('Voice output exceeded its limit')
        output = io.BytesIO()
        with wave.open(output, 'wb') as combined:
            combined.setnchannels(1)
            combined.setsampwidth(2)
            combined.setframerate(rate)
            combined.writeframes(pcm)
        # Discard audio if deletion, revocation or erasure happened during inference.
        await asyncio.to_thread(self.references.get, profile_id=profile_id, job_id=job_id)
        output.seek(0)
        return output
