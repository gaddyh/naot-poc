import asyncio
from time import perf_counter
from typing import Any

from langsmith import traceable

from naot_poc.domain.barcode import is_valid_ean13
from naot_poc.domain.models import BoundingBox, DetectedBarcode, ScanResult
from naot_poc.runtime.context import RunContext
from naot_poc.runtime.executor import execute

from .reconciliation import RecoveryRegionDiagnostic, reconcile_scan_and_audit
from .state import IngestImageState

# Adaptive crop padding levels for targeted recovery. For each missing Gemini
# region, recovery tries crops from tightest to loosest, stopping at the first
# crop that yields a valid primary EAN-13. This isolates whether the bottleneck
# is crop localization (padding helps) or ZXing decoding (padding irrelevant).
_CROP_PADDINGS: tuple[tuple[str, float], ...] = (
    ("exact", 0.0),
    ("+10%", 0.10),
    ("+25%", 0.25),
    ("+40%", 0.40),
)


@traceable(name="zxing.scan", run_type="tool")
async def _traced_scan(scanner: Any, image_path: Any) -> Any:
    """Traced wrapper around the zxing scan execution for LangSmith nesting."""
    return await execute(
        operation=scanner.scan,
        input_=image_path,
        context=RunContext(operation_name="scan_image"),
    )


@traceable(name="gemini.spatial_audit", run_type="tool")
async def _traced_audit(auditor: Any, image_path: Any) -> Any:
    """Traced wrapper around the Gemini spatial audit execution for LangSmith nesting."""
    return await execute(
        operation=auditor.audit,
        input_=image_path,
        context=RunContext(operation_name="gemini_spatial_audit"),
    )


class IngestImageNodes:
    def __init__(self, scanner):
        self.scanner = scanner

    async def scan(self, state: IngestImageState):
        execution = await execute(
            operation=self.scanner.scan,
            input_=state["image_path"],
            context=RunContext(operation_name="scan_image"),
        )

        return {
            "scan_result": execution.value,
        }


class AuditedIngestImageNodes:
    def __init__(self, scanner: Any, auditor: Any, recovery_scanner: Any | None = None):
        self.scanner = scanner
        self.auditor = auditor
        self.recovery_scanner = recovery_scanner or scanner

    async def parallel_scan_audit(self, state: IngestImageState):
        image_path = state["image_path"]
        scan_task = asyncio.create_task(_traced_scan(self.scanner, image_path))
        audit_task = asyncio.create_task(_traced_audit(self.auditor, image_path))

        scan_result, audit_result = await asyncio.gather(
            scan_task,
            audit_task,
            return_exceptions=True,
        )

        if isinstance(scan_result, BaseException):
            if not audit_task.done():
                audit_task.cancel()
            raise scan_result

        if isinstance(audit_result, BaseException):
            return {
                "scan_result": scan_result.value,
                "audit_error": f"{type(audit_result).__name__}: {audit_result}",
            }

        return {
            "scan_result": scan_result.value,
            "audit": audit_result.value,
        }

    async def reconcile(self, state: IngestImageState):
        audit = state.get("audit")
        if audit is None:
            return {
                "missing_regions": (),
                "reconciliation": None,
            }

        result = reconcile_scan_and_audit(state["scan_result"], audit)
        return {
            "missing_regions": result.missing_regions,
            "reconciliation": result,
        }

    async def targeted_recovery(self, state: IngestImageState):
        recover_diagnostics = getattr(
            self.recovery_scanner,
            "recover_region_diagnostics",
            None,
        )
        recover = getattr(self.recovery_scanner, "recover_region", None)
        if recover_diagnostics is None and recover is None:
            raise TypeError("audited recovery requires a targeted recovery scanner")

        image_path = state["image_path"]
        scan_result = state["scan_result"]
        results: list[ScanResult] = []
        diagnostics: list[RecoveryRegionDiagnostic] = []
        successes = 0
        for region in state.get("missing_regions", ()):
            started = perf_counter()
            original = region.original_box
            total_attempts = 0
            all_successful_transforms: list[str] = []
            recovered_values: tuple[str, ...] = ()
            recovered = False
            successful_padding = ""
            error: str | None = None
            best_result: ScanResult | None = None

            for padding_label, padding_frac in _CROP_PADDINGS:
                crop_box = _compute_padded_crop(
                    original,
                    padding_frac,
                    scan_result.image_width,
                    scan_result.image_height,
                )
                try:
                    if recover_diagnostics is not None:
                        execution = await execute(
                            operation=lambda inputs: recover_diagnostics(*inputs),
                            input_=(image_path, crop_box),
                            context=RunContext(operation_name="targeted_barcode_recovery"),
                        )
                        result = execution.value.result
                        attempts = execution.value.attempts
                        transforms = tuple(
                            f"rotate_{attempt.rotation}_{attempt.scale:g}x_{attempt.preprocessing}"
                            + ("_inverted" if attempt.inverted else "")
                            for attempt in attempts
                            if any(is_valid_ean13(value) for value in attempt.values)
                        )
                    else:
                        execution = await execute(
                            operation=lambda inputs: recover(*inputs),
                            input_=(image_path, crop_box),
                            context=RunContext(operation_name="targeted_barcode_recovery"),
                        )
                        result = execution.value
                        attempts = ()
                        transforms = ()
                except Exception as exc:  # noqa: BLE001 - one bad crop must not abort recovery
                    error = f"{type(exc).__name__}: {exc}"
                    continue

                total_attempts += len(attempts)
                all_successful_transforms.extend(transforms)
                values = tuple(barcode.value for barcode in result.barcodes)
                if values:
                    recovered = True
                    recovered_values = values
                    successful_padding = padding_label
                    best_result = result
                    error = None
                    break

            if best_result is not None:
                results.append(best_result)
            successes += int(recovered)
            diagnostics.append(
                RecoveryRegionDiagnostic(
                    label_index=region.label_index,
                    box=region.box,
                    status=region.status,
                    confidence=region.confidence,
                    attempts=total_attempts,
                    recovered=recovered,
                    recovered_values=recovered_values,
                    successful_transforms=tuple(all_successful_transforms),
                    latency_ms=(perf_counter() - started) * 1000,
                    crop_padding=successful_padding,
                    error=error,
                )
            )

        return {
            "recovery_results": tuple(results),
            "recovery_diagnostics": tuple(diagnostics),
            "recovery_attempts": len(state.get("missing_regions", ())),
            "recovery_successes": successes,
        }

    async def merge(self, state: IngestImageState):
        initial = state["scan_result"]
        merged = list(initial.barcodes)
        added_by_recovery: list[DetectedBarcode] = []
        for result in state.get("recovery_results", ()):
            for candidate in result.barcodes:
                if not any(_same_detection(candidate, existing) for existing in merged):
                    merged.append(candidate)
                    added_by_recovery.append(candidate)

        merged.sort(key=lambda barcode: (barcode.bounding_box.y1, barcode.bounding_box.x1))
        return {
            "scan_result": ScanResult(
                barcodes=tuple(merged),
                image_width=initial.image_width,
                image_height=initial.image_height,
            ),
            "initial_barcodes": tuple(initial.barcodes),
            "recovery_added_barcodes": tuple(added_by_recovery),
        }


