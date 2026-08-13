from naot_poc.evaluation.datasets import EvaluationCase
from naot_poc.evaluation.regression import EvaluationRun, aggregate


def _run(expected, found, latency_ms, error=None):
    expected_set = set(expected)
    found_set = set(found)
    matched = expected_set & found_set
    extra = found_set - expected_set
    scores = {
        "barcode_recall": 1.0 if not expected_set else len(matched) / len(expected_set),
        "complete": found_set == expected_set,
        "false_positives": len(extra),
        "matched_count": len(matched),
        "expected_count": len(expected_set),
        "found_count": len(found_set),
    }
    return EvaluationRun(
        case=EvaluationCase(inputs={}, reference_outputs={}, metadata={}),
        inputs={},
        outputs={"barcodes": found},
        scores=scores,
        latency_ms=latency_ms,
        error=error,
        matched=tuple(sorted(matched)),
        extra=tuple(sorted(extra)),
    )


def test_aggregate_recall_and_complete_rate():
    runs = [
        _run(["A", "B"], ["A", "B"], 10.0),  # complete
        _run(["A", "B"], ["A"], 20.0),  # partial
        _run(["A"], ["A", "X"], 30.0),  # false positive -> not complete
    ]

    metrics = aggregate(runs)

    assert metrics.case_count == 3
    # matched: 2 + 1 + 1 = 4 ; expected: 2 + 2 + 1 = 5
    assert metrics.overall_barcode_recall == 4 / 5
    assert metrics.complete_image_rate == 1 / 3
    assert metrics.total_false_positives == 1
    assert metrics.total_matched == 4
    assert metrics.total_expected == 5


def test_aggregate_percentiles_nearest_rank():
    latencies = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    runs = [_run([], [], ms) for ms in latencies]

    metrics = aggregate(runs)

    # p50: rank = ceil(0.5 * 10) = 5 -> latencies[4] = 50.0
    assert metrics.p50_latency_ms == 50.0
    # p95: rank = ceil(0.95 * 10) = 10 -> latencies[9] = 100.0
    assert metrics.p95_latency_ms == 100.0


def test_aggregate_empty_runs():
    metrics = aggregate([])

    assert metrics.case_count == 0
    assert metrics.overall_barcode_recall == 1.0
    assert metrics.complete_image_rate == 1.0
    assert metrics.p50_latency_ms == 0.0
    assert metrics.p95_latency_ms == 0.0


def test_aggregate_all_empty_expected_counts_as_complete():
    runs = [
        _run([], [], 10.0),
        _run([], [], 20.0),
    ]

    metrics = aggregate(runs)

    assert metrics.complete_image_rate == 1.0
    assert metrics.overall_barcode_recall == 1.0
    assert metrics.total_expected == 0
