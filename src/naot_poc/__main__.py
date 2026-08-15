"""CLI entry point: scan an image for barcodes."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from naot_poc.domain.errors import InvalidInputError, NaotPocError, ScannerError
from naot_poc.integrations.zxing import MultiPassZXingScanner
from naot_poc.runtime.context import RunContext
from naot_poc.workflows.ingest_image.graph import build_ingest_image_graph

DEFAULT_SAMPLE = Path("samples/multi_12_clean.jpeg")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="naot-scan",
        description="Scan an image for barcodes using zxing-cpp.",
    )
    parser.add_argument(
        "image",
        nargs="?",
        default=str(DEFAULT_SAMPLE),
        help=f"Path to the image to scan (defaults to {DEFAULT_SAMPLE}).",
    )
    return parser


async def _run(image_path: Path, context: RunContext) -> int:
    graph = build_ingest_image_graph(MultiPassZXingScanner())

    try:
        result = await graph.ainvoke({"image_path": image_path})
    except InvalidInputError as exc:
        print(f"Invalid input: {exc}\nrun_id={context.run_id}", file=sys.stderr)
        return 1
    except ScannerError as exc:
        print(f"Scanner error: {exc}\nrun_id={context.run_id}", file=sys.stderr)
        return 2
    except NaotPocError as exc:
        print(f"Error: {exc}\nrun_id={context.run_id}", file=sys.stderr)
        return 3

    scan_result = result["scan_result"]

    print(
        f"Scanned {image_path} "
        f"({scan_result.image_width}x{scan_result.image_height}): "
        f"{len(scan_result.barcodes)} barcode(s) found "
        f"[run_id={context.run_id}]"
    )
    for barcode in scan_result.barcodes:
        print(f"  [{barcode.format.value}] {barcode.value} @ {barcode.bounding_box}")

    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    image_path = Path(args.image)
    context = RunContext(operation_name="barcode_scan")

    return asyncio.run(_run(image_path, context))


if __name__ == "__main__":
    raise SystemExit(main())
