from dataclasses import dataclass
from enum import Enum


class BarcodeFormat(str, Enum):
    EAN13 = "EAN13"
    CODE128 = "CODE128"
    QR_CODE = "QR_CODE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class BoundingBox:
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass(frozen=True)
class DetectedBarcode:
    value: str
    format: BarcodeFormat
    bounding_box: BoundingBox


@dataclass(frozen=True)
class ScanResult:
    barcodes: tuple[DetectedBarcode, ...]
    image_width: int
    image_height: int