def _same_detection(first: DetectedBarcode, second: DetectedBarcode) -> bool:
    """Check if two detections represent the same physical barcode.

    Uses the same position-aware logic as the scanner's internal dedup:
    centers within max(40px, 35% of longest dimension) OR IoU >= 0.20
    (with 20px padding expansion). This is more generous than a fixed 30px
    threshold, preventing targeted-recovery re-finds of already-detected
    barcodes from being added as duplicates.
    """
    if first.value != second.value:
        return False
    return _same_physical_position(first.bounding_box, second.bounding_box)


def _same_physical_position(first: BoundingBox, second: BoundingBox) -> bool:
    first_cx = (first.x1 + first.x2) / 2
    first_cy = (first.y1 + first.y2) / 2
    second_cx = (second.x1 + second.x2) / 2
    second_cy = (second.y1 + second.y2) / 2

    first_w = max(1, first.x2 - first.x1)
    first_h = max(1, first.y2 - first.y1)
    second_w = max(1, second.x2 - second.x1)
    second_h = max(1, second.y2 - second.y1)
    longest = max(first_w, first_h, second_w, second_h)

    tolerance = max(40.0, longest * 0.35)
    if abs(first_cx - second_cx) <= tolerance and abs(first_cy - second_cy) <= tolerance:
        return True

    return _iou(
        _expand_box(first, padding=20),
        _expand_box(second, padding=20),
    ) >= 0.20


def _expand_box(box: BoundingBox, *, padding: int) -> BoundingBox:
    return BoundingBox(
        x1=box.x1 - padding,
        y1=box.y1 - padding,
        x2=box.x2 + padding,
        y2=box.y2 + padding,
    )


def _iou(first: BoundingBox, second: BoundingBox) -> float:
    ix1 = max(first.x1, second.x1)
    iy1 = max(first.y1, second.y1)
    ix2 = min(first.x2, second.x2)
    iy2 = min(first.y2, second.y2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    intersection = iw * ih
    if intersection == 0:
        return 0.0
    first_area = max(0, first.x2 - first.x1) * max(0, first.y2 - first.y1)
    second_area = max(0, second.x2 - second.x1) * max(0, second.y2 - second.y1)
    union = first_area + second_area - intersection
    if union <= 0:
        return 0.0
    return intersection / union


def _compute_padded_crop(
    box: BoundingBox,
    padding_frac: float,
    image_width: int,
    image_height: int,
) -> BoundingBox:
    """Compute a crop box with ``padding_frac`` fractional padding, clamped."""
    pad_x = round((box.x2 - box.x1) * padding_frac)
    pad_y = round((box.y2 - box.y1) * padding_frac)
    return BoundingBox(
        x1=max(0, box.x1 - pad_x),
        y1=max(0, box.y1 - pad_y),
        x2=min(image_width, box.x2 + pad_x),
        y2=min(image_height, box.y2 + pad_y),
    )
