"""Postings the student applied to on their own, marked from the ranked pages.

Kept in data/applied_myself.json. Marked postings are hidden on every page and
left out of later rankings and daily finds.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def path() -> Path:
    return Path(os.environ.get("HSBOT_DATA_DIR") or HERE / "data") / "applied_myself.json"


def load() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def ids() -> set[str]:
    return set(load())


def _save(entries: dict[str, dict[str, Any]]) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(entries, indent=1, ensure_ascii=False), encoding="utf-8")


def mark(job_id: str, info: dict[str, Any] | None = None) -> None:
    entries = load()
    entry = {k: str(v)[:300] for k, v in (info or {}).items() if k in ("title", "employer", "url", "page")}
    entry["applied_on"] = datetime.now().isoformat(timespec="seconds")
    entries[job_id] = entry
    _save(entries)


def unmark(job_id: str) -> None:
    entries = load()
    if entries.pop(job_id, None) is not None:
        _save(entries)
