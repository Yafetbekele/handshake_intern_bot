"""The questions the applier couldn't answer, counted across forms.

Every time a form is filled in, the required questions it had to leave empty,
and the ones it could only answer with its best judgement, are noted here.
The same question from different companies ("Are you related to any current
Acme employees?", "...any current Initech employees?") is counted as one.

    python employer_apply.py --questions

shows them, most common first, and lets the student answer them once for
every company. "Questions it couldn't answer.html" in the results folder has
the same list. A question drops off once the profile has an answer for it.

Kept in data/unanswered_questions.json: the questions and which postings asked
them, never an answer.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from html import escape
from pathlib import Path
from typing import Any

from employer_sites import candidates, clean_label, norm, pick_option
from handshake import NEVER_FILL

HERE = Path(__file__).resolve().parent


def path() -> Path:
    return Path(os.environ.get("HSBOT_DATA_DIR") or HERE / "data") / "unanswered_questions.json"


def load() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save(store: dict[str, dict[str, Any]]) -> None:
    path().parent.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps(store, indent=1, ensure_ascii=False), encoding="utf-8")


def general(label: str, employer: str) -> str:
    """The question with the company's name taken out, so it's the same question everywhere."""
    text = clean_label(label)
    names = {employer.strip(), employer.strip().split(" ")[0] if employer.strip() else ""}
    for name in sorted((n for n in names if len(n) > 2), key=len, reverse=True):
        text = re.sub(rf"(?<!\w){re.escape(name)}(?!\w)", "[company]", text, flags=re.IGNORECASE)
    return text


def note(posting: dict[str, Any], questions: list[dict[str, Any]], how: str) -> None:
    """Record questions from one form. `how` is "empty" (left empty) or "guessed" (best judgement used)."""
    if not questions:
        return
    store = load()
    for question in questions:
        label = clean_label(str(question.get("label") or ""))
        if not label or NEVER_FILL.search(label) or "can no longer be read" in label:
            continue
        shown = general(label, posting.get("employer", ""))
        entry = store.setdefault(norm(shown)[:300], {"question": shown, "labels": [], "choices": [], "employers": [],
                                                    "empty": [], "guessed": []})
        if posting["id"] not in entry[how]:
            entry[how].append(posting["id"])
        if label not in entry["labels"] and len(entry["labels"]) < 40:
            entry["labels"].append(label)
        employer = posting.get("employer", "")
        if employer and employer not in entry["employers"]:
            entry["employers"].append(employer)
        choices = [str(c) for c in question.get("options") or []][:40]
        if choices and not entry["choices"]:
            entry["choices"] = choices
        entry["last"] = date.today().isoformat()
    _save(store)


def _answered(entry: dict[str, Any], answers: list[dict[str, Any]]) -> bool:
    """Whether the profile now answers this question, in every wording it was asked in."""
    choices = entry.get("choices") or []
    for label in entry.get("labels") or [entry["question"]]:
        found = candidates(label, answers)
        if not found:
            return False
        if choices and not any(pick_option(choices, str(v)) is not None
                               for a in found for v in re.split(r"\s*[;|]\s*", str(a["value"]))):
            return False
    return True


def ranked(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Questions still without an answer, the ones asked on the most forms first."""
    rows = []
    for entry in load().values():
        if _answered(entry, answers):
            continue
        forms = set(entry.get("empty", [])) | set(entry.get("guessed", []))
        only_guessed = set(entry.get("guessed", [])) - set(entry.get("empty", []))
        rows.append(dict(entry, forms=len(forms), left_empty=len(set(entry.get("empty", []))), guessed_on=len(only_guessed)))
    rows.sort(key=lambda r: (-r["forms"], -r["left_empty"], r["question"].lower()))
    return rows


def what_happened(row: dict[str, Any]) -> str:
    parts = []
    if row["left_empty"]:
        parts.append(f"left empty on {row['left_empty']}")
    if row["guessed_on"]:
        parts.append(f"best judgement used on {row['guessed_on']}")
    return ", ".join(parts)


def write_page(folder: Path, rows: list[dict[str, Any]]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    body = []
    for rank, row in enumerate(rows, start=1):
        choices = " &nbsp;·&nbsp; ".join(escape(c) for c in row["choices"][:12]) + (" ..." if len(row["choices"]) > 12 else "")
        companies = ", ".join(row["employers"][:5]) + (f" and {len(row['employers']) - 5} more" if len(row["employers"]) > 5 else "")
        body.append(
            f"<tr><td class='num'>{rank}</td><td class='num'>{row['forms']}</td>"
            f"<td>{escape(row['question'])}<div class='why'>{choices or 'typed answer'}</div></td>"
            f"<td>{escape(what_happened(row))}</td><td class='why'>{escape(companies)}</td></tr>"
        )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Questions it couldn't answer</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6068; --line:#e3e3e8; --bg:#fff; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ececf1; --muted:#a4a5ad; --line:#34343b; --bg:#17171b; }} }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:1100px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p {{ color:var(--muted); margin:0 0 12px; }}
  .wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  .num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .why {{ color:var(--muted); font-size:13px; margin-top:2px; }}
</style></head>
<body><main>
<h1>Questions it couldn't answer</h1>
<p>{len(rows)} required questions your profile doesn't answer yet, the ones asked on the most forms first.
Updated {date.today().strftime('%B %d, %Y').replace(' 0', ' ')}. Answer them once for every company with
<strong>Answer skipped questions</strong> in the Programs folder (or <code>python employer_apply.py --questions</code>);
a question leaves this list once it has an answer.</p>
<div class="wrap"><table>
<thead><tr><th>#</th><th>Forms</th><th>Question, and its choices</th><th>What happened</th><th>Asked by</th></tr></thead>
<tbody>
{chr(10).join(body) or "<tr><td colspan='5'>Nothing unanswered so far.</td></tr>"}
</tbody></table></div>
</main></body></html>
"""
    out = folder / "Questions it couldn't answer.html"
    out.write_text(page, encoding="utf-8")
    return out
