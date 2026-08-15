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

The imported algorithm defaults to Code128-only; this adapter configures it
with the symbologies present in this project's data (Code128 + EAN13). On this
dataset zxing-cpp classifies the 13-digit GTIN codes as Code128 regardless of
the requested format set, so enabling EAN13 has no measured effect today — but
it is the semantically correct config and future-proofs against images where
zxing-cpp does recognize a true EAN-13.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import zxingcpp
from PIL import Image, ImageOps, UnidentifiedImageError

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
from naot_poc.integrations.zxing.enhanced_scanner import RecoveryAttempt


@dataclass(frozen=True)
class TargetedRecoveryDiagnostics:
    result: ScanResult
    attempts: tuple[RecoveryAttempt, ...]


# Default format set for this project. Shoe-box/product barcodes are GTIN-13
# (EAN-13) on the left and Code128 (model/size) on the right, so both symbologies
# are enabled to reflect the real-world data. Note: on this dataset zxing-cpp
# classifies the 13-digit GTIN codes as Code128 regardless of the requested
# format set (see README "Baseline vs. multi-pass scanner"), so enabling EAN13
# has no measured effect today — but it is the semantically correct config and
# future-proofs against images where zxing-cpp does recognize a true EAN-13.
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
    drop-in scanner for the workflow and the evaluation harness.
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

    def recover_region(self, image_path: Path, region: BoundingBox) -> ScanResult:
        return self.recover_region_diagnostics(image_path, region).result

    def recover_region_diagnostics(
        self,
        image_path: Path,
        region: BoundingBox,
    ) -> TargetedRecoveryDiagnostics:
        if not image_path.exists():
            raise InvalidInputError(f"Image does not exist: {image_path}")

        try:
            with Image.open(image_path) as source:
                image = ImageOps.exif_transpose(source).convert("RGB")
                x1 = max(0, min(region.x1, image.width))
                y1 = max(0, min(region.y1, image.height))
                x2 = max(x1, min(region.x2, image.width))
                y2 = max(y1, min(region.y2, image.height))
                crop = image.crop((x1, y1, x2, y2))
                internal, attempts = self._scanner.scan_crop_with_recovery_diagnostics(
                    crop,
                    offset_x=x1,
                    offset_y=y1,
                )

        except UnidentifiedImageError as exc:
            raise InvalidInputError(
                f"File is not a valid image: {image_path}"
            ) from exc
        except Exception as exc:
            raise ScannerError(
                f"Targeted barcode recovery failed for: {image_path}"
            ) from exc

        return TargetedRecoveryDiagnostics(
            result=ScanResult(
                barcodes=tuple(self._to_domain(detection) for detection in internal),
                image_width=image.width,
                image_height=image.height,
            ),
            attempts=tuple(attempts),
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
