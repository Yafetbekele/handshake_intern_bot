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


MANUAL_FOLDER_NAME = "Internships to apply to yourself"


class ManualList:
    """Good internships the tool could not apply to, kept across runs.

    Lives in its own folder so it is easy to find. Each save writes a page with
    clickable links (apply_yourself.html), a spreadsheet copy
    (apply_yourself.csv), and the data behind them (list.json). A posting drops
    off the list once the tool applies to it.
    """

    def __init__(self, folder: str | Path) -> None:
        self.folder = Path(folder)
        self.json_path = self.folder / "list.json"
        self.csv_path = self.folder / "apply_yourself.csv"
        self.html_path = self.folder / "apply_yourself.html"
        self._items: dict[str, dict[str, Any]] = {}
        try:
            raw = json.loads(self.json_path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._items = raw
        except (OSError, json.JSONDecodeError):
            pass

    def __len__(self) -> int:
        return len(self._items)

    def add(
        self,
        job_id: str,
        title: str,
        employer: str,
        location: str,
        url: str,
        score: float,
        reason: str,
        resume_path: str = "",
    ) -> None:
        now = datetime.now().isoformat(timespec="seconds")
        existing = self._items.get(job_id, {})
        self._items[job_id] = {
            "job_id": job_id,
            "title": title or existing.get("title", ""),
            "employer": employer or existing.get("employer", ""),
            "location": location or existing.get("location", ""),
            "url": url or existing.get("url", ""),
            "score": round(max(float(score), float(existing.get("score", 0))), 4),
            "reason": reason,
            "resume": resume_path or existing.get("resume", ""),
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
        }

    def remove(self, job_id: str) -> None:
        self._items.pop(job_id, None)

    def items(self) -> list[dict[str, Any]]:
        return sorted(self._items.values(), key=lambda r: (-float(r.get("score", 0)), r.get("title", "")))

    def save(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        rows = self.items()
        self.json_path.write_text(json.dumps(self._items, indent=2, sort_keys=True), encoding="utf-8")

        fields = ["score_percent", "title", "employer", "location", "reason", "url", "resume", "first_seen", "last_seen"]
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row, score_percent=round(float(row.get("score", 0)) * 100)))

        self.html_path.write_text(_manual_list_html(rows, self.folder), encoding="utf-8")
        return self.html_path


def _manual_list_html(rows: list[dict[str, Any]], folder: Path) -> str:
    import os
    from html import escape

    body = []
    for row in rows:
        percent = round(float(row.get("score", 0)) * 100)
        url = escape(str(row.get("url", "")), quote=True)
        job_id = escape(str(row.get("job_id", "")), quote=True)
        resume_cell = ""
        resume = str(row.get("resume", ""))
        if resume and Path(resume).exists():
            try:
                link = os.path.relpath(resume, folder).replace(os.sep, "/")
            except ValueError:  # on a different drive
                link = Path(resume).resolve().as_uri()
            resume_cell = f"<a href='{escape(link, quote=True)}'>Open</a>"
        body.append(
            f"<tr data-id='{job_id}'>"
            f"<td class='num'>{percent}%</td>"
            f"<td><a href='{url}' target='_blank' rel='noopener'>{escape(str(row.get('title', '')))}</a></td>"
            f"<td>{escape(str(row.get('employer', '')))}</td>"
            f"<td>{escape(str(row.get('location', '')))}</td>"
            f"<td>{escape(str(row.get('reason', '')))}</td>"
            f"<td>{resume_cell}</td>"
            f"<td class='when'>{escape(str(row.get('first_seen', ''))[:10])}</td>"
            "<td class='act'><button type='button' class='remove'>Remove</button></td>"
            "</tr>"
        )
    table = "\n".join(body) or "<tr><td colspan='8'>Nothing here yet.</td></tr>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internships to apply to yourself</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6068; --line:#e3e3e8; --bg:#fff; --accent:#1f5fd6; --soft:#f3f3f6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ececf1; --muted:#a4a5ad; --line:#34343b; --bg:#17171b; --accent:#8ab4ff; --soft:#232329; }} }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:1160px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p {{ color:var(--muted); margin:0 0 12px; }}
  .bar {{ display:flex; gap:14px; align-items:center; flex-wrap:wrap; margin:0 0 14px; color:var(--muted); font-size:14px; }}
  .wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; }}
  th, td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  a {{ color:var(--accent); font-weight:600; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .when {{ color:var(--muted); white-space:nowrap; }}
  .act {{ white-space:nowrap; }}
  button {{ font:inherit; font-size:13px; color:var(--fg); background:var(--soft); border:1px solid var(--line); border-radius:6px; padding:4px 10px; cursor:pointer; }}
  button:hover {{ border-color:var(--muted); }}
  .linkish {{ background:none; border:none; padding:0; color:var(--accent); font-weight:600; }}
  tr.removed {{ display:none; }}
  body.show-removed tr.removed {{ display:table-row; opacity:.5; }}
</style></head>
<body><main>
<h1>Internships to apply to yourself</h1>
<p>{len(rows)} good matches the assistant could not apply to for you, best match first. Each link opens the posting on Handshake.</p>
<div class="bar">
  <span id="count"></span>
  <button type="button" class="linkish" id="toggle" hidden>Show removed</button>
</div>
<div class="wrap"><table>
<thead><tr><th>Match</th><th>Internship</th><th>Employer</th><th>Location</th><th>Why it's here</th><th>Tailored resume</th><th>Found</th><th></th></tr></thead>
<tbody>
{table}
</tbody></table></div>
</main>
<script>
(function () {{
  // Removed internships are remembered in this browser, so they stay hidden
  // when the page is reopened or rebuilt by a later run.
  var KEY = 'hsbot-removed-internships';
  var removed = {{}};
  try {{ removed = JSON.parse(localStorage.getItem(KEY) || '{{}}') || {{}}; }} catch (e) {{ removed = {{}}; }}
  function save() {{ try {{ localStorage.setItem(KEY, JSON.stringify(removed)); }} catch (e) {{}} }}

  var rows = Array.prototype.slice.call(document.querySelectorAll('tbody tr[data-id]'));
  var toggle = document.getElementById('toggle');
  var count = document.getElementById('count');

  function render() {{
    var hidden = 0;
    rows.forEach(function (row) {{
      var gone = !!removed[row.getAttribute('data-id')];
      row.classList.toggle('removed', gone);
      row.querySelector('button.remove').textContent = gone ? 'Restore' : 'Remove';
      if (gone) hidden++;
    }});
    count.textContent = (rows.length - hidden) + ' shown' + (hidden ? ', ' + hidden + ' removed' : '');
    toggle.hidden = hidden === 0;
    if (hidden === 0) document.body.classList.remove('show-removed');
    toggle.textContent = document.body.classList.contains('show-removed') ? 'Hide removed' : 'Show removed';
  }}

  rows.forEach(function (row) {{
    row.querySelector('button.remove').addEventListener('click', function () {{
      var id = row.getAttribute('data-id');
      if (removed[id]) {{ delete removed[id]; }} else {{ removed[id] = new Date().toISOString().slice(0, 10); }}
      save();
      render();
    }});
  }});
  toggle.addEventListener('click', function () {{
    document.body.classList.toggle('show-removed');
    render();
  }});
  render();
}})();
</script>
</body></html>
"""
