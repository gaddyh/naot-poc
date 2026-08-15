"""Tests for the MultiPassZXingScanner adapter.

Tests the adapter boundary between the internal enhanced_scanner algorithm and
the domain BarcodeScanner port — format mapping, error translation, and
recovery delegation. The internal scanner is mocked to avoid needing real
images or zxing-cpp.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from naot_poc.domain.errors import InvalidInputError, ScannerError
from naot_poc.domain.models import BarcodeFormat, BoundingBox, ScanResult
from naot_poc.integrations.zxing.multipass import (
    MultiPassZXingScanner,
    TargetedRecoveryDiagnostics,
)

# --- Helpers ---


def _internal_detection(
    value="7297500243423",
    fmt="Code128",
    box=(10, 10, 50, 20),
    orientation=0,
):
    return SimpleNamespace(
        value=value,
        format=fmt,
        bounding_box=SimpleNamespace(x1=box[0], y1=box[1], x2=box[2], y2=box[3]),
        orientation=orientation,
    )


def _internal_attempt(
    rotation=0,
    scale=2.0,
    preprocessing="clahe",
    inverted=False,
    values=("7297500243423",),
):
    return SimpleNamespace(
        rotation=rotation,
        scale=scale,
        preprocessing=preprocessing,
        inverted=inverted,
        values=values,
    )


def _make_scanner(internal_detections=(), recovery_detections=(), recovery_attempts=()):
    """Build a MultiPassZXingScanner with a mocked internal scanner."""
    with patch(
        "naot_poc.integrations.zxing.multipass._InternalBarcodeScanner"
    ) as mock_cls:
        mock_internal = MagicMock()
        mock_internal.scan_image.return_value = list(internal_detections)
        mock_internal.scan_crop_with_recovery_diagnostics.return_value = (
            list(recovery_detections),
            list(recovery_attempts),
        )
        mock_cls.return_value = mock_internal
        scanner = MultiPassZXingScanner()
    return scanner, mock_internal


def _fake_image(width=200, height=100):
    """Create a mock PIL Image context manager."""
    mock_img = MagicMock()
    mock_img.size = (width, height)
    mock_img.__enter__ = MagicMock(return_value=mock_img)
    mock_img.__exit__ = MagicMock(return_value=False)
    return mock_img


def _make_image_file(tmp_path):
    """Create a real temp file so image_path.exists() passes."""
    image_path = tmp_path / "test_image.jpeg"
    image_path.write_bytes(b"fake image data")
    return image_path


# --- scan() ---


def test_scan_returns_domain_scan_result(tmp_path):
    det = _internal_detection()
    scanner, _ = _make_scanner(internal_detections=[det])
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod:
        mock_image_mod.open.return_value = _fake_image()
        mock_image_mod.UnidentifiedImageError = Exception
        result = scanner.scan(image_path)

    assert isinstance(result, ScanResult)
    assert result.image_width == 200
    assert result.image_height == 100
    assert len(result.barcodes) == 1


def test_scan_maps_code128_format(tmp_path):
    det = _internal_detection(fmt="Code128")
    scanner, _ = _make_scanner(internal_detections=[det])
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod:
        mock_image_mod.open.return_value = _fake_image()
        mock_image_mod.UnidentifiedImageError = Exception
        result = scanner.scan(image_path)

    assert result.barcodes[0].format == BarcodeFormat.CODE128


def test_scan_maps_ean13_format(tmp_path):
    det = _internal_detection(fmt="EAN-13")
    scanner, _ = _make_scanner(internal_detections=[det])
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod:
        mock_image_mod.open.return_value = _fake_image()
        mock_image_mod.UnidentifiedImageError = Exception
        result = scanner.scan(image_path)

    assert result.barcodes[0].format == BarcodeFormat.EAN13


def test_scan_unknown_format_maps_to_unknown(tmp_path):
    det = _internal_detection(fmt="DataMatrix")
    scanner, _ = _make_scanner(internal_detections=[det])
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod:
        mock_image_mod.open.return_value = _fake_image()
        mock_image_mod.UnidentifiedImageError = Exception
        result = scanner.scan(image_path)

    assert result.barcodes[0].format == BarcodeFormat.UNKNOWN


def test_scan_preserves_bounding_box_and_orientation(tmp_path):
    det = _internal_detection(box=(15, 25, 55, 35), orientation=90)
    scanner, _ = _make_scanner(internal_detections=[det])
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod:
        mock_image_mod.open.return_value = _fake_image()
        mock_image_mod.UnidentifiedImageError = Exception
        result = scanner.scan(image_path)

    box = result.barcodes[0].bounding_box
    assert box.x1 == 15
    assert box.y1 == 25
    assert box.x2 == 55
    assert box.y2 == 35
    assert result.barcodes[0].orientation == 90


def test_scan_nonexistent_file_raises_invalid_input():
    scanner, _ = _make_scanner()

    with pytest.raises(InvalidInputError, match="does not exist"):
        scanner.scan(Path("/nonexistent/image.jpeg"))


def test_scan_unidentified_image_raises_invalid_input(tmp_path):
    scanner, _ = _make_scanner()
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod:
        from PIL import UnidentifiedImageError as UIE
        mock_image_mod.open.return_value.__enter__.side_effect = UIE("bad image")
        mock_image_mod.UnidentifiedImageError = UIE
        with pytest.raises(InvalidInputError, match="not a valid image"):
            scanner.scan(image_path)


def test_scan_generic_exception_raises_scanner_error(tmp_path):
    scanner, _ = _make_scanner()
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod:
        mock_image_mod.open.return_value.__enter__.side_effect = RuntimeError("boom")
        mock_image_mod.UnidentifiedImageError = Exception
        with pytest.raises(ScannerError, match="scan failed"):
            scanner.scan(image_path)


# --- recover_region() ---


def _setup_recovery_image_mock(mock_image_mod, mock_imageops_mod, width=200, height=100):
    """Set up Image + ImageOps mocks for the recover_region code path.

    The code does: Image.open(path) -> ctx -> ImageOps.exif_transpose(src).convert("RGB")
    -> image.width/height/crop(). We need the convert() result to have width/height/crop.
    """
    mock_source = MagicMock()
    mock_source.__enter__ = MagicMock(return_value=mock_source)
    mock_source.__exit__ = MagicMock(return_value=False)

    mock_transposed = MagicMock()
    mock_converted = MagicMock()
    mock_converted.width = width
    mock_converted.height = height
    mock_converted.crop.return_value = MagicMock()  # crop result passed to scanner

    mock_transposed.convert.return_value = mock_converted
    mock_image_mod.open.return_value = mock_source
    mock_image_mod.UnidentifiedImageError = Exception
    mock_imageops_mod.exif_transpose.return_value = mock_transposed
    return mock_converted


def test_recover_region_delegates_to_diagnostics(tmp_path):
    det = _internal_detection()
    attempt = _internal_attempt()
    scanner, mock_internal = _make_scanner(
        recovery_detections=[det],
        recovery_attempts=[attempt],
    )
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod, \
         patch("naot_poc.integrations.zxing.multipass.ImageOps") as mock_imageops_mod:
        _setup_recovery_image_mock(mock_image_mod, mock_imageops_mod)

        result = scanner.recover_region(image_path, BoundingBox(10, 10, 50, 50))

    assert isinstance(result, ScanResult)
    assert len(result.barcodes) == 1
    assert mock_internal.scan_crop_with_recovery_diagnostics.called


def test_recover_region_diagnostics_returns_diagnostics(tmp_path):
    det = _internal_detection()
    attempt = _internal_attempt()
    scanner, _ = _make_scanner(
        recovery_detections=[det],
        recovery_attempts=[attempt],
    )
    image_path = _make_image_file(tmp_path)

    with patch("naot_poc.integrations.zxing.multipass.Image") as mock_image_mod, \
         patch("naot_poc.integrations.zxing.multipass.ImageOps") as mock_imageops_mod:
        _setup_recovery_image_mock(mock_image_mod, mock_imageops_mod)

        diagnostics = scanner.recover_region_diagnostics(
            image_path,
            BoundingBox(10, 10, 50, 50),
        )

    assert isinstance(diagnostics, TargetedRecoveryDiagnostics)
    assert len(diagnostics.attempts) == 1
    assert diagnostics.result.barcodes[0].value == "7297500243423"


def test_recover_region_nonexistent_file_raises_invalid_input():
    scanner, _ = _make_scanner()

    with pytest.raises(InvalidInputError, match="does not exist"):
        scanner.recover_region(Path("/nonexistent/image.jpeg"), BoundingBox(10, 10, 50, 50))
