"""Unit tests for the spatial reconciliation logic.

Tests the three matching criteria (center inside, center tolerance, box overlap),
non-recoverable label filtering, missing-region padding, and edge cases — all
independently of the LangGraph workflow.
"""

from types import SimpleNamespace

from naot_poc.domain.models import (
    BarcodeFormat,
    BoundingBox,
    DetectedBarcode,
    ScanResult,
)
from naot_poc.integrations.gemini.geometry import PixelBoundingBox
from naot_poc.workflows.ingest_image.reconciliation import (
    reconcile_scan_and_audit,
)


def _detection(value: str, box: tuple[int, int, int, int]) -> DetectedBarcode:
    return DetectedBarcode(
        value=value,
        format=BarcodeFormat.EAN13,
        bounding_box=BoundingBox(*box),
    )


def _scan(detections, width=200, height=200) -> ScanResult:
    return ScanResult(
        barcodes=tuple(detections),
        image_width=width,
        image_height=height,
    )


def _label(
    index=1,
    bbox=(60, 60, 100, 100),
    status="clear",
    confidence="high",
    barcode_bbox=None,
) -> SimpleNamespace:
    x1, y1, x2, y2 = bbox
    return SimpleNamespace(
        label_index=index,
        label_bbox=PixelBoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
        barcode_bbox=barcode_bbox,
        status=status,
        confidence=confidence,
    )


def _audit(labels) -> SimpleNamespace:
    return SimpleNamespace(labels=list(labels))


# --- Matching criteria ---


def test_center_inside_label_matches():
    """Detection center inside the label box → matched."""
    det = _detection("7297500243423", (65, 65, 75, 75))  # center (70,70) inside (60,60)-(100,100)
    result = reconcile_scan_and_audit(_scan([det]), _audit([_label()]))
    assert result.matched_count == 1
    assert len(result.missing_regions) == 0


def test_center_tolerance_matches():
    """Detection center near label center within tolerance → matched."""
    # Label center (80, 80). Detection center (85, 85).
    # Normalized distance: dx=5/200=0.025, dy=5/200=0.025 → 0.035 < 0.05.
    det = _detection("7297500243423", (80, 80, 90, 90))
    result = reconcile_scan_and_audit(
        _scan([det]),
        _audit([_label(bbox=(70, 70, 90, 90))]),
        center_tolerance=0.05,
    )
    assert result.matched_count == 1
    assert len(result.missing_regions) == 0


def test_center_outside_tolerance_no_overlap_is_missing():
    """Detection far from label, no overlap → missing."""
    det = _detection("7297500243423", (10, 10, 30, 30))
    result = reconcile_scan_and_audit(_scan([det]), _audit([_label()]))
    assert result.matched_count == 0
    assert len(result.missing_regions) == 1


def test_box_overlap_matches():
    """Detection center outside label, but boxes overlap enough → matched."""
    # Detection (30,30)-(85,85), center (57.5, 57.5). Label (60,60)-(100,100).
    # Center outside. Overlap: (60,60)-(85,85) = 25*25=625. Det area=55*55=3025.
    # IoD = 625/3025 = 0.207 > 0.15 → match.
    det = _detection("7297500243423", (30, 30, 85, 85))
    result = reconcile_scan_and_audit(_scan([det]), _audit([_label()]))
    assert result.matched_count == 1
    assert len(result.missing_regions) == 0


def test_box_overlap_below_threshold_is_missing():
    """Detection overlaps label but not enough → missing."""
    # Detection (10,10)-(50,50), center (30,30). Label (60,60)-(100,100).
    # Overlap: (60,60)-(50,50) → no overlap (x2 < x1). Missing.
    det = _detection("7297500243423", (10, 10, 50, 50))
    result = reconcile_scan_and_audit(_scan([det]), _audit([_label()]))
    assert result.matched_count == 0
    assert len(result.missing_regions) == 1


def test_non_ean13_detection_ignored_for_matching():
    """Non-13-digit detections should not match labels."""
    det = _detection("900439-42", (65, 65, 75, 75))  # not 13 digits
    result = reconcile_scan_and_audit(_scan([det]), _audit([_label()]))
    assert result.matched_count == 0
    assert len(result.missing_regions) == 1


# --- Non-recoverable labels ---


def test_cropped_label_skipped():
    """Cropped labels are not recoverable → skipped (not missing, not matched)."""
    det = _detection("7297500243423", (10, 10, 30, 30))
    result = reconcile_scan_and_audit(
        _scan([det]),
        _audit([_label(status="cropped")]),
    )
    assert result.matched_count == 0
    assert len(result.missing_regions) == 0
    assert result.visible_count == 1


def test_not_visible_label_skipped():
    det = _detection("7297500243423", (10, 10, 30, 30))
    result = reconcile_scan_and_audit(
        _scan([det]),
        _audit([_label(status="not_visible")]),
    )
    assert result.matched_count == 0
    assert len(result.missing_regions) == 0


