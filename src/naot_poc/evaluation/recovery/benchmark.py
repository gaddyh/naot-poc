"""Micro-benchmark runner for crop-level barcode recovery.

Loads crops from ``recovery_cases.json``, applies transform candidates, and
reports which transforms decode each crop correctly.

This is a fast, isolated diagnostic tool — no Gemini, no LangGraph, no full
image. The development loop is::

    22 failed crops
    current recovery: 0/22

    change preprocessing → 4/22
    change ZXing hints   → 9/22
    change deskew        → 15/22

Usage::

    .venv/bin/python -m naot_poc.evaluation.recovery.benchmark \\
        --input evaluation/recovery/recovery_cases.json

    # Save debug images for visual inspection
    .venv/bin/python -m naot_poc.evaluation.recovery.benchmark \\
        --input evaluation/recovery/recovery_cases.json \\
        --debug-dir evaluation/recovery/debug
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any

import cv2
import numpy as np
import zxingcpp
from PIL import Image, ImageOps

from naot_poc.domain.barcode import is_valid_ean13


@dataclass
class TransformResult:
    name: str
    decoded_values: list[str]
    latency_ms: float
    error: str | None = None


@dataclass
class CaseResult:
    case_id: str
    expected: list[str]
    missing: list[str]
    initial: list[str]
    recovered: bool
    successful_transforms: list[str]
    all_results: list[TransformResult] = field(default_factory=list)
    false_positives: list[str] = field(default_factory=list)
    neighbor_bleeds: list[str] = field(default_factory=list)


def _load_image(crop_path: Path) -> Image.Image:
    return ImageOps.exif_transpose(Image.open(crop_path)).convert("RGB")


def _to_numpy(img: Image.Image) -> np.ndarray:
    return np.asarray(img)


def _resize(img: Image.Image, scale: float) -> Image.Image:
    if scale == 1.0:
        return img
    new_size = (int(img.width * scale), int(img.height * scale))
    return img.resize(new_size, Image.LANCZOS)


def _grayscale(img: Image.Image) -> Image.Image:
    return ImageOps.grayscale(img)


def _clahe(img: Image.Image) -> Image.Image:
    gray = cv2.cvtColor(_to_numpy(img), cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return Image.fromarray(enhanced)


def _sharpen(img: Image.Image) -> Image.Image:
    arr = _to_numpy(img)
    kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
    return Image.fromarray(cv2.filter2D(arr, -1, kernel).astype(np.uint8))


def _aggressive_sharpen(img: Image.Image) -> Image.Image:
    arr = _to_numpy(img)
    kernel = np.array([[-1, -1, -1], [-1, 9, -1], [-1, -1, -1]], dtype=np.float32)
    return Image.fromarray(cv2.filter2D(arr, -1, kernel).astype(np.uint8))


def _otsu(img: Image.Image) -> Image.Image:
    gray = cv2.cvtColor(_to_numpy(img), cv2.COLOR_RGB2GRAY)
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return Image.fromarray(binary)


def _adaptive_threshold(img: Image.Image) -> Image.Image:
    gray = cv2.cvtColor(_to_numpy(img), cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
    )
    return Image.fromarray(binary)


def _invert(img: Image.Image) -> Image.Image:
    return ImageOps.invert(img.convert("RGB"))


def _deskew(img: Image.Image) -> Image.Image:
    """Attempt skew correction using Hough line transform."""
    gray = cv2.cvtColor(_to_numpy(img), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 50, 150, apertureSize=3)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, threshold=30,
        minLineLength=max(20, img.width // 4), maxLineGap=10,
    )
    if lines is None:
        return img

    angles = []
    for line in lines:
        x1, y1, x2, y2 = line[0]
        angle = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        # Normalize to near-horizontal angles.
        if angle > 45:
            angle -= 90
        elif angle < -45:
            angle += 90
        if abs(angle) < 20:
            angles.append(angle)

    if not angles:
        return img

    median_angle = np.median(angles)
    if abs(median_angle) < 0.5:
        return img

    arr = _to_numpy(img)
    h, w = arr.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
    rotated = cv2.warpAffine(arr, matrix, (w, h), flags=cv2.INTER_CUBIC,
                             borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(rotated)


def _perspective_rectify(img: Image.Image) -> Image.Image:
    """Attempt perspective correction by finding the largest quadrilateral."""
    gray = cv2.cvtColor(_to_numpy(img), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 30, 200)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return img

    # Find the largest contour that approximates to a quadrilateral.
    contours = sorted(contours, key=cv2.contourArea, reverse=True)[:5]
    for contour in contours:
        peri = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * peri, True)
        if len(approx) == 4 and cv2.contourArea(approx) > img.width * img.height * 0.1:
            pts = approx.reshape(4, 2).astype(np.float32)
            # Order points: top-left, top-right, bottom-right, bottom-left.
            rect = _order_points(pts)
            w = max(
                np.linalg.norm(rect[0] - rect[1]),
                np.linalg.norm(rect[2] - rect[3]),
            )
            h = max(
                np.linalg.norm(rect[0] - rect[3]),
                np.linalg.norm(rect[1] - rect[2]),
            )
            w, h = int(w), int(h)
            if w < 20 or h < 10:
                continue
            dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]],
                           dtype=np.float32)
            matrix = cv2.getPerspectiveTransform(rect, dst)
            arr = _to_numpy(img)
            warped = cv2.warpPerspective(arr, matrix, (w, h))
            return Image.fromarray(warped)

    return img


def _order_points(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    s = pts.sum(axis=1)
    rect[0] = pts[np.argmin(s)]
    rect[2] = pts[np.argmax(s)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


# Transform pipeline: (name, preprocessing_fn, scale, invert, extra_ops)
TransformSpec = tuple[str, Any, float, bool, list]

DEFAULT_TRANSFORMS: list[TransformSpec] = [
    # Existing transforms (matching the recovery tree).
    ("1x_original", None, 1.0, False, []),
    ("2x_original", None, 2.0, False, []),
    ("2x_clahe", _clahe, 2.0, False, []),
    ("2x_sharpened", _sharpen, 2.0, False, []),
    ("3x_otsu", _otsu, 3.0, False, []),
    ("3x_adaptive", _adaptive_threshold, 3.0, False, []),
    ("3x_aggressive_sharpen", _aggressive_sharpen, 3.0, False, []),
    ("3x_otsu_inverted", _otsu, 3.0, True, []),
    # New experiments.
    ("2x_grayscale", _grayscale, 2.0, False, []),
    ("3x_grayscale", _grayscale, 3.0, False, []),
    ("2x_deskew", _deskew, 2.0, False, []),
    ("3x_deskew", _deskew, 3.0, False, []),
    ("2x_deskew_clahe", None, 2.0, False, [_deskew, _clahe]),
    ("3x_deskew_clahe", None, 3.0, False, [_deskew, _clahe]),
    ("2x_perspective", _perspective_rectify, 2.0, False, []),
    ("3x_perspective", _perspective_rectify, 3.0, False, []),
    ("2x_perspective_clahe", None, 2.0, False, [_perspective_rectify, _clahe]),
    ("3x_perspective_clahe", None, 3.0, False, [_perspective_rectify, _clahe]),
    ("4x_original", None, 4.0, False, []),
    ("4x_clahe", _clahe, 4.0, False, []),
    ("4x_aggressive_sharpen", _aggressive_sharpen, 4.0, False, []),
]


def _apply_transform(
    img: Image.Image,
    spec: TransformSpec,
) -> Image.Image:
    _name, preprocess, scale, invert, extra = spec
    result = img

    # Apply extra operations first (deskew, perspective, etc).
    for op in extra:
        result = op(result)

    if preprocess is not None:
        result = preprocess(result)

    if scale != 1.0:
        result = _resize(result, scale)

    if invert:
        result = _invert(result)

    return result


def _decode(img: Image.Image) -> list[str]:
    arr = np.asarray(img)
    if len(arr.shape) == 2:
        arr = np.stack([arr] * 3, axis=-1)
    results = zxingcpp.read_barcodes(
        arr,
        formats=(zxingcpp.BarcodeFormat.Code128, zxingcpp.BarcodeFormat.EAN13),
        try_rotate=True,
        try_downscale=False,
        try_invert=False,
        return_errors=False,
    )
    return [r.text for r in results if r.text]


def _run_transform(img: Image.Image, spec: TransformSpec) -> TransformResult:
    name = spec[0]
    started = perf_counter()
    try:
        prepared = _apply_transform(img, spec)
        values = _decode(prepared)
        latency = (perf_counter() - started) * 1000
        return TransformResult(name=name, decoded_values=values, latency_ms=latency)
    except Exception as exc:  # noqa: BLE001
        latency = (perf_counter() - started) * 1000
        return TransformResult(
            name=name, decoded_values=[], latency_ms=latency,
            error=f"{type(exc).__name__}: {exc}",
        )


def _run_case(
    case: dict[str, Any],
    crops_root: Path,
    transforms: list[TransformSpec],
    debug_dir: Path | None = None,
) -> CaseResult:
    case_id = case["id"]
    crop_path = crops_root / case["crop_file"]
    img = _load_image(crop_path)

    expected = case["expected_barcodes"]
    missing = case["missing_barcodes"]
    initial = case["initial_barcodes"]
    missing_set = set(missing)
    expected_set = set(expected)
    initial_set = set(initial)

    results: list[TransformResult] = []
    successful: list[str] = []
    false_positives: list[str] = []
    neighbor_bleeds: list[str] = []

    for spec in transforms:
        result = _run_transform(img, spec)
        results.append(result)

        for value in result.decoded_values:
            if not is_valid_ean13(value):
                continue
            if value in missing_set:
                if spec[0] not in successful:
                    successful.append(spec[0])
            elif value in initial_set:
                if value not in neighbor_bleeds:
                    neighbor_bleeds.append(value)
            elif value not in expected_set and value not in false_positives:
                false_positives.append(value)

        if debug_dir is not None and result.decoded_values:
            debug_path = debug_dir / f"{case_id}_{spec[0]}.png"
            debug_path.parent.mkdir(parents=True, exist_ok=True)
            _apply_transform(img, spec).save(debug_path)

    return CaseResult(
        case_id=case_id,
        expected=expected,
        missing=missing,
        initial=initial,
        recovered=len(successful) > 0,
        successful_transforms=successful,
        all_results=results,
        false_positives=false_positives,
        neighbor_bleeds=neighbor_bleeds,
    )


def _print_report(results: list[CaseResult], transforms: list[TransformSpec]) -> None:
    failed = [r for r in results if not r.recovered]
    recovered = [r for r in results if r.recovered]

    print(f"\n{'='*80}")
    print(f"Recovery Micro-Benchmark: {len(results)} crops")
    print(f"  Recovered: {len(recovered)} / {len(results)}")
    print(f"  Failed:    {len(failed)} / {len(results)}")
    print(f"{'='*80}\n")

    # Per-case summary.
    print(f"{'case':<40} {'status':>6} {'transform':<30} {'fps':>3} {'nbr':>3}")
    print("-" * 90)
    for r in sorted(results, key=lambda x: x.case_id):
        status = "OK" if r.recovered else "FAIL"
        transform = ", ".join(r.successful_transforms) or "none"
        fps = len(r.false_positives)
        nbr = len(r.neighbor_bleeds)
        print(
            f"{r.case_id:<40.40} {status:>6} {transform:<30.30} {fps:>3} {nbr:>3}"
        )

    # Transform success distribution.
    print("\nTransform success distribution:")
    transform_counts: Counter = Counter()
    for r in results:
        for t in r.successful_transforms:
            transform_counts[t] += 1
    for name, count in sorted(transform_counts.items(), key=lambda x: -x[1]):
        print(f"  {name:<35} {count:>3}")

    # Failed cases detail.
    if failed:
        print(f"\nFailed cases ({len(failed)}):")
        for r in failed:
            print(f"  {r.case_id}")
            print(f"    missing: {r.missing}")
            if r.false_positives:
                print(f"    false_positives: {r.false_positives}")
            if r.neighbor_bleeds:
                print(f"    neighbor_bleeds: {r.neighbor_bleeds}")

    # Aggregate latency.
    all_latencies = [
        tr.latency_ms for r in results for tr in r.all_results if not tr.error
    ]
    if all_latencies:
        all_latencies.sort()
        n = len(all_latencies)
        p50 = all_latencies[n // 2]
        p95 = all_latencies[int(n * 0.95)]
        print(f"\nPer-transform latency: p50={p50:.1f}ms p95={p95:.1f}ms "
              f"(over {n} transform applications)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="recovery-benchmark",
        description="Micro-benchmark for crop-level barcode recovery.",
    )
    parser.add_argument(
        "--input",
        default="evaluation/recovery/recovery_cases.json",
        help="Path to recovery_cases.json manifest.",
    )
    parser.add_argument(
        "--debug-dir",
        default=None,
        help="Directory to save debug images for successful transforms.",
    )
    args = parser.parse_args(argv)

    manifest = json.loads(Path(args.input).read_text())
    cases = manifest["cases"]
    crops_root = Path(args.input).parent

    results: list[CaseResult] = []
    for case in cases:
        print(f"Running {case['id']} ...", flush=True)
        result = _run_case(
            case,
            crops_root,
            DEFAULT_TRANSFORMS,
            debug_dir=Path(args.debug_dir) if args.debug_dir else None,
        )
        results.append(result)

    _print_report(results, DEFAULT_TRANSFORMS)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
