"""Per-run records and experiment-level aggregate metrics.

Keeps a clear boundary between:

* **per-example scores** (correctness, from evaluators) — carried on
  ``EvaluationRun.scores``;
* **per-example execution data** (latency, error) — also on ``EvaluationRun``,
  but outside the scores dict, mirroring LangSmith's run-properties split;
* **per-example diagnostics** (``matched`` / ``extra`` tuples) — on
  ``EvaluationRun`` for the local report, kept out of the LangSmith-style
  scores dict;
* **experiment-level summary metrics** — computed by :func:`aggregate`.
"""

from dataclasses import dataclass, field
from math import ceil
from typing import Any

from naot_poc.evaluation.datasets.models import EvaluationCase
from naot_poc.evaluation.evaluators.barcode_accuracy import Scores


@dataclass(frozen=True)
class EvaluationRun:
    """One target invocation + its scores + execution data + diagnostics."""

    case: EvaluationCase
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    scores: Scores
    latency_ms: float
    error: str | None = None
    matched: tuple[str, ...] = field(default_factory=tuple)
    extra: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AggregateMetrics:
    case_count: int
    overall_barcode_recall: float
    complete_image_rate: float
    total_false_positives: int
    total_matched: int
    total_expected: int
    p50_latency_ms: float
    p95_latency_ms: float
    audit_failure_rate: float = 0.0
    targeted_recovery_success_rate: float = 0.0


def _percentile(sorted_values: list[float], percentile: float) -> float:
    """Nearest-rank percentile. ``percentile`` in [0, 100]."""
    if not sorted_values:
        return 0.0
    rank = max(1, ceil(percentile / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


def aggregate(runs: list[EvaluationRun]) -> AggregateMetrics:
    """Compute experiment-level summary metrics across ``runs``."""
    case_count = len(runs)
    total_expected = sum(run.scores.get("expected_count", 0) for run in runs)
    total_matched = sum(run.scores.get("matched_count", 0) for run in runs)
    total_false_positives = sum(
        run.scores.get("false_positives", 0) for run in runs
    )
    complete_count = sum(1 for run in runs if run.scores.get("complete"))

    overall_recall = (
        1.0 if total_expected == 0 else total_matched / total_expected
    )
    complete_image_rate = 1.0 if case_count == 0 else complete_count / case_count

    latencies = sorted(run.latency_ms for run in runs)
    audited = [run for run in runs if "audit_failed" in run.outputs]
    audit_failures = sum(bool(run.outputs.get("audit_failed")) for run in audited)
    recovery_attempts = sum(
        int(run.outputs.get("targeted_recovery_attempts", 0)) for run in runs
    )
    recovery_successes = sum(
        int(run.outputs.get("targeted_recovery_successes", 0)) for run in runs
    )

    return AggregateMetrics(
        case_count=case_count,
        overall_barcode_recall=overall_recall,
        complete_image_rate=complete_image_rate,
        total_false_positives=total_false_positives,
        total_matched=total_matched,
        total_expected=total_expected,
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        audit_failure_rate=(audit_failures / len(audited) if audited else 0.0),
        targeted_recovery_success_rate=(
            recovery_successes / recovery_attempts if recovery_attempts else 0.0
        ),
    )
