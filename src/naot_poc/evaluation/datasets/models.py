"""Dataset models for evaluation.

Shaped to match LangSmith's example model: ``inputs`` go into the target
function, ``reference_outputs`` are the ground truth the evaluator compares
against, and ``metadata`` carries provenance/notes. This keeps the dataset
schema stable as we add richer ground truth (box counts, positions, ...) and
makes a future LangSmith dataset import almost mechanical.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class EvaluationCase:
    """A single evaluation example.

    ``inputs`` and ``reference_outputs`` are intentionally untyped dicts so
    different targets/evaluators can define their own contracts without
    changing the dataset schema.
    """

    inputs: dict[str, Any]
    reference_outputs: dict[str, Any]
    metadata: dict[str, Any]
