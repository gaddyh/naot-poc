from pathlib import Path

from naot_poc.domain.models import ScanResult
from naot_poc.ingest.service import IngestService
from naot_poc.runtime.context import RunContext


class FakeScanner:
    def scan(self, image_path: Path) -> ScanResult:
        return ScanResult(
            barcodes=(),
            image_width=100,
            image_height=200,
        )


def test_ingest_image_uses_scanner():
    service = IngestService(scanner=FakeScanner())
    context = RunContext(operation_name="barcode_scan")

    result = service.ingest_image(
        Path("anything.jpg"),
        context=context,
    )

    assert result.image_width == 100
    assert result.image_height == 200
    assert result.barcodes == ()