"""Deterministic barcode-accuracy evaluator.

A pure function that scores application outputs against reference outputs::

    barcode_accuracy(inputs=..., outputs=..., reference_outputs=...) -> scores

It returns a LangSmith-friendly scores dict containing **only scalar** values
(``float | int | bool``), so it can be wrapped trivially as a LangSmith code
evaluator::

    def langsmith_barcode_evaluator(inputs, outputs, reference_outputs):
        return barcode_accuracy(
            inputs=inputs,
            outputs=outputs,
            reference_outputs=reference_outputs,
        )

The raw ``matched`` / ``extra`` tuples are intentionally **not** included here;
they are diagnostic details and belong on the run record, not in the scores
dict.

Matching semantics: barcodes are compared as a **multiset of values** (EAN13
strings). Duplicate values represent separate visible boxes and therefore
count separately; positions are ignored. ``complete`` is strict: the found
and expected multisets must be equal (all expected boxes found and no extras).
"""

from collections import Counter
from typing import Any

ScoreValue = float | int | bool
Scores = dict[str, ScoreValue]


def barcode_accuracy(
    *,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    reference_outputs: dict[str, Any],
) -> Scores:
    """Score barcode detection against reference ground truth.

    Reads ``reference_outputs["barcodes"]`` and ``outputs["barcodes"]`` as
    lists of barcode value strings and compares them as sets.
    """
    expected_values = list(reference_outputs.get("barcodes", []))
    found_values = list(outputs.get("barcodes", []))
    expected = Counter(expected_values)
    found = Counter(found_values)

    matched = expected & found
    extra = found - expected

    expected_count = len(expected_values)
    matched_count = sum(matched.values())
    false_positives = sum(extra.values())
    found_count = len(found_values)

    recall = 1.0 if expected_count == 0 else matched_count / expected_count
    complete = found == expected  # strict: all expected boxes and no extras

    return {
        "barcode_recall": recall,
        "complete": complete,
        "false_positives": false_positives,
        "matched_count": matched_count,
        "expected_count": expected_count,
        "found_count": found_count,
    }
