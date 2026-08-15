"""Target adapters for the ingest-image workflows.

The target boundary owns LangGraph state normalization and keeps evaluators
agnostic to workflow state and domain models. Both scanner-only and audited
variants return the stable ``{"barcodes": [...]}`` contract.
"""

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from naot_poc.domain.ports import BarcodeScanner
from naot_poc.integrations.zxing import MultiPassZXingScanner
from naot_poc.workflows.ingest_image.graph import (
    build_audited_ingest_image_graph,
    build_ingest_image_graph,
)

Target = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


async def run_ingest_image(
    inputs: dict[str, Any],
    scanner: BarcodeScanner | None = None,
) -> dict[str, Any]:
    image_path = Path(inputs["image"])
    graph = build_ingest_image_graph(scanner or MultiPassZXingScanner())
    result = await graph.ainvoke({"image_path": image_path})
    scan_result = result["scan_result"]
    return {"barcodes": [barcode.value for barcode in scan_result.barcodes]}


async def run_audited_ingest_image(
    inputs: dict[str, Any],
    scanner: BarcodeScanner,
    *,
    auditor: Any | None = None,
    recovery_scanner: Any | None = None,
) -> dict[str, Any]:
    """Run scan + Gemini spatial audit + targeted recovery.

    The auditor is injected for tests and production configuration. When it is
    omitted, the lazy Gemini adapter is constructed without importing the
    optional SDK until the graph actually runs.
    """
    if auditor is None:
        from naot_poc.integrations.gemini.auditor import GeminiSpatialAuditor

        auditor = GeminiSpatialAuditor()

    image_path = Path(inputs["image"])
    graph = build_audited_ingest_image_graph(
        scanner,
        auditor,
        recovery_scanner or scanner,
    )
    result = await graph.ainvoke({"image_path": image_path})
    scan_result = result["scan_result"]
    reconciliation = result.get("reconciliation")
    return {
        "barcodes": [barcode.value for barcode in scan_result.barcodes],
        "audit_failed": bool(result.get("audit_error")),
        "audit_failure_count": int(bool(result.get("audit_error"))),
        "targeted_recovery_attempts": result.get("recovery_attempts", 0),
        "targeted_recovery_successes": result.get("recovery_successes", 0),
        "recovery_diagnostics": [
            {
                "label_index": diagnostic.label_index,
                "box": [
                    diagnostic.box.x1,
                    diagnostic.box.y1,
                    diagnostic.box.x2,
                    diagnostic.box.y2,
                ],
                "status": diagnostic.status,
                "confidence": diagnostic.confidence,
                "attempts": diagnostic.attempts,
                "recovered": diagnostic.recovered,
                "recovered_values": list(diagnostic.recovered_values),
                "successful_transforms": list(diagnostic.successful_transforms),
                "latency_ms": diagnostic.latency_ms,
                "crop_padding": diagnostic.crop_padding,
                "error": diagnostic.error,
            }
            for diagnostic in result.get("recovery_diagnostics", ())
        ],
        "audit_visible_labels": (
            reconciliation.visible_count if reconciliation is not None else 0
        ),
        "initial_barcodes": [
            barcode.value for barcode in result.get("initial_barcodes", ())
        ],
        "recovery_added_barcodes": [
            {
                "value": barcode.value,
                "box": [
                    barcode.bounding_box.x1,
                    barcode.bounding_box.y1,
                    barcode.bounding_box.x2,
                    barcode.bounding_box.y2,
                ],
            }
            for barcode in result.get("recovery_added_barcodes", ())
        ],
    }
