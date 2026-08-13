from pathlib import Path

import pytest

from naot_poc.domain.errors import InvalidInputError
from naot_poc.integrations.zxing import ZXingBarcodeScanner

SAMPLES_DIR = Path(__file__).resolve().parents[3] / "samples"


def test_scans_known_barcode_image():
    scanner = ZXingBarcodeScanner()

    result = scanner.scan(SAMPLES_DIR / "multi_clear_6_boxes.jpeg")

    assert result.image_width > 0
    assert result.image_height > 0
    assert len(result.barcodes) > 0

    values = {barcode.value for barcode in result.barcodes}

    assert "7297500243423" in values


def test_missing_image_raises_invalid_input():
    scanner = ZXingBarcodeScanner()

    with pytest.raises(InvalidInputError):
        scanner.scan(SAMPLES_DIR / "does_not_exist.jpeg")
