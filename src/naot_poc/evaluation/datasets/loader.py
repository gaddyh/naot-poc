"""Dataset loader.

Reads a JSON file of the form::

    {
      "name": "...",
      "_note": "...",
      "cases": [
        {
          "inputs": {"image": "samples/foo.jpeg"},
          "reference_outputs": {"barcodes": ["..."]},
          "metadata": {"source": "...", "notes": ""}
        }
      ]
    }

Image paths inside ``inputs`` are kept as-is (relative to ``root`` when the
runner resolves them); the loader does not touch the filesystem.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from naot_poc.evaluation.datasets.models import EvaluationCase


@dataclass(frozen=True)
class Dataset:
    name: str
    cases: tuple[EvaluationCase, ...]
    metadata: dict[str, Any]
    excluded_cases: tuple[EvaluationCase, ...] = ()


def load_dataset(path: Path) -> Dataset:
    """Load an evaluation dataset from ``path``.

    Top-level keys other than ``cases`` (e.g. ``name``, ``_note``) are folded
    into ``Dataset.metadata``; ``name`` defaults to the file stem.
    """
    with Path(path).open(encoding="utf-8") as fh:
        raw = json.load(fh)

    all_cases = tuple(
        EvaluationCase(
            inputs=dict(case.get("inputs", {})),
            reference_outputs=dict(case.get("reference_outputs", {})),
            metadata=dict(case.get("metadata", {})),
        )
        for case in raw.get("cases", [])
    )
    cases = tuple(
        case for case in all_cases if not case.metadata.get("exclude_from_eval", False)
    )
    excluded_cases = tuple(
        case for case in all_cases if case.metadata.get("exclude_from_eval", False)
    )

    metadata = {
        key: value
        for key, value in raw.items()
        if key not in {"cases", "name"}
    }

    return Dataset(
        name=raw.get("name", Path(path).stem),
        cases=cases,
        metadata=metadata,
        excluded_cases=excluded_cases,
    )
