"""Primary-only barcode filter wrapper.

Wraps any :class:`naot_poc.domain.ports.BarcodeScanner` and filters its
``ScanResult`` to only valid EAN-13 barcodes — the primary GTIN-13 barcode on
each shoe box. Secondary Code128 model/size barcodes are dropped.

This is a workflow-level concern, not a scanner-level one: the scanner detects
barcodes; the workflow decides which ones it cares about. The wrapper keeps
scanners pure and avoids duplicating filter logic across implementations.

zxing-cpp classifies GTIN-13 bars as Code128 regardless of the requested format
set, so the filter validates the decoded *value* (13 digits + checksum), not
the ``format`` field.
"""

from __future__ import annotations

from pathlib import Path

from naot_poc.domain.barcode import is_valid_ean13
from naot_poc.domain.models import BoundingBox, ScanResult
from naot_poc.domain.ports import BarcodeScanner


class PrimaryOnlyScanner:
    """BarcodeScanner wrapper that keeps only valid EAN-13 barcodes.

    Conforms to :class:`naot_poc.domain.ports.BarcodeScanner` so it is a
    drop-in replacement anywhere a scanner is expected.
    """

    def __init__(self, inner: BarcodeScanner) -> None:
        self._inner = inner

    def scan(self, image_path: Path) -> ScanResult:
        return self._filter(self._inner.scan(image_path))

    def recover_region(self, image_path: Path, region: BoundingBox) -> ScanResult:
        recovery = getattr(self._inner, "recover_region", None)
        if recovery is None:
            raise TypeError("inner scanner does not support targeted recovery")
        return self._filter(recovery(image_path, region))

    def recover_region_diagnostics(self, image_path: Path, region: BoundingBox):
        recovery = getattr(self._inner, "recover_region_diagnostics", None)
        if recovery is None:
            raise TypeError("inner scanner does not support recovery diagnostics")
        diagnostics = recovery(image_path, region)
        return type(diagnostics)(
            result=self._filter(diagnostics.result),
            attempts=diagnostics.attempts,
        )

    @staticmethod
    def _filter(result: ScanResult) -> ScanResult:
        primary = tuple(
            barcode for barcode in result.barcodes if is_valid_ean13(barcode.value)
        )
        return ScanResult(
            barcodes=primary,
            image_width=result.image_width,
            image_height=result.image_height,
        )
