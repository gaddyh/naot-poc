from pathlib import Path
from typing import Protocol

from naot_poc.domain.models import BoundingBox, ScanResult


class BarcodeScanner(Protocol):
    def scan(self, image_path: Path) -> ScanResult: ...


class TargetedBarcodeRecovery(Protocol):
    def recover_region(self, image_path: Path, region: BoundingBox) -> ScanResult: ...
