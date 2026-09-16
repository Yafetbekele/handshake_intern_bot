"""Local ledger of everything the tool has looked at or applied to.

Keeps the tool from re-applying to the same posting across runs and gives the
student a record of what went out.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


class Ledger:
    def __init__(self, path: str | Path = "data/applied.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            print(f"[warn] Could not read {self.path}; starting a fresh ledger.")
            return
        if isinstance(raw, dict):
            self._records = raw

    def _save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self._records, indent=2, sort_keys=True), encoding="utf-8"
        )
        tmp.replace(self.path)

    def seen(self, job_id: str) -> bool:
        return job_id in self._records

    def applied(self, job_id: str) -> bool:
        record = self._records.get(job_id)
        return bool(record and record.get("status") == "applied")

    def status_of(self, job_id: str) -> str:
        record = self._records.get(job_id)
        return record.get("status", "") if record else ""

    def record(
        self,
        job_id: str,
        status: str,
        title: str = "",
        employer: str = "",
        location: str = "",
        url: str = "",
        score: float = 0.0,
        note: str = "",
    ) -> None:
        self._records[job_id] = {
            "job_id": job_id,
            "status": status,
            "title": title,
            "employer": employer,
            "location": location,
            "url": url,
            "score": round(score, 4),
            "note": note,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()

    def applied_today(self) -> int:
        today = date.today().isoformat()
        return sum(
            1
            for r in self._records.values()
            if r.get("status") == "applied"
            and str(r.get("timestamp", "")).startswith(today)
        )

    def total_applied(self) -> int:
        return sum(1 for r in self._records.values() if r.get("status") == "applied")

    def export_csv(self, out_path: str | Path) -> Path:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "timestamp", "status", "score", "title", "employer",
            "location", "url", "note", "job_id",
        ]
        rows = sorted(
            self._records.values(),
            key=lambda r: str(r.get("timestamp", "")),
            reverse=True,
        )
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return out


def write_followups(rows: list[dict[str, Any]], out_path: str | Path) -> Path:
    """Write matches the student has to finish by hand to CSV."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["score_percent", "title", "employer", "location", "reason", "url"]
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return out


def write_report(rows: list[dict[str, Any]], out_path: str | Path) -> Path:
    """Write a ranked list of candidate jobs to CSV."""
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "rank", "score_percent", "title", "employer", "location",
        "apply_kind", "url", "top_matches",
    ]
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return out
