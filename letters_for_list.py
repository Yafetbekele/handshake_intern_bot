"""Write cover letters for the top postings on a ranked list, in one go.

    python letters_for_list.py                   top 20 of "Found elsewhere"
    python letters_for_list.py --top 10
    python letters_for_list.py --list ranked     top of "All internships, ranked" (Handshake)
    python letters_for_list.py --resumes         tailored resumes too

Postings you've marked Applied are skipped, and a posting that already has a
letter keeps it. Letters go into tailored_resumes/<job>/ like every other
letter, and "Cover letters.html" in the results folder links to all of them.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import date
from html import escape
from pathlib import Path

import applied_myself
import cover_letter
import posting_cache
import tailor
from main import DATA_DIR, manual_list


def elsewhere_postings() -> list[dict]:
    try:
        return json.loads((DATA_DIR / "elsewhere_ranked.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit('Run "Find internships elsewhere" first, then try again.')


def ranked_postings() -> list[dict]:
    path = manual_list().folder / "all_ranked.csv"
    try:
        rows = list(csv.reader(path.open(encoding="utf-8")))[1:]
    except OSError:
        raise SystemExit("Run rank_all.py first, then try again.")
    postings = []
    for row in rows:
        url = row[11]
        job_id = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        saved = posting_cache.load(job_id)
        if saved:
            postings.append({"id": job_id, "title": saved["title"], "employer": saved["employer"],
                             "location": saved.get("location", ""), "url": url, "description": saved["description"]})
    return postings


def write_index(entries: list[dict], folder: Path) -> Path:
    rows = []
    for e in entries:
        rel_letter = os.path.relpath(e["letter"], folder).replace(os.sep, "/")
        resume = ""
        if e.get("resume"):
            resume = f" · <a href='{escape(os.path.relpath(e['resume'], folder).replace(os.sep, '/'), quote=True)}'>Resume</a>"
        rows.append(
            f"<tr><td class='num'>{e['rank']}</td>"
            f"<td><a href='{escape(e['url'], quote=True)}' target='_blank' rel='noopener'>{escape(e['title'])}</a>"
            f"<div class='why'>{escape(e['location'])}</div></td>"
            f"<td>{escape(e['employer'])}</td>"
            f"<td><a href='{escape(rel_letter, quote=True)}'>Cover letter</a>{resume}</td></tr>"
        )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cover letters</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6068; --line:#e3e3e8; --bg:#fff; --accent:#1f5fd6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ececf1; --muted:#a4a5ad; --line:#34343b; --bg:#17171b; --accent:#8ab4ff; }} }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:1000px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p {{ color:var(--muted); margin:0 0 12px; }}
  table {{ border-collapse:collapse; width:100%; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  a {{ color:var(--accent); font-weight:600; text-decoration:none; }}
  .num {{ font-variant-numeric:tabular-nums; }}
  .why {{ color:var(--muted); font-size:13px; }}
</style></head>
<body><main>
<h1>Cover letters</h1>
<p>{len(entries)} letters for the top of your lists, written {date.today().strftime('%B %d').replace(' 0', ' ')}.
The posting link opens the application; each letter also has a .txt copy in its folder for web forms.</p>
<table><thead><tr><th>#</th><th>Internship</th><th>Company</th><th>Documents</th></tr></thead>
<tbody>
{chr(10).join(rows)}
</tbody></table>
</main></body></html>
"""
    out = folder / "Cover letters.html"
    out.write_text(page, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write cover letters for the top of a ranked list.")
    parser.add_argument("--list", choices=["elsewhere", "ranked"], default="elsewhere")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--resumes", action="store_true", help="make tailored resumes too")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args(argv)

    postings = elsewhere_postings() if args.list == "elsewhere" else ranked_postings()
    done = applied_myself.ids()
    chosen = [p for p in postings if p["id"] not in done][: args.top]
    print(f"Writing cover letters for the top {len(chosen)} on the "
          f"{'Found elsewhere' if args.list == 'elsewhere' else 'ranked'} list (Applied ones skipped).\n")

    entries = []
    for rank, posting in enumerate(chosen, start=1):
        folder_id = posting["id"].replace("/", "-")[:60]
        print(f"[{rank}/{len(chosen)}] {posting['title'][:60]} @ {posting['employer']}")
        try:
            letter = cover_letter.cover_letter(folder_id, posting["title"], posting["employer"],
                                               posting["description"], use_ai=not args.no_ai)
        except Exception as exc:  # one bad posting never stops the rest
            print(f"  couldn't write this one ({type(exc).__name__}: {exc})")
            continue
        how = {"ai": "Claude, fact-checked", "baseline": "your baseline letter", "profile": "your profile",
               "saved": "written earlier"}.get(letter.method, letter.method)
        print(f"  cover letter ({how})")
        entry = dict(posting, rank=rank, letter=letter.path)
        if args.resumes:
            try:
                entry["resume"] = tailor.tailor_resume(folder_id, posting["title"], posting["employer"],
                                                       posting["description"], use_ai=not args.no_ai).path
            except Exception as exc:
                print(f"  couldn't tailor the resume ({type(exc).__name__})")
        entries.append(entry)

    index = write_index(entries, manual_list().folder)
    print(f"\n{len(entries)} letters written. All of them: {index}")
    if not args.no_open and not os.environ.get("HSBOT_NO_OPEN"):
        try:
            os.startfile(str(index))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. Letters written so far are saved.")
        sys.exit(130)
