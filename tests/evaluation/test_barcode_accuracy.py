from naot_poc.evaluation.evaluators import barcode_accuracy


def _eval(expected, found):
    return barcode_accuracy(
        inputs={},
        outputs={"barcodes": found},
        reference_outputs={"barcodes": expected},
    )


def test_perfect_match_is_complete_with_full_recall():
    scores = _eval(["A", "B"], ["A", "B"])

    assert scores["barcode_recall"] == 1.0
    assert scores["complete"] is True
    assert scores["false_positives"] == 0
    assert scores["matched_count"] == 2
    assert scores["expected_count"] == 2
    assert scores["found_count"] == 2


def test_partial_match_is_not_complete():
    scores = _eval(["A", "B", "C"], ["A", "B"])

    assert scores["barcode_recall"] == 2 / 3
    assert scores["complete"] is False
    assert scores["false_positives"] == 0
    assert scores["matched_count"] == 2
    assert scores["expected_count"] == 3
    assert scores["found_count"] == 2


def test_false_positive_disqualifies_complete_even_when_all_expected_found():
    # The strict-complete fix: found == expected, not matched == expected.
    scores = _eval(["A", "B"], ["A", "B", "X"])

    assert scores["barcode_recall"] == 1.0
    assert scores["complete"] is False
    assert scores["false_positives"] == 1
    assert scores["matched_count"] == 2
    assert scores["found_count"] == 3


def test_empty_expected_is_complete_when_nothing_found():
    scores = _eval([], [])

    assert scores["barcode_recall"] == 1.0
    assert scores["complete"] is True
    assert scores["false_positives"] == 0
    assert scores["expected_count"] == 0


def test_empty_expected_with_false_positive_is_not_complete():
    scores = _eval([], ["X"])

    assert scores["barcode_recall"] == 1.0
    assert scores["complete"] is False
    assert scores["false_positives"] == 1


def test_empty_found_against_nonempty_expected():
    scores = _eval(["A"], [])

    assert scores["barcode_recall"] == 0.0
    assert scores["complete"] is False
    assert scores["matched_count"] == 0


def test_missing_barcodes_key_in_outputs_treated_as_empty():
    scores = barcode_accuracy(
        inputs={},
        outputs={},
        reference_outputs={"barcodes": ["A"]},
    )

    assert scores["barcode_recall"] == 0.0
    assert scores["complete"] is False
    assert scores["found_count"] == 0


def test_duplicate_barcodes_count_as_separate_boxes():
    scores = _eval(["A", "A"], ["A"])

    assert scores["expected_count"] == 2
    assert scores["found_count"] == 1
    assert scores["matched_count"] == 1
    assert scores["barcode_recall"] == 0.5
    assert scores["complete"] is False


def test_duplicate_barcodes_can_be_complete_when_counts_match():
    scores = _eval(["A", "A"], ["A", "A"])

    assert scores["expected_count"] == 2
    assert scores["found_count"] == 2
    assert scores["matched_count"] == 2
    assert scores["complete"] is True
