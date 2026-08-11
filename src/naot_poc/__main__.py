"""CLI entry point: scan an image for barcodes."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from naot_poc.domain.errors import InvalidInputError, NaotPocError, ScannerError
from naot_poc.ingest.service import IngestService
from naot_poc.runtime.context import RunContext
from naot_poc.scanning.zxing_scanner import ZXingBarcodeScanner

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    image_path = Path(args.image)

    service = IngestService(scanner=ZXingBarcodeScanner())
    context = RunContext(operation_name="barcode_scan")

    try:
        result = service.ingest_image(image_path, context=context)
    except InvalidInputError as exc:
        print(f"Invalid input: {exc}\nrun_id={context.run_id}", file=sys.stderr)
        return 1
    except ScannerError as exc:
        print(f"Scanner error: {exc}\nrun_id={context.run_id}", file=sys.stderr)
        return 2
    except NaotPocError as exc:
        print(f"Error: {exc}\nrun_id={context.run_id}", file=sys.stderr)
        return 3

    print(
        f"Scanned {image_path} "
        f"({result.image_width}x{result.image_height}): "
        f"{len(result.barcodes)} barcode(s) found "
        f"[run_id={context.run_id}]"
    )
    for barcode in result.barcodes:
        print(f"  [{barcode.format.value}] {barcode.value} @ {barcode.bounding_box}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
