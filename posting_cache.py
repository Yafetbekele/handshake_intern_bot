"""A local copy of every posting the tool reads, so it can be ranked later.

Each posting is one JSON file in data/postings/<job id>.json with its title,
employer, location, link, how it's applied to, and the full description.
Nothing here opens Handshake; see rank_all.py for filling in missing ones.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def folder() -> Path:
    data = Path(os.environ.get("HSBOT_DATA_DIR") or HERE / "data")
    return data / "postings"


def path_for(job_id: str) -> Path:
    return folder() / f"{job_id}.json"


def has(job_id: str) -> bool:
    return path_for(job_id).exists()


def save(job: Any) -> None:
    """Keep a copy of a posting that was just read. Never fails a run."""
    if not getattr(job, "job_id", "") or not getattr(job, "title", ""):
        return
    try:
        folder().mkdir(parents=True, exist_ok=True)
        path_for(job.job_id).write_text(
            json.dumps(
                {
                    "job_id": job.job_id,
                    "title": job.title,
                    "employer": job.employer,
                    "location": job.location,
                    "url": job.url,
                    "apply_kind": job.apply_kind,
                    "description": job.description,
                    "read_on": datetime.now().isoformat(timespec="seconds"),
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
    except OSError:
        pass


def load(job_id: str) -> dict[str, Any] | None:
    try:
        return json.loads(path_for(job_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def import_tailored(tailored_dir: Path) -> int:
    """Postings saved next to tailored resumes before this store existed."""
    added = 0
    for posting in tailored_dir.glob("*/job_posting.txt"):
        job_id = posting.parent.name.split("-", 1)[0]
        if not job_id.isdigit() or has(job_id):
            continue
        try:
            title, employer, _, *rest = posting.read_text(encoding="utf-8").split("\n")
        except (OSError, ValueError):
            continue

        class _Job:
            pass

        job = _Job()
        job.job_id, job.title, job.employer = job_id, title, employer
        job.location, job.url, job.apply_kind = "", "", "external"
        job.description = "\n".join(rest)
        save(job)
        added += 1
    return added
