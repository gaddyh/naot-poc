"""Integration adapter: multi-pass ZXing scanner -> domain ScanResult.

This is the adapter boundary between the imported multi-pass barcode-scanner
algorithm (:mod:`naot_poc.integrations.zxing.enhanced_scanner`) and the domain
port :class:`naot_poc.domain.ports.BarcodeScanner`.

Layering::

    Workflow
        ↓
    BarcodeScanner (domain port)
        ↓
    MultiPassZXingScanner (this adapter)
        ↓
    enhanced_scanner.BarcodeScanner (imported algorithm)
        ↓
    ZXing / cv2 / numpy

The workflow continues to see only ``scan(path) -> ScanResult``; it does not
know that this implementation uses OpenCV, tiles, multiple preprocessing
passes, orientation, etc. Implementation-only details (which preprocessing
variant found a code, tile index, upscale factor) stay inside the integration
layer. ``orientation`` is surfaced to the domain because it describes an
actual detection and may matter for later reconciliation/recovery.

The imported algorithm is configured here with the barcode formats relevant to
this project (Code128 + EAN13); its own default remains Code128-only so the
original behaviour is preserved.
"""

from __future__ import annotations

from pathlib import Path

import zxingcpp
from PIL import Image, UnidentifiedImageError

from naot_poc.domain.errors import InvalidInputError, ScannerError
from naot_poc.domain.models import (
    BarcodeFormat,
    BoundingBox,
    DetectedBarcode,
    ScanResult,
)
from naot_poc.integrations.zxing.enhanced_scanner import (
    BarcodeScanner as _InternalBarcodeScanner,
)
from naot_poc.integrations.zxing.enhanced_scanner import (
    BoundingBox as _InternalBoundingBox,
)
from naot_poc.integrations.zxing.enhanced_scanner import (
    DetectedBarcode as _InternalDetectedBarcode,
)

# Default format set for this project: shoe-box/product barcodes are EAN13-style,
# while warehouse/logistics labels are frequently Code128. Scanning both removes
# an artificial handicap versus the baseline (which reads all formats) while
# keeping the multi-pass strategy as the variable under test.
DEFAULT_FORMATS: tuple[zxingcpp.BarcodeFormat, ...] = (
    zxingcpp.BarcodeFormat.Code128,
    zxingcpp.BarcodeFormat.EAN13,
)

# Maps the internal scanner's normalized format strings to the domain enum.
_FORMAT_STRING_TO_DOMAIN: dict[str, BarcodeFormat] = {
    "Code128": BarcodeFormat.CODE128,
    "EAN-13": BarcodeFormat.EAN13,
    "QRCode": BarcodeFormat.QR_CODE,
}


class MultiPassZXingScanner:
    """BarcodeScanner port implementation backed by the multi-pass algorithm.

    Conforms to :class:`naot_poc.domain.ports.BarcodeScanner` so it is a
    drop-in replacement for :class:`ZXingBarcodeScanner` in the workflow and
    the evaluation harness.
    """

    def __init__(
        self,
        *,
        formats: tuple[zxingcpp.BarcodeFormat, ...] = DEFAULT_FORMATS,
        **scanner_kwargs: object,
    ) -> None:
        self._scanner = _InternalBarcodeScanner(formats=formats, **scanner_kwargs)

    def scan(self, image_path: Path) -> ScanResult:
        if not image_path.exists():
            raise InvalidInputError(f"Image does not exist: {image_path}")

        try:
            with Image.open(image_path) as image:
                width, height = image.size
                internal = self._scanner.scan_image(image)

        except UnidentifiedImageError as exc:
            raise InvalidInputError(
                f"File is not a valid image: {image_path}"
            ) from exc

        except Exception as exc:
            raise ScannerError(
                f"Barcode scan failed for: {image_path}"
            ) from exc

        barcodes = tuple(self._to_domain(detection) for detection in internal)

        return ScanResult(
            barcodes=barcodes,
            image_width=width,
            image_height=height,
        )

    @staticmethod
    def _to_domain(
        detection: _InternalDetectedBarcode,
    ) -> DetectedBarcode:
        return DetectedBarcode(
            value=detection.value,
            format=_FORMAT_STRING_TO_DOMAIN.get(
                detection.format,
                BarcodeFormat.UNKNOWN,
            ),
            bounding_box=MultiPassZXingScanner._to_bounding_box(
                detection.bounding_box
            ),
            orientation=detection.orientation,
        )

    @staticmethod
    def _to_bounding_box(box: _InternalBoundingBox) -> BoundingBox:
        return BoundingBox(
            x1=box.x1,
            y1=box.y1,
            x2=box.x2,
            y2=box.y2,
        )
