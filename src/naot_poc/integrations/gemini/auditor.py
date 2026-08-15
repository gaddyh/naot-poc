from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

DEFAULT_CACHE_DIR = Path("evaluation/.gemini_cache")


class GeminiSpatialAuditor:
    """Lazy adapter around the optional Gemini spatial-label audit.

    When ``cache_dir`` is set, audit responses are cached per-image on disk
    so repeated runs produce identical results without calling the Gemini API.
    Pass ``refresh=True`` to ignore the cache and re-fetch.
    """

    def __init__(
        self,
        *,
        cache_dir: Path | None = DEFAULT_CACHE_DIR,
        refresh: bool = False,
        **kwargs: Any,
    ) -> None:
        self._cache_dir = cache_dir
        self._refresh = refresh
        self._kwargs = kwargs

    async def audit(self, image_path: Path) -> Any:
        from naot_poc.integrations.gemini.vision import (
            SpatialLabelAuditPixels,
            audit_shoebox_labels_async,
        )

        cache_path = self._cache_path(image_path)

        if not self._refresh and cache_path is not None and cache_path.exists():
            return SpatialLabelAuditPixels.model_validate_json(
                cache_path.read_text()
            )

        result = await audit_shoebox_labels_async(image_path, **self._kwargs)

        if cache_path is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(result.model_dump_json(indent=2))

        return result

    def _cache_path(self, image_path: Path) -> Path | None:
        if self._cache_dir is None:
            return None
        resolved = Path(image_path).resolve()
        stat = resolved.stat()
        key = hashlib.sha256(
            f"{resolved}:{stat.st_size}:{stat.st_mtime}".encode()
        ).hexdigest()[:16]
        return self._cache_dir / f"{resolved.stem}_{key}.json"
