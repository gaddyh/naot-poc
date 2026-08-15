from pathlib import Path
from typing import Any, TypedDict

from naot_poc.domain.models import DetectedBarcode, ScanResult
from naot_poc.workflows.ingest_image.reconciliation import (
    MissingRegion,
    ReconciliationResult,
    RecoveryRegionDiagnostic,
)


class IngestImageState(TypedDict, total=False):
    image_path: Path
    scan_result: ScanResult
    audit: Any
    audit_error: str
    reconciliation: ReconciliationResult
    missing_regions: tuple[MissingRegion, ...]
    recovery_results: tuple[ScanResult, ...]
    recovery_diagnostics: tuple[RecoveryRegionDiagnostic, ...]
    recovery_attempts: int
    recovery_successes: int
    initial_barcodes: tuple[DetectedBarcode, ...]
    recovery_added_barcodes: tuple[DetectedBarcode, ...]
