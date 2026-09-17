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
    off the list once the tool applies to it. Postings the student removes are
    kept in removed.json so later runs don't add them back.

    Rows are ordered by their fit score (see ranking.py), and only the best
    `limit` are kept.
    """

    def __init__(self, folder: str | Path, limit: int = 50) -> None:
        self.folder = Path(folder)
        self.limit = limit
        self.json_path = self.folder / "list.json"
        self.csv_path = self.folder / "apply_yourself.csv"
        self.html_path = self.folder / "apply_yourself.html"
        self.removed_path = self.folder / "removed.json"
        self._items = self._read(self.json_path)
        self._removed = self._read(self.removed_path)

    @staticmethod
    def _read(path: Path) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

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
        fit: float | None = None,
        why: list[str] | None = None,
    ) -> None:
        if job_id in self._removed:
            return
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
            "fit": float(fit) if fit is not None else existing.get("fit"),
            "why": list(why) if why is not None else existing.get("why", []),
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
        }

    def remove(self, job_id: str) -> None:
        """Take a posting off the list, e.g. once the tool applied to it."""
        self._items.pop(job_id, None)

    def dismiss(self, job_id: str) -> bool:
        """The student removed it: take it off and keep it from coming back."""
        item = self._items.pop(job_id, None)
        if item is None:
            return job_id in self._removed
        self._removed[job_id] = dict(item, removed_on=datetime.now().isoformat(timespec="seconds"))
        return True

    def restore(self, job_id: str) -> bool:
        item = self._removed.pop(job_id, None)
        if item is None:
            return job_id in self._items
        item.pop("removed_on", None)
        self._items[job_id] = item
        return True

    def is_removed(self, job_id: str) -> bool:
        return job_id in self._removed

    def removed_items(self) -> list[dict[str, Any]]:
        return sorted(self._removed.values(), key=lambda r: str(r.get("removed_on", "")), reverse=True)

    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._items.get(job_id) or self._removed.get(job_id)

    def unranked(self) -> list[dict[str, Any]]:
        """Entries saved before fit scores existed."""
        return [item for item in self._items.values() if item.get("fit") is None]

    def set_fit(self, job_id: str, fit: float, why: list[str]) -> None:
        if job_id in self._items:
            self._items[job_id]["fit"] = float(fit)
            self._items[job_id]["why"] = list(why)

    @staticmethod
    def _sort_key(row: dict[str, Any]) -> tuple[float, float, str]:
        fit = row.get("fit")
        return (-float(fit if fit is not None else -1), -float(row.get("score", 0)), row.get("title", ""))

    def items(self) -> list[dict[str, Any]]:
        return sorted(self._items.values(), key=self._sort_key)

    def would_keep(self, job_id: str, fit: float) -> bool:
        """Whether an entry with this fit makes the best `limit`."""
        if job_id in self._removed:
            return False
        if job_id in self._items or not self.limit or len(self._items) < self.limit:
            return True
        worst = self.items()[self.limit - 1].get("fit")
        return worst is None or float(fit) > float(worst)

    def page_html(self, served: bool = False) -> str:
        return _manual_list_html(self.items(), self.folder, self.limit, self.removed_items(), served)

    def trim(self) -> list[dict[str, Any]]:
        """Drop everything below the best `limit` entries; returns what was dropped."""
        rows = self.items()
        dropped = rows[self.limit:] if self.limit and self.limit > 0 else []
        for row in dropped:
            self._items.pop(row["job_id"], None)
        return dropped

    def save(self) -> Path:
        self.folder.mkdir(parents=True, exist_ok=True)
        self.trim()
        rows = self.items()
        self.json_path.write_text(json.dumps(self._items, indent=2, sort_keys=True), encoding="utf-8")
        if self._removed or self.removed_path.exists():
            self.removed_path.write_text(json.dumps(self._removed, indent=2, sort_keys=True), encoding="utf-8")

        fields = ["fit", "score_percent", "title", "employer", "location", "reason", "why", "url", "resume", "first_seen", "last_seen"]
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(row, score_percent=round(float(row.get("score", 0)) * 100), why="; ".join(row.get("why") or [])))

        self.html_path.write_text(self.page_html(), encoding="utf-8")
        return self.html_path


def _manual_list_html(
    rows: list[dict[str, Any]],
    folder: Path,
    limit: int = 50,
    removed: list[dict[str, Any]] | None = None,
    served: bool = False,
) -> str:
    """The list page.

    Opened as a file, Remove can only hide a row in that browser. Served by
    list_server.py, Remove deletes the posting from the saved list for good.
    """
    import os
    from html import escape

    def resume_link(row: dict[str, Any]) -> str:
        resume = str(row.get("resume", ""))
        if not resume or not Path(resume).exists():
            return ""
        if served:
            link = f"/resume/{row.get('job_id', '')}"
        else:
            try:
                link = os.path.relpath(resume, folder).replace(os.sep, "/")
            except ValueError:  # on a different drive
                link = Path(resume).resolve().as_uri()
        return f"<a href='{escape(link, quote=True)}'>Open</a>"

    body = []
    tagged = [(row, False) for row in rows] + [(row, True) for row in (removed or [])]
    for row, gone in tagged:
        percent = round(float(row.get("score", 0)) * 100)
        fit = row.get("fit")
        fit_cell = f"{round(float(fit))}" if fit is not None else "&ndash;"
        why = "; ".join(str(w) for w in (row.get("why") or []))
        why_line = f"<div class='why'>{escape(why)}</div>" if why else ""
        url = escape(str(row.get("url", "")), quote=True)
        job_id = escape(str(row.get("job_id", "")), quote=True)
        body.append(
            f"<tr data-id='{job_id}' data-removed='{1 if gone else 0}'>"
            f"<td class='num fit'>{fit_cell}</td>"
            f"<td class='num'>{percent}%</td>"
            f"<td><a href='{url}' target='_blank' rel='noopener'>{escape(str(row.get('title', '')))}</a>{why_line}</td>"
            f"<td>{escape(str(row.get('employer', '')))}</td>"
            f"<td>{escape(str(row.get('location', '')))}</td>"
            f"<td>{escape(str(row.get('reason', '')))}</td>"
            f"<td>{resume_link(row)}</td>"
            f"<td class='when'>{escape(str(row.get('first_seen', ''))[:10])}</td>"
            "<td class='act'><button type='button' class='remove'>Remove</button></td>"
            "</tr>"
        )
    table = "\n".join(body) or "<tr><td colspan='9'>Nothing here yet.</td></tr>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internships to apply to yourself</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6068; --line:#e3e3e8; --bg:#fff; --accent:#1f5fd6; --soft:#f3f3f6; --warn:#fff4d6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ececf1; --muted:#a4a5ad; --line:#34343b; --bg:#17171b; --accent:#8ab4ff; --soft:#232329; --warn:#3a3220; }} }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:1160px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p {{ color:var(--muted); margin:0 0 12px; }}
  .note {{ background:var(--warn); color:var(--fg); border-radius:8px; padding:8px 12px; font-size:14px; }}
  .bar {{ display:flex; gap:14px; align-items:center; flex-wrap:wrap; margin:0 0 14px; color:var(--muted); font-size:14px; }}
  .wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; }}
  th, td {{ text-align:left; padding:9px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  a {{ color:var(--accent); font-weight:600; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .fit {{ font-weight:700; }}
  .why {{ color:var(--muted); font-size:13px; margin-top:2px; }}
  .when {{ color:var(--muted); white-space:nowrap; }}
  .act {{ white-space:nowrap; }}
  button {{ font:inherit; font-size:13px; color:var(--fg); background:var(--soft); border:1px solid var(--line); border-radius:6px; padding:4px 10px; cursor:pointer; }}
  button:hover {{ border-color:var(--muted); }}
  button:disabled {{ opacity:.6; cursor:default; }}
  .linkish {{ background:none; border:none; padding:0; color:var(--accent); font-weight:600; }}
  tr.removed {{ display:none; }}
  body.show-removed tr.removed {{ display:table-row; opacity:.5; }}
</style></head>
<body><main>
<h1>Internships to apply to yourself</h1>
<p>{len(rows)} good matches the assistant could not apply to for you, best fit first (at most {limit}). Fit is out of 100: the major match, overlap with your resume, how open it is to undergraduates, and the title. Each link opens the posting on Handshake.</p>
<p class="note" id="note" hidden></p>
<div class="bar">
  <span id="count"></span>
  <button type="button" class="linkish" id="toggle" hidden>Show removed</button>
</div>
<div class="wrap"><table>
<thead><tr><th>Fit</th><th>Match</th><th>Internship</th><th>Employer</th><th>Location</th><th>Why it's here</th><th>Tailored resume</th><th>Found</th><th></th></tr></thead>
<tbody>
{table}
</tbody></table></div>
</main>
<script>
(function () {{
  var SERVED = {'true' if served else 'false'};
  // Opened as a plain file, removals can only be remembered in this browser.
  var KEY = 'hsbot-removed-internships';
  var local = {{}};
  try {{ local = JSON.parse(localStorage.getItem(KEY) || '{{}}') || {{}}; }} catch (e) {{ local = {{}}; }}
  function saveLocal() {{ try {{ localStorage.setItem(KEY, JSON.stringify(local)); }} catch (e) {{}} }}

  var rows = Array.prototype.slice.call(document.querySelectorAll('tbody tr[data-id]'));
  var toggle = document.getElementById('toggle');
  var count = document.getElementById('count');
  var note = document.getElementById('note');

  function say(text) {{ note.textContent = text; note.hidden = !text; }}
  function isRemoved(row) {{
    return row.getAttribute('data-removed') === '1' || (!SERVED && !!local[row.getAttribute('data-id')]);
  }}
  function post(action, id) {{
    return fetch('/api/' + action, {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json', 'X-Hsbot': '1' }},
      body: JSON.stringify({{ id: id }})
    }}).then(function (r) {{ if (!r.ok) throw new Error(r.status); return r.json(); }});
  }}

  function render() {{
    var hidden = 0;
    rows.forEach(function (row) {{
      var gone = isRemoved(row);
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
    var button = row.querySelector('button.remove');
    button.addEventListener('click', function () {{
      var id = row.getAttribute('data-id');
      if (!SERVED) {{
        if (row.getAttribute('data-removed') === '1') {{
          say('To restore this one, open the list with "Open apply-yourself list.bat".');
          return;
        }}
        if (local[id]) {{ delete local[id]; }} else {{ local[id] = new Date().toISOString().slice(0, 10); }}
        saveLocal();
        render();
        return;
      }}
      var restoring = row.getAttribute('data-removed') === '1';
      button.disabled = true;
      post(restoring ? 'restore' : 'remove', id).then(function () {{
        row.setAttribute('data-removed', restoring ? '0' : '1');
        say('');
        render();
      }}).catch(function () {{
        say('The list closed after sitting idle. Open it again to remove or restore postings.');
      }}).then(function () {{ button.disabled = false; }});
    }});
  }});
  toggle.addEventListener('click', function () {{
    document.body.classList.toggle('show-removed');
    render();
  }});

  if (SERVED) {{
    setInterval(function () {{ fetch('/api/ping', {{ method: 'POST', headers: {{ 'X-Hsbot': '1' }} }}).catch(function () {{}}); }}, 20000);
  }} else {{
    say('Remove here only hides a posting in this browser. To remove postings for good, open the list with "Open apply-yourself list.bat".');
  }}
  render();
}})();
</script>
</body></html>
"""
