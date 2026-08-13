from pathlib import Path

from naot_poc.evaluation.datasets import EvaluationCase
from naot_poc.evaluation.regression import run_evaluation


class FakeTarget:
    """Records calls and returns canned outputs, optionally raising."""

    def __init__(self, outputs_by_image: dict[str, dict], errors: dict[str, str] | None = None):
        self._outputs = outputs_by_image
        self._errors = errors or {}
        self.calls: list[dict] = []

    async def __call__(self, inputs: dict) -> dict:
        self.calls.append(inputs)
        image = inputs["image"]
        name = Path(image).name
        if name in self._errors:
            raise RuntimeError(self._errors[name])
        return self._outputs.get(name, {"barcodes": []})


def _case(image: str, expected: list[str]) -> EvaluationCase:
    return EvaluationCase(
        inputs={"image": image},
        reference_outputs={"barcodes": expected},
        metadata={},
    )


async def test_run_evaluation_aggregates_perfect_and_partial_cases():
    cases = [
        _case("samples/a.jpeg", ["A", "B"]),
        _case("samples/b.jpeg", ["A", "B"]),
    ]
    target = FakeTarget(
        {
            "a.jpeg": {"barcodes": ["A", "B"]},  # complete
            "b.jpeg": {"barcodes": ["A"]},  # partial
        }
    )

    runs, metrics = await run_evaluation(cases, root=Path("/repo"), target=target)

    assert len(runs) == 2
    assert runs[0].scores["complete"] is True
    assert runs[1].scores["complete"] is False
    assert runs[1].scores["barcode_recall"] == 0.5

    assert metrics.case_count == 2
    assert metrics.overall_barcode_recall == 0.75  # (2 + 1) / (2 + 2)
    assert metrics.complete_image_rate == 0.5


async def test_run_evaluation_resolves_image_paths_under_root():
    cases = [_case("samples/a.jpeg", ["A"])]
    target = FakeTarget({"a.jpeg": {"barcodes": ["A"]}})

    await run_evaluation(cases, root=Path("/repo"), target=target)

    assert target.calls[0]["image"] == str(Path("/repo/samples/a.jpeg").resolve())


async def test_run_evaluation_records_error_and_continues():
    cases = [
        _case("samples/a.jpeg", ["A", "B"]),
        _case("samples/b.jpeg", ["C"]),
    ]
    target = FakeTarget(
        {
            "a.jpeg": {"barcodes": ["A", "B"]},
        },
        errors={"b.jpeg": "boom"},
    )

    runs, metrics = await run_evaluation(cases, root=Path("/repo"), target=target)

    assert runs[0].error is None
    assert runs[0].scores["complete"] is True

    assert runs[1].error is not None
    assert "boom" in runs[1].error
    assert runs[1].scores["complete"] is False
    assert runs[1].scores["found_count"] == 0
    assert runs[1].scores["barcode_recall"] == 0.0
    assert runs[1].outputs == {}

    # The experiment still completes for all cases.
    assert metrics.case_count == 2


async def test_run_evaluation_populates_matched_and_extra_diagnostics():
    cases = [_case("samples/a.jpeg", ["A", "B"])]
    target = FakeTarget({"a.jpeg": {"barcodes": ["A", "X"]}})

    runs, _ = await run_evaluation(cases, root=Path("/repo"), target=target)

    run = runs[0]
    assert run.matched == ("A",)
    assert run.extra == ("X",)
    assert run.scores["false_positives"] == 1
    assert run.scores["complete"] is False
