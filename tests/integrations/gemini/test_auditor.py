"""Tests for the GeminiSpatialAuditor cache logic.

Tests cache hit, cache miss, refresh, disabled caching, and cache key
invalidation — all without calling the real Gemini API.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from naot_poc.integrations.gemini.auditor import GeminiSpatialAuditor


def _fake_audit_result():
    """A minimal audit result that supports model_validate_json."""
    return SimpleNamespace(
        labels=[],
        model_dump_json=lambda indent=2: '{"labels": []}',
    )


def _mock_model_validate_json(data):
    """Mock for pydantic model_validate_json."""
    return SimpleNamespace(labels=[])


async def test_cache_miss_calls_api_and_stores_result(tmp_path):
    """On first call, the API should be invoked and the result cached."""
    cache_dir = tmp_path / "cache"
    auditor = GeminiSpatialAuditor(cache_dir=cache_dir)

    fake_result = _fake_audit_result()

    with patch(
        "naot_poc.integrations.gemini.vision.audit_shoebox_labels_async",
        new_callable=AsyncMock,
        return_value=fake_result,
    ), patch(
        "naot_poc.integrations.gemini.vision.SpatialLabelAuditPixels"
    ) as mock_model:
        mock_model.model_validate_json = _mock_model_validate_json

        image = tmp_path / "test.jpeg"
        image.write_bytes(b"fake image data")

        result = await auditor.audit(image)

    assert result is fake_result
    # Cache file should exist
    cache_files = list(cache_dir.glob("*.json"))
    assert len(cache_files) == 1


async def test_cache_hit_returns_cached_without_api_call(tmp_path):
    """On second call, the cached result should be returned without API."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    auditor = GeminiSpatialAuditor(cache_dir=cache_dir)

    image = tmp_path / "test.jpeg"
    image.write_bytes(b"fake image data")

    # Pre-populate cache
    stat = image.stat()
    import hashlib
    key = hashlib.sha256(
        f"{image.resolve()}:{stat.st_size}:{stat.st_mtime}".encode()
    ).hexdigest()[:16]
    cache_path = cache_dir / f"{image.stem}_{key}.json"
    cache_path.write_text('{"labels": []}')

    with patch(
        "naot_poc.integrations.gemini.vision.audit_shoebox_labels_async",
        new_callable=AsyncMock,
    ) as mock_api, patch(
        "naot_poc.integrations.gemini.vision.SpatialLabelAuditPixels"
    ) as mock_model:
        mock_model.model_validate_json = _mock_model_validate_json

        result = await auditor.audit(image)

    # API should not have been called
    mock_api.assert_not_called()
    assert result is not None


async def test_refresh_ignores_cache_and_refetches(tmp_path):
    """With refresh=True, the cache should be ignored and the API called."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir(parents=True)
    auditor = GeminiSpatialAuditor(cache_dir=cache_dir, refresh=True)

    image = tmp_path / "test.jpeg"
    image.write_bytes(b"fake image data")

    # Pre-populate cache
    stat = image.stat()
    import hashlib
    key = hashlib.sha256(
        f"{image.resolve()}:{stat.st_size}:{stat.st_mtime}".encode()
    ).hexdigest()[:16]
    cache_path = cache_dir / f"{image.stem}_{key}.json"
    cache_path.write_text('{"labels": []}')

    fake_result = _fake_audit_result()

    with patch(
        "naot_poc.integrations.gemini.vision.audit_shoebox_labels_async",
        new_callable=AsyncMock,
        return_value=fake_result,
    ) as mock_api, patch(
        "naot_poc.integrations.gemini.vision.SpatialLabelAuditPixels"
    ) as mock_model:
        mock_model.model_validate_json = _mock_model_validate_json

        result = await auditor.audit(image)

    mock_api.assert_called_once()
    assert result is fake_result


async def test_cache_dir_none_disables_caching(tmp_path):
    """With cache_dir=None, no cache files should be created."""
    auditor = GeminiSpatialAuditor(cache_dir=None)

    fake_result = _fake_audit_result()
    image = tmp_path / "test.jpeg"
    image.write_bytes(b"fake image data")

    with patch(
        "naot_poc.integrations.gemini.vision.audit_shoebox_labels_async",
        new_callable=AsyncMock,
        return_value=fake_result,
    ), patch(
        "naot_poc.integrations.gemini.vision.SpatialLabelAuditPixels"
    ) as mock_model:
        mock_model.model_validate_json = _mock_model_validate_json

        result = await auditor.audit(image)

    assert result is fake_result
    # No cache directory should exist
    assert not (tmp_path / "cache").exists()


async def test_cache_key_includes_mtime(tmp_path):
    """Cache key should change when file mtime changes, invalidating stale cache."""
    cache_dir = tmp_path / "cache"
    auditor = GeminiSpatialAuditor(cache_dir=cache_dir)

    image = tmp_path / "test.jpeg"
    image.write_bytes(b"fake image data")

    fake_result = _fake_audit_result()

    # First call — populates cache
    with patch(
        "naot_poc.integrations.gemini.vision.audit_shoebox_labels_async",
        new_callable=AsyncMock,
        return_value=fake_result,
    ) as mock_api, patch(
        "naot_poc.integrations.gemini.vision.SpatialLabelAuditPixels"
    ) as mock_model:
        mock_model.model_validate_json = _mock_model_validate_json
        await auditor.audit(image)

    assert mock_api.call_count == 1

    # Modify file mtime (same size, different mtime) to invalidate cache
    import os
    old_mtime = image.stat().st_mtime
    os.utime(image, (old_mtime + 100, old_mtime + 100))

    # Second call — should miss cache due to mtime change
    with patch(
        "naot_poc.integrations.gemini.vision.audit_shoebox_labels_async",
        new_callable=AsyncMock,
        return_value=fake_result,
    ) as mock_api2, patch(
        "naot_poc.integrations.gemini.vision.SpatialLabelAuditPixels"
    ) as mock_model:
        mock_model.model_validate_json = _mock_model_validate_json
        await auditor.audit(image)

    assert mock_api2.call_count == 1
