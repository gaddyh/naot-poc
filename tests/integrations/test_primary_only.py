"""Tests for the PrimaryOnlyScanner wrapper.

Verifies that it filters to valid EAN-13 barcodes only, preserves image
dimensions, and correctly delegates recovery methods to the inner scanner.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

from naot_poc.domain.models import (
    BarcodeFormat,
    BoundingBox,
    DetectedBarcode,
    ScanResult,
)
from naot_poc.integrations.primary_only import PrimaryOnlyScanner


def _barcode(value: str, box=(10, 10, 30, 30)) -> DetectedBarcode:
    return DetectedBarcode(
        value=value,
        format=BarcodeFormat.CODE128,
        bounding_box=BoundingBox(*box),
    )


def _scan_result(barcodes, width=100, height=100) -> ScanResult:
    return ScanResult(
        barcodes=tuple(barcodes),
        image_width=width,
        image_height=height,
    )


class FakeInnerScanner:
    """Minimal scanner that returns a fixed result and records calls."""

    def __init__(self, result: ScanResult) -> None:
        self.result = result
        self.scan_calls = 0
        self.recover_calls = 0

    def scan(self, image_path: Path) -> ScanResult:
        self.scan_calls += 1
        return self.result

    def recover_region(self, image_path: Path, region: BoundingBox) -> ScanResult:
        self.recover_calls += 1
        return self.result

    def recover_region_diagnostics(self, image_path: Path, region: BoundingBox):
        self.recover_calls += 1
        return SimpleNamespace(
            result=self.result,
            attempts=(
                SimpleNamespace(
                    rotation=0,
                    scale=2.0,
                    preprocessing="clahe",
                    inverted=False,
                    values=(self.result.barcodes[0].value,) if self.result.barcodes else (),
                ),
            ),
        )


class FakeNoRecoveryScanner:
    """Scanner without recovery methods."""

    def __init__(self, result: ScanResult) -> None:
        self.result = result

    def scan(self, image_path: Path) -> ScanResult:
        return self.result


# --- scan() filtering ---


def test_scan_filters_to_valid_ean13_only():
    """Only valid 13-digit EAN-13 barcodes should survive the filter."""
    inner = FakeInnerScanner(_scan_result([
        _barcode("7297500243423"),  # valid EAN-13
        _barcode("900439-42"),      # not EAN-13 (has dashes)
        _barcode("123456789012"),   # 12 digits, not 13
        _barcode("7297500243424"),  # 13 digits but bad checksum
    ]))
    wrapper = PrimaryOnlyScanner(inner)

    result = wrapper.scan(Path("image.jpeg"))

    assert len(result.barcodes) == 1
    assert result.barcodes[0].value == "7297500243423"


def test_scan_preserves_image_dimensions():
    inner = FakeInnerScanner(_scan_result(
        [_barcode("7297500243423")],
        width=1920,
        height=1080,
    ))
    wrapper = PrimaryOnlyScanner(inner)

    result = wrapper.scan(Path("image.jpeg"))

    assert result.image_width == 1920
    assert result.image_height == 1080


def test_scan_with_all_invalid_returns_empty():
    inner = FakeInnerScanner(_scan_result([
        _barcode("900439-42"),
        _barcode("123"),
    ]))
    wrapper = PrimaryOnlyScanner(inner)

    result = wrapper.scan(Path("image.jpeg"))

    assert len(result.barcodes) == 0
    assert result.image_width == 100


def test_scan_delegates_to_inner():
    inner = FakeInnerScanner(_scan_result([_barcode("7297500243423")]))
    wrapper = PrimaryOnlyScanner(inner)

    wrapper.scan(Path("image.jpeg"))

    assert inner.scan_calls == 1


# --- recover_region() ---


def test_recover_region_filters_and_delegates():
    inner = FakeInnerScanner(_scan_result([
        _barcode("7297500243423"),
        _barcode("900439-42"),
    ]))
    wrapper = PrimaryOnlyScanner(inner)
    region = BoundingBox(10, 10, 50, 50)

    result = wrapper.recover_region(Path("image.jpeg"), region)

    assert inner.recover_calls == 1
    assert len(result.barcodes) == 1
    assert result.barcodes[0].value == "7297500243423"


def test_recover_region_raises_when_inner_lacks_recovery():
    inner = FakeNoRecoveryScanner(_scan_result([_barcode("7297500243423")]))
    wrapper = PrimaryOnlyScanner(inner)

    with pytest.raises(TypeError, match="does not support targeted recovery"):
        wrapper.recover_region(Path("image.jpeg"), BoundingBox(10, 10, 50, 50))


# --- recover_region_diagnostics() ---


def test_recover_region_diagnostics_filters_and_delegates():
    inner = FakeInnerScanner(_scan_result([
        _barcode("7297500243423"),
        _barcode("900439-42"),
    ]))
    wrapper = PrimaryOnlyScanner(inner)

    diagnostics = wrapper.recover_region_diagnostics(
        Path("image.jpeg"),
        BoundingBox(10, 10, 50, 50),
    )

    assert inner.recover_calls == 1
    assert len(diagnostics.result.barcodes) == 1
    assert diagnostics.result.barcodes[0].value == "7297500243423"
    # Attempts should be preserved
    assert len(diagnostics.attempts) == 1


def test_recover_region_diagnostics_raises_when_inner_lacks_recovery():
    inner = FakeNoRecoveryScanner(_scan_result([_barcode("7297500243423")]))
    wrapper = PrimaryOnlyScanner(inner)

    with pytest.raises(TypeError, match="does not support recovery diagnostics"):
        wrapper.recover_region_diagnostics(
            Path("image.jpeg"),
            BoundingBox(10, 10, 50, 50),
        )
