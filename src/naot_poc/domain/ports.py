from pathlib import Path
from typing import Protocol

from naot_poc.domain.models import ScanResult


class BarcodeScanner(Protocol):
    def scan(self, image_path: Path) -> ScanResult:
        ...
