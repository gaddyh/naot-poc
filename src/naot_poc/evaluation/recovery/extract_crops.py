"""Extract failed recovery crops for micro-benchmarking.

Runs the audited workflow on each image, captures every targeted-recovery
region (both successful and failed), saves the crop to disk, and writes a
``recovery_cases.json`` manifest.

The manifest format::

    {
      "cases": [
        {
          "id": "vegan_a_region_1",
          "image": "samples/vegan_12_labels_a.jpeg",
          "crop_file": "crops/vegan_a_region_1.png",
          "label_index": 1,
          "box": [x1, y1, x2, y2],
          "original_box": [x1, y1, x2, y2],
          "status": "clear",
          "confidence": "high",
          "recovered": false,
          "recovered_values": [],
          "successful_transforms": [],
          "attempts": 64,
          "crop_padding": "none",
          "expected_barcodes": ["7297501154155", "7297501153974", ...],
          "initial_barcodes": ["7297501154056", ...],
          "missing_barcodes": ["7297501154155", ...]
        }
      ]
    }

``expected_barcodes`` is the full ground-truth list for the image.
``initial_barcodes`` is what the multipass scanner found before recovery.
``missing_barcodes`` is the multiset difference (expected - found).

The micro-benchmark runner can check whether a decoded value is in
``missing_barcodes`` (true positive), in ``initial_barcodes`` (neighbor bleed),
or not in ``expected_barcodes`` at all (false positive).

Usage::

    .venv/bin/python -m naot_poc.evaluation.recovery.extract_crops \\
        --output-dir evaluation/recovery
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps

from naot_poc.evaluation.datasets.loader import load_dataset
from naot_poc.evaluation.targets.ingest_image import run_audited_ingest_image
from naot_poc.integrations.primary_only import PrimaryOnlyScanner
from naot_poc.integrations.zxing import MultiPassZXingScanner

DEFAULT_DATASET = Path("src/naot_poc/evaluation/datasets/barcode_baseline.json")


def _image_slug(image_path: str) -> str:
    """Convert 'vegan_12_labels_a.jpeg' → 'vegan_a'."""
    name = Path(image_path).stem
    # Strip common suffixes/prefixes.
    name = name.replace("topdown_12_labels_", "topdown_")
    name = name.replace("vegan_12_labels_", "vegan_")
    name = name.replace("multi_12_clean", "multi_clean")
    name = name.replace("multi_clear_6_boxes", "multi_clear")
    name = name.replace("stacked_6_labels", "stacked")
    return name


def _save_crop(
    image_path: Path,
    box: list[int],
    output_path: Path,
) -> None:
    """Save a crop of ``image_path`` at ``box`` to ``output_path``."""
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        x1, y1, x2, y2 = box
        x1 = max(0, min(x1, img.width))
        y1 = max(0, min(y1, img.height))
        x2 = max(x1, min(x2, img.width))
        y2 = max(y1, min(y2, img.height))
        crop = img.crop((x1, y1, x2, y2))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        crop.save(output_path)


async def _extract(
    dataset_path: Path,
    root: Path,
    output_dir: Path,
) -> int:
    dataset = load_dataset(dataset_path)
    scanner = PrimaryOnlyScanner(MultiPassZXingScanner())
    crops_dir = output_dir / "crops"
    crops_dir.mkdir(parents=True, exist_ok=True)

    cases: list[dict[str, Any]] = []

    for case in dataset.cases:
        if case.metadata.get("exclude_from_eval"):
            continue

        image_rel = case.inputs["image"]
        image_path = (root / image_rel).resolve()
        slug = _image_slug(image_rel)
        expected = list(case.reference_outputs.get("barcodes", []))

        print(f"Processing {image_rel} ...", flush=True)
        outputs = await run_audited_ingest_image(
            {"image": str(image_path)},
            scanner=scanner,
        )

        initial = list(outputs.get("initial_barcodes", []))
        expected_counter = Counter(expected)
        initial_counter = Counter(initial)
        missing_counter = expected_counter - initial_counter
        missing_barcodes = [
            value
            for value, count in sorted(missing_counter.items())
            for _ in range(count)
        ]

        diagnostics = outputs.get("recovery_diagnostics", [])
        for diag in diagnostics:
            label_index = diag["label_index"]
            box = diag["box"]
            case_id = f"{slug}_region_{label_index}"
            crop_file = f"crops/{case_id}.png"

            # Save the crop at the padded box that recovery actually decoded from.
            # The original (unpadded) Gemini box is not included in diagnostics;
            # the padded box is the best proxy for what the decoder saw.
            _save_crop(image_path, box, output_dir / crop_file)

            cases.append(
                {
                    "id": case_id,
                    "image": image_rel,
                    "crop_file": crop_file,
                    "label_index": label_index,
                    "box": box,
                    "status": diag["status"],
                    "confidence": diag["confidence"],
                    "recovered": diag["recovered"],
                    "recovered_values": list(diag["recovered_values"]),
                    "successful_transforms": list(diag["successful_transforms"]),
                    "attempts": diag["attempts"],
                    "crop_padding": diag.get("crop_padding", ""),
                    "expected_barcodes": expected,
                    "initial_barcodes": initial,
                    "missing_barcodes": missing_barcodes,
                }
            )

            status = "OK" if diag["recovered"] else "FAIL"
            print(
                f"  {case_id}: {status} "
                f"attempts={diag['attempts']} "
                f"padding={diag.get('crop_padding', 'none')}",
                flush=True,
            )

    manifest = {"cases": cases}
    manifest_path = output_dir / "recovery_cases.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"\nWrote {manifest_path} ({len(cases)} cases)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="extract-crops",
        description="Extract failed recovery crops for micro-benchmarking.",
    )
    parser.add_argument(
        "--dataset",
        default=str(DEFAULT_DATASET),
        help="Path to the dataset JSON.",
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Root directory for resolving image paths.",
    )
    parser.add_argument(
        "--output-dir",
        default="evaluation/recovery",
        help="Output directory for crops and manifest.",
    )
    args = parser.parse_args(argv)
    return asyncio.run(
        _extract(
            Path(args.dataset),
            Path(args.root).resolve(),
            Path(args.output_dir),
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
