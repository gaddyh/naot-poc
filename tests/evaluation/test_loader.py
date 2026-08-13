import json
from pathlib import Path

from naot_poc.evaluation.datasets import EvaluationCase, load_dataset


def _write_dataset(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_load_dataset_builds_cases_with_inputs_reference_and_metadata(tmp_path):
    path = _write_dataset(
        tmp_path,
        {
            "name": "test_set",
            "_note": "hello",
            "cases": [
                {
                    "inputs": {"image": "samples/a.jpeg"},
                    "reference_outputs": {"barcodes": ["111", "222"]},
                    "metadata": {"source": "manual"},
                },
                {
                    "inputs": {"image": "samples/b.jpeg"},
                    "reference_outputs": {"barcodes": []},
                    "metadata": {},
                },
            ],
        },
    )

    dataset = load_dataset(path)

    assert dataset.name == "test_set"
    assert len(dataset.cases) == 2
    assert dataset.metadata["_note"] == "hello"

    first = dataset.cases[0]
    assert isinstance(first, EvaluationCase)
    assert first.inputs == {"image": "samples/a.jpeg"}
    assert first.reference_outputs == {"barcodes": ["111", "222"]}
    assert first.metadata == {"source": "manual"}

    assert dataset.cases[1].reference_outputs == {"barcodes": []}


def test_load_dataset_skips_excluded_cases_and_retains_them_for_visibility(tmp_path):
    path = _write_dataset(
        tmp_path,
        {
            "cases": [
                {"inputs": {"image": "included.jpeg"}},
                {
                    "inputs": {"image": "fuzzy.jpeg"},
                    "metadata": {"exclude_from_eval": True},
                },
            ],
        },
    )

    dataset = load_dataset(path)

    assert [case.inputs["image"] for case in dataset.cases] == ["included.jpeg"]
    assert [case.inputs["image"] for case in dataset.excluded_cases] == ["fuzzy.jpeg"]


def test_load_dataset_defaults_name_to_file_stem_and_tolerates_missing_keys(tmp_path):
    path = _write_dataset(
        tmp_path,
        {
            "cases": [
                {"inputs": {"image": "x.jpeg"}},
            ],
        },
    )

    dataset = load_dataset(path)

    assert dataset.name == "dataset"
    assert dataset.cases[0].reference_outputs == {}
    assert dataset.cases[0].metadata == {}
