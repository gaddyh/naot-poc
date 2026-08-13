"""Evaluation runner: drive a target over a dataset and collect runs.

For each case:

1. resolve ``inputs["image"]`` against ``root``;
2. time the target invocation with ``perf_counter``;
3. on error, capture the exception message and continue (an eval run records
   failures, it does not abort the experiment);
4. score the outputs with :func:`barcode_accuracy`;
5. also compute the raw ``matched`` / ``extra`` tuples as diagnostics for the
   local per-image report (kept out of the scores dict);
6. build an :class:`EvaluationRun` and aggregate at the end.
"""

from collections import Counter
from pathlib import Path
from time import perf_counter
from typing import Any

from naot_poc.domain.errors import NaotPocError
from naot_poc.evaluation.datasets.models import EvaluationCase
from naot_poc.evaluation.evaluators.barcode_accuracy import barcode_accuracy
from naot_poc.evaluation.regression.aggregate import (
    AggregateMetrics,
    EvaluationRun,
    aggregate,
)
from naot_poc.evaluation.targets.ingest_image import Target, run_ingest_image


def _resolve_inputs(case: EvaluationCase, root: Path) -> dict[str, Any]:
    """Return a copy of ``case.inputs`` with ``image`` resolved under ``root``."""
    inputs = dict(case.inputs)
    image = inputs.get("image")
    if image is not None:
        inputs["image"] = str((root / image).resolve()) if not Path(image).is_absolute() else image
    return inputs


async def run_evaluation(
    cases: list[EvaluationCase],
    root: Path,
    target: Target = run_ingest_image,
) -> tuple[list[EvaluationRun], AggregateMetrics]:
    """Run ``target`` over ``cases`` and return per-run records + aggregate."""
    runs: list[EvaluationRun] = []

    for case in cases:
        inputs = _resolve_inputs(case, root)
        outputs: dict[str, Any] = {}
        error: str | None = None

        start = perf_counter()
        try:
            outputs = await target(inputs)
        except NaotPocError as exc:
            error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:  # noqa: BLE001 - eval must not abort on target errors
            error = f"{type(exc).__name__}: {exc}"
        latency_ms = (perf_counter() - start) * 1000

        scores = barcode_accuracy(
            inputs=inputs,
            outputs=outputs,
            reference_outputs=case.reference_outputs,
        )

        expected = Counter(case.reference_outputs.get("barcodes", []))
        found = Counter(outputs.get("barcodes", []))
        matched_counts = expected & found
        extra_counts = found - expected
        matched = tuple(
            value
            for value, count in sorted(matched_counts.items())
            for _ in range(count)
        )
        extra = tuple(
            value
            for value, count in sorted(extra_counts.items())
            for _ in range(count)
        )

        runs.append(
            EvaluationRun(
                case=case,
                inputs=inputs,
                outputs=outputs,
                scores=scores,
                latency_ms=latency_ms,
                error=error,
                matched=matched,
                extra=extra,
            )
        )

    return runs, aggregate(runs)


def print_report(runs: list[EvaluationRun], metrics: AggregateMetrics) -> None:
    """Print a per-image table followed by aggregate metrics."""
    header = (
        f"{'image':<48} {'exp':>3} {'found':>5} {'match':>5} "
        f"{'fp':>3} {'recall':>6} {'comp':>4} {'lat(ms)':>8}"
    )
    print(header)
    print("-" * len(header))

    for run in runs:
        image = Path(run.inputs.get("image", "?")).name
        s = run.scores
        comp = "OK" if s.get("complete") else "no"
        if run.error:
            comp = "ERR"
        print(
            f"{image:<48.48} "
            f"{s.get('expected_count', 0):>3} "
            f"{s.get('found_count', 0):>5} "
            f"{s.get('matched_count', 0):>5} "
            f"{s.get('false_positives', 0):>3} "
            f"{s.get('barcode_recall', 0.0):>6.2f} "
            f"{comp:>4} "
            f"{run.latency_ms:>8.1f}"
        )
        if run.error:
            print(f"    ! {run.error}")

    print()
    print(f"cases:                  {metrics.case_count}")
    print(f"overall_barcode_recall: {metrics.overall_barcode_recall:.2%}")
    print(f"complete_image_rate:    {metrics.complete_image_rate:.2%}")
    print(f"total_false_positives:  {metrics.total_false_positives}")
    print(
        f"matched / expected:     {metrics.total_matched} / {metrics.total_expected}"
    )
    print(f"p50 latency:            {metrics.p50_latency_ms:.1f} ms")
    print(f"p95 latency:            {metrics.p95_latency_ms:.1f} ms")
