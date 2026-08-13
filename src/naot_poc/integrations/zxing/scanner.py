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


class ZXingBarcodeScanner:
    def scan(self, image_path: Path) -> ScanResult:
        if not image_path.exists():
            raise InvalidInputError(
                f"Image does not exist: {image_path}"
            )

        try:
            with Image.open(image_path) as image:
                width, height = image.size
                results = zxingcpp.read_barcodes(image)

        except UnidentifiedImageError as exc:
            raise InvalidInputError(
                f"File is not a valid image: {image_path}"
            ) from exc

        except Exception as exc:
            raise ScannerError(
                f"Barcode scan failed for: {image_path}"
            ) from exc

        barcodes = tuple(
            DetectedBarcode(
                value=result.text,
                format=self._map_format(result.format),
                bounding_box=self._to_bounding_box(result),
            )
            for result in results
        )

        return ScanResult(
            barcodes=barcodes,
            image_width=width,
            image_height=height,
        )

    @staticmethod
    def _map_format(format_: zxingcpp.BarcodeFormat) -> BarcodeFormat:
        mapping = {
            zxingcpp.BarcodeFormat.EAN13: BarcodeFormat.EAN13,
            zxingcpp.BarcodeFormat.Code128: BarcodeFormat.CODE128,
            zxingcpp.BarcodeFormat.QRCode: BarcodeFormat.QR_CODE,
        }

        return mapping.get(format_, BarcodeFormat.UNKNOWN)

    @staticmethod
    def _to_bounding_box(result) -> BoundingBox:
        points = [
            result.position.top_left,
            result.position.top_right,
            result.position.bottom_right,
            result.position.bottom_left,
        ]

        xs = [point.x for point in points]
        ys = [point.y for point in points]

        return BoundingBox(
            x1=min(xs),
            y1=min(ys),
            x2=max(xs),
            y2=max(ys),
        )