def test_uncertain_low_confidence_label_skipped():
    det = _detection("7297500243423", (10, 10, 30, 30))
    result = reconcile_scan_and_audit(
        _scan([det]),
        _audit([_label(status="uncertain", confidence="low")]),
    )
    assert result.matched_count == 0
    assert len(result.missing_regions) == 0


def test_uncertain_high_confidence_label_processed():
    det = _detection("7297500243423", (10, 10, 30, 30))
    result = reconcile_scan_and_audit(
        _scan([det]),
        _audit([_label(status="uncertain", confidence="high")]),
    )
    assert result.matched_count == 0
    assert len(result.missing_regions) == 1


# --- Missing region properties ---


def test_missing_region_has_padded_box():
    """Missing regions should have a padded box larger than the original."""
    det = _detection("7297500243423", (10, 10, 30, 30))
    label = _label(bbox=(60, 60, 90, 90))  # 30x30 box
    result = reconcile_scan_and_audit(_scan([det]), _audit([label]))
    assert len(result.missing_regions) == 1
    region = result.missing_regions[0]
    # Padded box should be larger than original (30x30 + 12px pad each side = 54x54)
    assert region.box.x1 <= 60
    assert region.box.y1 <= 60
    assert region.box.x2 >= 90
    assert region.box.y2 >= 90
    # Original box should be clamped to image bounds
    assert region.original_box.x1 == 60
    assert region.original_box.y1 == 60
    assert region.original_box.x2 == 90
    assert region.original_box.y2 == 90


def test_missing_region_preserves_label_metadata():
    det = _detection("7297500243423", (10, 10, 30, 30))
    result = reconcile_scan_and_audit(
        _scan([det]),
        _audit([_label(index=7, status="clear", confidence="medium")]),
    )
    region = result.missing_regions[0]
    assert region.label_index == 7
    assert region.status == "clear"
    assert region.confidence == "medium"


def test_padding_clamped_to_image_bounds():
    """Padding should not extend beyond image dimensions."""
    det = _detection("7297500243423", (150, 150, 170, 170))
    label = _label(bbox=(180, 180, 200, 200))  # near bottom-right corner
    result = reconcile_scan_and_audit(_scan([det], width=200, height=200), _audit([label]))
    region = result.missing_regions[0]
    assert region.box.x2 <= 200
    assert region.box.y2 <= 200
    assert region.box.x1 >= 0
    assert region.box.y1 >= 0


# --- Mixed scenarios ---


def test_multiple_labels_some_matched_some_missing():
    det1 = _detection("7297500243423", (65, 65, 75, 75))  # inside label 1
    det2 = _detection("7297500243447", (10, 10, 30, 30))  # far from label 2
    result = reconcile_scan_and_audit(
        _scan([det1, det2]),
        _audit([
            _label(index=1, bbox=(60, 60, 100, 100)),
            _label(index=2, bbox=(130, 130, 170, 170)),
        ]),
    )
    assert result.matched_count == 1
    assert len(result.missing_regions) == 1
    assert result.missing_regions[0].label_index == 2
    assert result.visible_count == 2


def test_empty_detections_all_missing():
    result = reconcile_scan_and_audit(
        _scan([]),
        _audit([_label(index=1), _label(index=2, bbox=(130, 130, 170, 170))]),
    )
    assert result.matched_count == 0
    assert len(result.missing_regions) == 2


def test_empty_audit_no_missing():
    det = _detection("7297500243423", (65, 65, 75, 75))
    result = reconcile_scan_and_audit(_scan([det]), _audit([]))
    assert result.matched_count == 0
    assert len(result.missing_regions) == 0
    assert result.visible_count == 0


def test_barcode_bbox_preferred_over_label_bbox():
    """When both barcode_bbox and label_bbox exist, barcode_bbox is used for matching."""
    det = _detection("7297500243423", (65, 65, 75, 75))
    # label_bbox is far away, but barcode_bbox contains the detection center
    label = SimpleNamespace(
        label_index=1,
        label_bbox=PixelBoundingBox(x1=150, y1=150, x2=190, y2=190),
        barcode_bbox=PixelBoundingBox(x1=60, y1=60, x2=100, y2=100),
        status="clear",
        confidence="high",
    )
    result = reconcile_scan_and_audit(_scan([det]), _audit([label]))
    assert result.matched_count == 1
    assert len(result.missing_regions) == 0


def test_attempted_count_property():
    det = _detection("7297500243423", (10, 10, 30, 30))
    result = reconcile_scan_and_audit(
        _scan([det]),
        _audit([_label(index=1), _label(index=2, bbox=(130, 130, 170, 170))]),
    )
    assert result.attempted_count == 2
    assert result.attempted_count == len(result.missing_regions)
