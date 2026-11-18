"""At most a few applications per company, counted across every way the student applies.

A pile of applications at one company doesn't raise the odds of hearing back from
it: one recruiter often reads them all. So once a company has had
`most_applications_per_company` applications (3 unless the settings say
otherwise; 0 turns the limit off), automatic runs leave its other postings for
other companies.

Counted, each posting once:
  - Quick Apply sends on Handshake (data/applied.json),
  - forms sent on employers' own sites, including ones that went through
    unconfirmed or wait on an emailed code (data/employer_applications.json),
  - postings the student marked as applied themselves (data/applied_myself.json).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from deep_rank import company_key

DEFAULT_MOST = 3
# "quadric.io" and "Quadric" are one company,
WEB_ENDING = re.compile(r"\.(io|ai|com|co|tech|inc)\b", re.I)
# and so are "Qorvo, now part of Skyworks" and "Qorvo", "Southwest Research Institute (SwRI)" and the name alone,
ASIDE = re.compile(r"\s*\([^)]*\)|,?\s+(now )?(part of|a subsidiary of|an? \w+ company)\b.*$", re.I)
# and "Anduril Industries", "MACOM Technology Solutions", "Persona AI" and their short names.
GENERIC_END = re.compile(r"(\s+(industries|technologies|technology solutions|technology|ai|energy|labs?))+$")


def key(employer: str) -> str:
    name = company_key(WEB_ENDING.sub(" ", ASIDE.sub("", employer or "")))
    return GENERIC_END.sub("", name) or name


def _read(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def sent_on_site(entry: dict[str, Any]) -> bool:
    """Sent, or filled in and waiting only on the code its site emails."""
    status = str(entry.get("status", ""))
    return status in ("applied", "uncertain") or (
        status == "needs_manual" and str(entry.get("note", "")).startswith("the site wanted"))


class CompanyCap:
    """How many applications each company has had, and whether it's had enough."""

    def __init__(self, data_dir: Path, most: int = DEFAULT_MOST) -> None:
        self.most = max(0, int(most))
        sent: dict[str, str] = {}  # posting id -> employer, so one posting counts once
        for job_id, entry in _read(data_dir / "applied.json").items():
            if entry.get("status") == "applied":
                sent.setdefault(job_id, str(entry.get("employer", "")))
        for job_id, entry in _read(data_dir / "employer_applications.json").items():
            if sent_on_site(entry):
                sent.setdefault(job_id, str(entry.get("employer", "")))
        for job_id, entry in _read(data_dir / "applied_myself.json").items():
            sent.setdefault(job_id, str(entry.get("employer", "")))
        self.ids = set(sent)
        self.counts: dict[str, int] = {}
        for employer in sent.values():
            if employer.strip():
                self.counts[key(employer)] = self.counts.get(key(employer), 0) + 1

    @classmethod
    def from_config(cls, config: dict[str, Any], data_dir: Path) -> "CompanyCap":
        try:
            most = int(config.get("most_applications_per_company", DEFAULT_MOST))
        except (TypeError, ValueError):
            most = DEFAULT_MOST
        return cls(data_dir, most)

    def count(self, employer: str) -> int:
        return self.counts.get(key(employer), 0)

    def full(self, employer: str) -> bool:
        return bool(self.most) and bool((employer or "").strip()) and self.count(employer) >= self.most

    def add(self, employer: str, job_id: str = "") -> None:
        """One more sent this run."""
        if job_id and job_id in self.ids:
            return
        if job_id:
            self.ids.add(job_id)
        if (employer or "").strip():
            self.counts[key(employer)] = self.count(employer) + 1

    def why(self, employer: str) -> str:
        return (f"{employer} has had {self.count(employer)} of your applications already, the most per company "
                f"(\"most_applications_per_company\" in the settings)")

    def companies_full(self) -> int:
        return sum(1 for n in self.counts.values() if self.most and n >= self.most)
