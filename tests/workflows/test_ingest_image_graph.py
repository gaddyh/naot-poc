from pathlib import Path

from naot_poc.domain.models import (
    BarcodeFormat,
    BoundingBox,
    DetectedBarcode,
    ScanResult,
)
from naot_poc.workflows.ingest_image.graph import build_ingest_image_graph


class FakeScanner:
    def __init__(self, result: ScanResult) -> None:
        self._result = result
        self.calls: list[Path] = []

    def scan(self, image_path: Path) -> ScanResult:
        self.calls.append(image_path)
        return self._result


def _scan_result() -> ScanResult:
    return ScanResult(
        barcodes=(
            DetectedBarcode(
                value="7297500243430",
                format=BarcodeFormat.CODE128,
                bounding_box=BoundingBox(x1=257, y1=710, x2=259, y2=914),
            ),
        ),
        image_width=1608,
        image_height=2048,
    )


async def test_graph_runs_scan_node_and_returns_scan_result():
    scanner = FakeScanner(_scan_result())
    graph = build_ingest_image_graph(scanner)

    result = await graph.ainvoke(
        {"image_path": Path("samples/multi_clear_6_boxes.jpeg")}
    )

    assert scanner.calls == [Path("samples/multi_clear_6_boxes.jpeg")]

    scan_result = result["scan_result"]

    assert scan_result.image_width == 1608
    assert scan_result.image_height == 2048
    assert scan_result.barcodes[0].value == "7297500243430"
