"""Regression runner: drive targets over datasets and report metrics."""

from naot_poc.evaluation.regression.aggregate import (
    AggregateMetrics,
    EvaluationRun,
    aggregate,
)
from naot_poc.evaluation.regression.runner import print_report, run_evaluation

__all__ = [
    "AggregateMetrics",
    "EvaluationRun",
    "aggregate",
    "print_report",
    "run_evaluation",
]
