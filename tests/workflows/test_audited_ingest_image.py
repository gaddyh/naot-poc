import asyncio
from pathlib import Path
from types import SimpleNamespace

from naot_poc.domain.models import (
    BarcodeFormat,
    BoundingBox,
    DetectedBarcode,
    ScanResult,
)
from naot_poc.workflows.ingest_image.graph import build_audited_ingest_image_graph


class FakeAuditedScanner:
    def __init__(self, result: ScanResult, events: list[str]) -> None:
        self.result = result
        self.events = events

    def scan(self, image_path: Path) -> ScanResult:
        self.events.append("scan-start")
        return self.result

    def recover_region(self, image_path: Path, region: BoundingBox) -> ScanResult:
        self.events.append(f"recover-{region.x1}")
        return self.result

    def recover_region_diagnostics(self, image_path: Path, region: BoundingBox):
        self.events.append(f"recover-diagnostics-{region.x1}-{region.x2}")
        return SimpleNamespace(
            result=self.result,
            attempts=(
                SimpleNamespace(
                    rotation=0,
                    scale=2.0,
                    preprocessing="clahe",
                    inverted=False,
                    values=(self.result.barcodes[0].value,),
                ),
            ),
        )


class FakeEmptyRecoveryScanner:
    """Scanner whose recovery returns no barcodes until a given crop width."""

    def __init__(self, result: ScanResult, events: list[str], min_width: int) -> None:
        self.result = result
        self.events = events
        self.min_width = min_width

    def scan(self, image_path: Path) -> ScanResult:
        self.events.append("scan-start")
        return ScanResult(barcodes=(), image_width=100, image_height=100)

    def recover_region_diagnostics(self, image_path: Path, region: BoundingBox):
        width = region.x2 - region.x1
        self.events.append(f"recover-diagnostics-{region.x1}-{width}")
        if width >= self.min_width:
            return SimpleNamespace(
                result=self.result,
                attempts=(
                    SimpleNamespace(
                        rotation=0,
                        scale=3.0,
                        preprocessing="aggressive_sharpen",
                        inverted=False,
                        values=(self.result.barcodes[0].value,),
                    ),
                ),
            )
        return SimpleNamespace(result=ScanResult(barcodes=(), image_width=100, image_height=100), attempts=())


class FakeAuditor:
    def __init__(self, audit, events: list[str]) -> None:
        self.audit_result = audit
        self.events = events

    async def audit(self, image_path: Path):
        self.events.append("audit-start")
        await asyncio.sleep(0)
        self.events.append("audit-end")
        return self.audit_result


def _result(value: str = "7297500243423") -> ScanResult:
    return ScanResult(
        barcodes=(
            DetectedBarcode(
                value=value,
                format=BarcodeFormat.EAN13,
                bounding_box=BoundingBox(10, 10, 30, 30),
            ),
        ),
        image_width=100,
        image_height=100,
    )


def _audit(*, missing: bool):
    box = SimpleNamespace(x1=60, y1=60, x2=90, y2=90, width=30, height=30)
    if not missing:
        box = SimpleNamespace(x1=0, y1=0, x2=100, y2=100, width=100, height=100)
    return SimpleNamespace(
        labels=[
            SimpleNamespace(
                label_index=1,
                label_bbox=box,
                barcode_bbox=None,
                status="clear",
                confidence="high",
            )
        ]
    )


async def test_parallel_scan_audit_reconciles_without_recovery_when_matched():
    events: list[str] = []
    scanner = FakeAuditedScanner(_result(), events)
    graph = build_audited_ingest_image_graph(
        scanner,
        FakeAuditor(_audit(missing=False), events),
    )

    result = await graph.ainvoke({"image_path": Path("image.jpeg")})

    assert result["scan_result"].barcodes[0].value == "7297500243423"
    assert "scan-start" in events
    assert "audit-start" in events
    assert result.get("recovery_attempts") is None


async def test_missing_region_is_recovered_after_audit():
    events: list[str] = []
    scanner = FakeAuditedScanner(_result(), events)
    graph = build_audited_ingest_image_graph(
        scanner,
        FakeAuditor(_audit(missing=True), events),
    )

    result = await graph.ainvoke({"image_path": Path("image.jpeg")})

    assert result["recovery_attempts"] == 1
    assert result["recovery_successes"] == 1
    diag = result["recovery_diagnostics"][0]
    assert diag.recovered is True
    assert diag.successful_transforms == ("rotate_0_2x_clahe",)
    assert diag.crop_padding == "exact"
    assert any(event.startswith("recover-diagnostics-") for event in events)


async def test_adaptive_crop_padding_falls_through_to_larger_padding():
    """When exact crop fails, recovery should try +10%, +25%, +40%."""
    events: list[str] = []
    # Original box is (60,60)-(90,90), width=30. +25% padding adds ~8px each side → width ~46.
    # Set min_width=40 so exact (30) and +10% (36) fail, +25% (46) succeeds.
    scanner = FakeEmptyRecoveryScanner(_result(), events, min_width=40)
    graph = build_audited_ingest_image_graph(
        scanner,
        FakeAuditor(_audit(missing=True), events),
    )

    result = await graph.ainvoke({"image_path": Path("image.jpeg")})

    assert result["recovery_successes"] == 1
    diag = result["recovery_diagnostics"][0]
    assert diag.recovered is True
    assert diag.crop_padding == "+25%"
    # Should have tried exact, +10%, and +25% before succeeding
    recover_events = [e for e in events if e.startswith("recover-diagnostics-")]
    assert len(recover_events) == 3


