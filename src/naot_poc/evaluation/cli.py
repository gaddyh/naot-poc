"""CLI entry point: run barcode evaluation over a dataset.

Usage::

    naot-eval
    naot-eval --dataset path/to/dataset.json --root /repo
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from naot_poc.evaluation.datasets.loader import load_dataset
from naot_poc.evaluation.regression.runner import print_report, run_evaluation

DEFAULT_DATASET = Path("src/naot_poc/evaluation/datasets/barcode_baseline.json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="naot-eval",
        description="Run barcode evaluation over a ground-truth dataset.",
    )
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help=f"Path to the dataset JSON (defaults to {DEFAULT_DATASET}).",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory for resolving image paths in the dataset "
        "(defaults to the current working directory).",
    )
    return parser


async def _run(dataset_path: Path, root: Path) -> int:
    dataset = load_dataset(dataset_path)
    print(f"dataset: {dataset.name} ({len(dataset.cases)} cases)")
    if dataset.excluded_cases:
        print(f"excluded: {len(dataset.excluded_cases)} case(s) marked exclude_from_eval")
    if dataset.metadata.get("_note"):
        print(f"note:    {dataset.metadata['_note']}")
    print()

    runs, metrics = await run_evaluation(
        cases=list(dataset.cases),
        root=root,
    )

    print_report(runs, metrics)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset_path = Path(args.dataset)
    root = Path(args.root).resolve()
    return asyncio.run(_run(dataset_path, root))


if __name__ == "__main__":
    raise SystemExit(main())
