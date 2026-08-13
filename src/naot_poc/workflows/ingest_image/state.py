from pathlib import Path
from typing import TypedDict

from naot_poc.domain.models import ScanResult


class IngestImageState(TypedDict, total=False):
    image_path: Path
    scan_result: ScanResult