"""CLI entry point: run barcode evaluation over a dataset.

Usage::

    naot-eval
    naot-eval --dataset path/to/dataset.json --root /repo
    naot-eval --target audited
    naot-eval --target audited --refresh-cache
    naot-eval --no-primary-only
"""

from __future__ import annotations

import argparse
import asyncio
from functools import partial
from pathlib import Path

from naot_poc.domain.ports import BarcodeScanner
from naot_poc.evaluation.datasets.loader import load_dataset
from naot_poc.evaluation.regression.runner import print_report, run_evaluation
from naot_poc.evaluation.targets.ingest_image import (
    run_audited_ingest_image,
    run_ingest_image,
)
from naot_poc.integrations.gemini.auditor import GeminiSpatialAuditor
from naot_poc.integrations.primary_only import PrimaryOnlyScanner
from naot_poc.integrations.zxing import MultiPassZXingScanner

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
    parser.add_argument(
        "--target",
        default="scan",
        choices=("scan", "audited"),
        help="Workflow target: scan (multipass only) or audited (multipass + "
        "Gemini spatial audit + targeted recovery). Defaults to scan.",
    )
    parser.add_argument(
        "--refresh-cache",
        dest="refresh_cache",
        action="store_true",
        default=False,
        help="Ignore cached Gemini audit responses and re-fetch from the API. "
        "Only relevant for --target audited.",
    )
    parser.add_argument(
        "--no-primary-only",
        dest="primary_only",
        action="store_false",
        default=True,
        help="Disable EAN-13 primary-only filtering (keep all detected barcodes). "
        "By default only valid EAN-13 barcodes are kept, matching the Naot workflow.",
    )
    return parser


async def _run(
    dataset_path: Path,
    root: Path,
    scanner: BarcodeScanner,
    *,
    target_name: str,
    refresh_cache: bool = False,
) -> int:
    dataset = load_dataset(dataset_path)
    print(f"dataset: {dataset.name} ({len(dataset.cases)} cases)")
    print(f"target:  {target_name}")
    print(f"scanner: {type(scanner).__name__}")
    if dataset.excluded_cases:
        print(f"excluded: {len(dataset.excluded_cases)} case(s) marked exclude_from_eval")
    if dataset.metadata.get("_note"):
        print(f"note:    {dataset.metadata['_note']}")
    print()

    if target_name == "audited":
        auditor = GeminiSpatialAuditor(refresh=refresh_cache)
        target = partial(
            run_audited_ingest_image, scanner=scanner, auditor=auditor
        )
    else:
        target = partial(run_ingest_image, scanner=scanner)

    runs, metrics = await run_evaluation(
        cases=list(dataset.cases),
        root=root,
        target=target,
    )

    print_report(runs, metrics)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    dataset_path = Path(args.dataset)
    root = Path(args.root).resolve()
    target_name = args.target
    scanner: BarcodeScanner = MultiPassZXingScanner()
    if args.primary_only:
        scanner = PrimaryOnlyScanner(scanner)
    return asyncio.run(
        _run(
            dataset_path,
            root,
            scanner,
            target_name=target_name,
            refresh_cache=args.refresh_cache,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