async def test_merge_dedup_prevents_recovery_duplicate():
    """Recovery re-finding an already-detected barcode should not add a duplicate."""
    events: list[str] = []
    # Scanner returns a barcode at (10,10)-(30,30) for both scan and recovery.
    # The audit label box is at (60,60)-(90,90) → marked as missing.
    # Recovery returns the same barcode → merge should dedup it.
    scanner = FakeAuditedScanner(_result(), events)
    graph = build_audited_ingest_image_graph(
        scanner,
        FakeAuditor(_audit(missing=True), events),
    )

    result = await graph.ainvoke({"image_path": Path("image.jpeg")})

    # Recovery succeeded (found barcodes), but merge should dedup to 1 barcode.
    assert result["recovery_successes"] == 1
    assert len(result["scan_result"].barcodes) == 1


async def test_recovery_error_does_not_abort_other_regions():
    """One failing crop must not prevent other regions from being attempted."""
    events: list[str] = []

    class PartialFailScanner:
        def __init__(self):
            self.calls = 0

        def scan(self, image_path: Path) -> ScanResult:
            return ScanResult(barcodes=(), image_width=100, image_height=100)

        def recover_region_diagnostics(self, image_path: Path, region: BoundingBox):
            self.calls += 1
            # First 4 calls = all 4 paddings for region 1 → all fail.
            if self.calls <= 4:
                raise RuntimeError("crop failure")
            return SimpleNamespace(
                result=_result("7297500243447"),
                attempts=(
                    SimpleNamespace(
                        rotation=0, scale=2.0, preprocessing="original",
                        inverted=False, values=("7297500243447",),
                    ),
                ),
            )

    audit = SimpleNamespace(
        labels=[
            SimpleNamespace(
                label_index=1, label_bbox=SimpleNamespace(x1=10, y1=10, x2=40, y2=40, width=30, height=30),
                barcode_bbox=None, status="clear", confidence="high",
            ),
            SimpleNamespace(
                label_index=2, label_bbox=SimpleNamespace(x1=60, y1=60, x2=90, y2=90, width=30, height=30),
                barcode_bbox=None, status="clear", confidence="high",
            ),
        ]
    )

    scanner = PartialFailScanner()
    graph = build_audited_ingest_image_graph(
        scanner,
        FakeAuditor(audit, events),
    )

    result = await graph.ainvoke({"image_path": Path("image.jpeg")})

    assert result["recovery_attempts"] == 2
    assert result["recovery_successes"] == 1
    diags = result["recovery_diagnostics"]
    assert len(diags) == 2
    failed = [d for d in diags if not d.recovered]
    succeeded = [d for d in diags if d.recovered]
    assert len(failed) == 1
    assert failed[0].error is not None
    assert len(succeeded) == 1
    assert succeeded[0].recovered_values == ("7297500243447",)


async def test_reconciliation_overlap_prevents_false_missing():
    """A detection whose bbox overlaps a Gemini label box should match,
    even if its center is outside the label box and beyond center tolerance.
    This prevents the label from being marked missing and causing recovery
    to re-find a neighbor's barcode."""
    # Detection at (30, 30)-(80, 80), center at (55, 55).
    # Label box at (60, 60)-(100, 100). Center (55,55) is outside the box.
    # Normalized distance: dx=5/200=0.025, dy=5/200=0.025 → dist=0.035 > 0.05? No, 0.035 < 0.05.
    # So center tolerance would match. Let me make it further: detection center at (45, 45).
    # Detection at (20, 20)-(70, 70), center (45, 45). Label (60,60)-(100,100).
    # Overlap: (60,60)-(70,70) = 10×10=100. Det area=2500. IoD=0.04. Too small.
    # Use: detection at (30,30)-(90,90), center (60,60). Label (60,60)-(100,100).
    # Center (60,60) is on the label box corner → inside (boundary inclusive).
    # Use: detection at (25,25)-(75,75), center (50,50). Label (60,60)-(100,100).
    # Overlap: (60,60)-(75,75) = 15×15=225. Det area=50×50=2500. IoD=0.09. Still low.
    # Use: detection at (35,35)-(85,85), center (60,60). Label (60,60)-(100,100).
    # Center (60,60) is inside (boundary). Not useful.
    # Use: detection at (30,30)-(85,85), center (57.5, 57.5). Label (60,60)-(100,100).
    # Center outside. Overlap: (60,60)-(85,85) = 25×25=625. Det area=55×55=3025. IoD=0.207. > 0.15 ✓
    scan_result = ScanResult(
        barcodes=(
            DetectedBarcode(
                value="7297500243423",
                format=BarcodeFormat.EAN13,
                bounding_box=BoundingBox(30, 30, 85, 85),
            ),
        ),
        image_width=200,
        image_height=200,
    )
    audit = SimpleNamespace(
        labels=[
            SimpleNamespace(
                label_index=1,
                label_bbox=SimpleNamespace(x1=60, y1=60, x2=100, y2=100, width=40, height=40),
                barcode_bbox=None,
                status="clear",
                confidence="high",
            )
        ]
    )

    from naot_poc.workflows.ingest_image.reconciliation import reconcile_scan_and_audit

    result = reconcile_scan_and_audit(scan_result, audit)
    # The detection overlaps the label box (>15% of detection inside) → matched.
    assert result.matched_count == 1
    assert len(result.missing_regions) == 0
