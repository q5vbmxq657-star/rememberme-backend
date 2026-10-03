import pytest

from voice_runtime.benchmark import summarize


def test_p95_uses_tail_and_does_not_certify_quality():
    report = summarize([{'inference_seconds': float(n)} for n in range(1, 21)], 18)
    assert report['p95_seconds'] == 19
    assert report['p50_seconds'] == 10.5
    assert report['latency_gate'] == 'failed'
    assert report['listening_review'] == 'required'
    assert report['end_to_end_device_review'] == 'required'


@pytest.mark.parametrize('threshold', [0, -1, float('nan'), float('inf')])
def test_invalid_acceptance_threshold_is_rejected(threshold):
    with pytest.raises(ValueError):
        summarize([{'inference_seconds': 1}], threshold)
