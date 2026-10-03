"""Real CUDA benchmark. Never certifies listening quality or device-call latency."""

import argparse
import io
import json
import math
import os
from pathlib import Path
import statistics
import time
import wave

from app.schemas.self_hosted_voice import SelfHostedVoiceRequest
from voice_runtime.engine import ChatterboxEngine


def summarize(measurements, max_p95_seconds):
    if not measurements or not math.isfinite(max_p95_seconds) or max_p95_seconds <= 0:
        raise ValueError('A positive acceptance threshold and measurements are required')
    seconds = sorted(item['inference_seconds'] for item in measurements)
    p95 = seconds[math.ceil(len(seconds) * .95) - 1]
    return {'samples': len(seconds), 'p50_seconds': statistics.median(seconds),
        'p95_seconds': p95, 'max_p95_seconds': max_p95_seconds,
        'latency_gate': 'passed' if p95 <= max_p95_seconds else 'failed',
        'listening_review': 'required', 'end_to_end_device_review': 'required',
        'measurement_scope': 'buffered inference; excludes network, queue and device playback'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, required=True,
                        help='Private JSON array of authorized SelfHostedVoiceRequest payloads')
    parser.add_argument('--output', type=Path, required=True, help='New private result directory')
    parser.add_argument('--max-p95-seconds', type=float, required=True)
    parser.add_argument('--iterations', type=int, default=10)
    args = parser.parse_args()
    if not 10 <= args.iterations <= 100:
        parser.error('Use 10 to 100 iterations per case')
    summarize([{'inference_seconds': 0}], args.max_p95_seconds)
    try:
        raw = args.cases.read_bytes()
        if len(raw) > 15_000_000:
            raise ValueError('Oversized cases')
        cases = [SelfHostedVoiceRequest.model_validate_json(json.dumps(item)) for item in json.loads(raw)]
        if not 2 <= len(cases) <= 10 or {case.language for case in cases} != {'de', 'en'}:
            raise ValueError('German and English cases are required')
        for case in cases:
            case.reference_audio()
        args.output.mkdir(mode=0o700, parents=False, exist_ok=False)
        started = time.perf_counter()
        engine = ChatterboxEngine(os.environ['STAY_VOICE_MODEL_DIRECTORY'],
                                 os.environ['STAY_VOICE_MODEL_REVISION'])
        startup_seconds = time.perf_counter() - started
        measurements = []
        for iteration in range(args.iterations):
            for index, case in enumerate(cases):
                started = time.perf_counter()
                audio = engine.synthesize(case)
                elapsed = time.perf_counter() - started
                with wave.open(io.BytesIO(audio), 'rb') as wav:
                    duration = wav.getnframes() / wav.getframerate()
                filename = f'{iteration:03d}-{index:02d}.wav'
                with (args.output / filename).open('xb') as output:
                    output.write(audio)
                measurements.append({'iteration': iteration, 'case': index, 'language': case.language,
                    'inference_seconds': elapsed, 'audio_seconds': duration,
                    'real_time_factor': elapsed / duration, 'audio_file': filename})
        report = {**summarize(measurements, args.max_p95_seconds), 'startup_seconds': startup_seconds,
                  'model_revision': engine.revision, 'measurements': measurements}
        with (args.output / 'report.json').open('x') as output:
            json.dump(report, output, indent=2)
        print(json.dumps({key: report[key] for key in ('samples', 'p50_seconds', 'p95_seconds', 'latency_gate')}))
        return 0 if report['latency_gate'] == 'passed' else 1
    except Exception:
        parser.exit(1, 'GPU benchmark did not complete. Check runtime configuration and private input files.\n')


if __name__ == '__main__':
    raise SystemExit(main())
