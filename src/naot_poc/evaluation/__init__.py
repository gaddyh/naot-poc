"""Evaluation layer: datasets, targets, evaluators, and regression runner.

Shaped to match LangSmith's dataset -> target -> evaluator -> experiment model
so a LangSmith adapter can be added later without redesign.

- :mod:`datasets`  — ``EvaluationCase`` (inputs / reference_outputs / metadata)
  and a JSON loader.
- :mod:`targets`   — adapters that normalize a workflow into a stable output
  contract (e.g. ``{"barcodes": [...]}``); the only place that knows about
  graph state.
- :mod:`evaluators` — pure functions
  ``(inputs, outputs, reference_outputs) -> scores`` returning scalar-only
  scores dicts, directly reusable as LangSmith code evaluators.
- :mod:`regression` — ``EvaluationRun`` (scores + latency/error + diagnostics),
  ``aggregate`` for experiment-level summary metrics, and the ``runner`` /
  ``print_report`` that drive a target over a dataset.
"""

from naot_poc.evaluation.datasets import Dataset, EvaluationCase, load_dataset
from naot_poc.evaluation.evaluators import Scores, barcode_accuracy
from naot_poc.evaluation.regression import (
    AggregateMetrics,
    EvaluationRun,
    aggregate,
    print_report,
    run_evaluation,
)
from naot_poc.evaluation.targets import Target, run_ingest_image

__all__ = [
    "AggregateMetrics",
    "Dataset",
    "EvaluationCase",
    "EvaluationRun",
    "Scores",
    "Target",
    "aggregate",
    "barcode_accuracy",
    "load_dataset",
    "print_report",
    "run_evaluation",
    "run_ingest_image",
]
