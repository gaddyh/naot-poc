"""Target adapter: run the ingest_image workflow and normalize its output.

This is the **only** place that knows about LangGraph state shape
(``result["scan_result"]``) and the ``ScanResult`` domain model. It normalizes
the workflow output into a stable contract::

    {"barcodes": ["7297500243430", ...]}

so evaluators never see graph state or domain models. When Gemini / recovery
are added, either this adapter is updated to invoke the new workflow, or a
sibling target is added and selected via the CLI. Evaluators and the dataset
are untouched.

This mirrors LangSmith's concept of a target function: application inputs go
in, application outputs come out.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from naot_poc.domain.ports import BarcodeScanner
from naot_poc.integrations.zxing import ZXingBarcodeScanner
from naot_poc.workflows.ingest_image.graph import build_ingest_image_graph

Target = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def run_ingest_image(
    inputs: dict[str, Any],
    scanner: BarcodeScanner | None = None,
) -> dict[str, Any]:
    """Run the ingest_image workflow for ``inputs`` and return normalized output.

    ``inputs`` must contain ``"image"`` (a path string, resolved by the caller).
    """
    image_path = Path(inputs["image"])
    graph = build_ingest_image_graph(scanner or ZXingBarcodeScanner())

    result = await graph.ainvoke({"image_path": image_path})

    scan_result = result["scan_result"]

    return {
        "barcodes": [barcode.value for barcode in scan_result.barcodes],
    }
