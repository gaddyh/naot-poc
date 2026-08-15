from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from naot_poc.domain.models import BoundingBox, ScanResult
from naot_poc.integrations.gemini.geometry import PixelBoundingBox, bbox_center


@dataclass(frozen=True)
class MissingRegion:
    label_index: int
    box: BoundingBox
    original_box: BoundingBox
    status: str
    confidence: str


@dataclass(frozen=True)
class RecoveryRegionDiagnostic:
    label_index: int
    box: BoundingBox
    status: str
    confidence: str
    attempts: int
    recovered: bool
    recovered_values: tuple[str, ...]
    successful_transforms: tuple[str, ...]
    latency_ms: float
    crop_padding: str = ""
    error: str | None = None


@dataclass(frozen=True)
class ReconciliationResult:
    missing_regions: tuple[MissingRegion, ...]
    matched_count: int
    visible_count: int

    @property
    def attempted_count(self) -> int:
        return len(self.missing_regions)


def reconcile_scan_and_audit(
    scan_result: ScanResult,
    audit: Any,
    *,
    center_tolerance: float = 0.05,
) -> ReconciliationResult:
    """Find audited product labels that have no matching primary scan.

    Gemini boxes and scanner boxes are both expressed in the original image
    pixel frame. Matching uses three checks, any of which counts as a match:

    1. Detection center falls inside the audited barcode/label box.
    2. Detection center is within ``center_tolerance`` (fraction of image
       diagonal) of the box center — catches slight Gemini box displacement.
    3. Detection bounding box overlaps the audited box (IoU ≥ 0.10) — catches
       cases where the detection is spatially associated with the label even
       if neither center falls inside the other's box.

    This conservative matching prevents labels whose crops overlap a
    neighboring already-detected barcode from being falsely marked missing,
    which would cause targeted recovery to re-find the neighbor's barcode and
    introduce duplicate false positives.
    """
    detections = tuple(scan_result.barcodes)
    missing: list[MissingRegion] = []
    matched = 0

    for label in audit.labels:
        if _is_not_recoverable(label):
            continue

        target = label.barcode_bbox or label.label_bbox
        if _has_matching_detection(
            detections,
            target,
            scan_result.image_width,
            scan_result.image_height,
            center_tolerance=center_tolerance,
        ):
            matched += 1
            continue

        padded = _pad_box(target, scan_result.image_width, scan_result.image_height)
        original = BoundingBox(
            x1=max(0, round(target.x1)),
            y1=max(0, round(target.y1)),
            x2=min(scan_result.image_width, round(target.x2)),
            y2=min(scan_result.image_height, round(target.y2)),
        )
        missing.append(
            MissingRegion(
                label_index=label.label_index,
                box=BoundingBox(
                    x1=padded.x1,
                    y1=padded.y1,
                    x2=padded.x2,
                    y2=padded.y2,
                ),
                original_box=original,
                status=_enum_value(label.status),
                confidence=_enum_value(label.confidence),
            )
        )

    return ReconciliationResult(
        missing_regions=tuple(missing),
        matched_count=matched,
        visible_count=len(audit.labels),
    )


def _is_not_recoverable(label: Any) -> bool:
    status = _enum_value(label.status)
    confidence = _enum_value(label.confidence)
    return status in {"cropped", "not_visible"} or (
        status == "uncertain" and confidence == "low"
    )


def _has_matching_detection(
    detections: tuple[Any, ...],
    target: PixelBoundingBox,
    image_width: int,
    image_height: int,
    *,
    center_tolerance: float,
) -> bool:
    target_center = bbox_center(target)
    target_box = BoundingBox(
        x1=target.x1,
        y1=target.y1,
        x2=target.x2,
        y2=target.y2,
    )
    for detection in detections:
        if len(detection.value) != 13 or not detection.value.isdigit():
            continue
        box = detection.bounding_box
        center = ((box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2)
        # Check 1: detection center inside label box.
        if _inside(center, target):
            return True
        # Check 2: detection center close to label box center.
        if _normalized_distance(
            center, target_center, image_width, image_height
        ) <= center_tolerance:
            return True
        # Check 3: detection bounding box overlaps label box.
        if _box_overlap(box, target_box) >= 0.15:
            return True
    return False


def _box_overlap(first: BoundingBox, second: BoundingBox) -> float:
    """Return the larger of IoU and intersection-over-detection-area.

    IoU alone is too strict when the detection and label boxes differ greatly
    in size (e.g. a tight barcode detection inside a large label box).
    Intersection-over-detection-area catches the case where most of the
    detection falls inside the label box even if IoU is low.
    """
    ix1 = max(first.x1, second.x1)
    iy1 = max(first.y1, second.y1)
    ix2 = min(first.x2, second.x2)
    iy2 = min(first.y2, second.y2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    intersection = iw * ih
    if intersection == 0:
        return 0.0
    first_area = max(1, (first.x2 - first.x1) * (first.y2 - first.y1))
    second_area = max(1, (second.x2 - second.x1) * (second.y2 - second.y1))
    union = first_area + second_area - intersection
    iou = intersection / union if union > 0 else 0.0
    iod = intersection / first_area  # fraction of detection inside label
    return max(iou, iod)


def _inside(point: tuple[float, float], box: PixelBoundingBox) -> bool:
    return box.x1 <= point[0] <= box.x2 and box.y1 <= point[1] <= box.y2


def _normalized_distance(
    first: tuple[float, float],
    second: tuple[float, float],
    width: int,
    height: int,
) -> float:
    dx = (first[0] - second[0]) / max(1, width)
    dy = (first[1] - second[1]) / max(1, height)
    return (dx * dx + dy * dy) ** 0.5


def _pad_box(box: PixelBoundingBox, width: int, height: int) -> PixelBoundingBox:
    pad_x = max(12, round(box.width * 0.20))
    pad_y = max(12, round(box.height * 0.20))
    return PixelBoundingBox(
        x1=max(0, box.x1 - pad_x),
        y1=max(0, box.y1 - pad_y),
        x2=min(width, box.x2 + pad_x),
        y2=min(height, box.y2 + pad_y),
    )


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))
