from pathlib import Path

from naot_poc.domain.models import ScanResult
from naot_poc.runtime.context import RunContext
from naot_poc.runtime.executor import execute
from naot_poc.scanning.scanner import BarcodeScanner


class IngestService:
    def __init__(self, scanner: BarcodeScanner):
        self._scanner = scanner

    def ingest_image(
        self,
        image_path: Path,
        context: RunContext,
    ) -> ScanResult:
        execution = execute(
            operation=self._scanner.scan,
            input_=image_path,
            context=context,
        )

        return execution.value