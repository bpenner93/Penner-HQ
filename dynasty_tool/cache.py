"""A tiny, dependency-free disk cache shared by the Sleeper client and the
DynastyProcess value loader.

Everything is stored as UTF-8 text files under a single root so a full run can
be replayed offline. ``refresh=True`` forces every read to miss (re-fetch).
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Optional

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_key(key: str) -> str:
    return _SAFE.sub("_", key)


class DiskCache:
    def __init__(self, root: Path | str, refresh: bool = False) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh

    def path(self, key: str) -> Path:
        return self.root / _safe_key(key)

    def fresh(self, key: str, max_age_hours: Optional[float] = None) -> bool:
        """True if a cached value exists and is young enough to reuse."""
        if self.refresh:
            return False
        p = self.path(key)
        if not p.exists():
            return False
        if max_age_hours is None:
            return True
        age_h = (time.time() - p.stat().st_mtime) / 3600.0
        return age_h < max_age_hours

    # --- text ---------------------------------------------------------------
    def get_text(self, key: str) -> str:
        return self.path(key).read_text(encoding="utf-8")

    def put_text(self, key: str, text: str) -> None:
        self.path(key).write_text(text, encoding="utf-8")

    # --- invalidation -------------------------------------------------------
    def invalidate(self, prefix: str = "") -> int:
        """Delete cached entries whose key starts with ``prefix``; returns the
        count removed.

        Needed because clearing Streamlit's in-memory cache does not touch this
        layer — a "refresh" that only cleared the former would re-read the same
        stale bytes off disk and look like it had done nothing.
        """
        removed = 0
        safe = _safe_key(prefix) if prefix else ""
        for p in self.root.glob("*"):
            if p.is_file() and (not safe or p.name.startswith(safe)):
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    # --- json ---------------------------------------------------------------
    def get_json(self, key: str) -> Any:
        return json.loads(self.get_text(key))

    def put_json(self, key: str, obj: Any) -> None:
        # compact but stable; these are machine-read caches, not for humans
        self.put_text(key, json.dumps(obj, ensure_ascii=False))
