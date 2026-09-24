"""Rank every employer-site posting the tool has found, best fit first.

    python rank_all.py              read any postings not saved yet, then rank
    python rank_all.py --no-fetch   rank only what's already saved
    python rank_all.py --per-company 3

Reading opens each posting on Handshake once, read only, the way a run does,
and saves it in data/postings/ so it never has to be read again. It can be
stopped at any time and picks up where it left off.

The ranked list goes to "Internships to apply to yourself/all_ranked.html"
(plus .csv). See deep_rank.py for how postings are graded.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from html import escape
from pathlib import Path

import applied_myself
import deep_rank
import page_bits
import posting_cache
import resume_parser
import tailor
from main import DATA_DIR, load_config, load_selectors, manual_list

HERE = Path(__file__).resolve().parent


def employer_site_postings() -> dict[str, dict]:
    """Every posting recorded as 'apply on the employer's site', from the ledger."""
    try:
        ledger = json.loads((DATA_DIR / "applied.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in ledger.items() if v.get("status") == "skipped_external"}


def fetch_missing(ids: list[str], pause: tuple[float, float] = (0.5, 1.5)) -> int:
    from handshake import HandshakeSession

    missing = [i for i in ids if not posting_cache.has(i)]
    if not missing:
        return 0
    minutes = len(missing) * 6 / 60
    print(f"Reading {len(missing)} postings not saved yet (about {minutes:.0f} minutes). Read only.")
    config = load_config(argparse.Namespace())
    read = 0
    started = time.monotonic()
    with HandshakeSession(config, load_selectors(), DATA_DIR / "browser_profile") as session:
        if not session.ensure_logged_in():
            raise SystemExit("Not signed in to Handshake. Run the launcher once and sign in.")
        for index, job_id in enumerate(missing, start=1):
            try:
                job = session.load_job(job_id)
            except SystemExit:
                raise
            except Exception as exc:
                if "closed" in str(exc).lower():
                    print(f"\nThe browser window was closed after {read} postings; ranking what's saved.")
                    return read
                print(f"  couldn't read {job_id} ({type(exc).__name__}); skipping it")
                continue
            if job.title:
                posting_cache.save(job)
                read += 1
            if index % 25 == 0 or index == len(missing):
                rate = (time.monotonic() - started) / index
                left = (len(missing) - index) * rate / 60
                print(f"  {index}/{len(missing)} read, about {left:.0f} minutes left")
            time.sleep(random.uniform(*pause))
    return read


def write_outputs(ranked: list[deep_rank.Graded], folder: Path, total: int, per_company: int) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "all_ranked.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "score", "title", "employer", "location", "pay", "deadline", "kind of job",
                         "matches", "notes", "more at this company", "url"]
                        + [f"{k} score" for k in deep_rank.WEIGHTS])
        for rank, g in enumerate(ranked, start=1):
            writer.writerow([rank, g.score, g.title, g.employer, g.location, g.pay, g.deadline, g.family,
                             "; ".join(g.matched), "; ".join(g.flags), g.more_at_company, g.url]
                            + [g.parts[k] for k in deep_rank.WEIGHTS])

    rows = []
    for rank, g in enumerate(ranked, start=1):
        breakdown = " · ".join(f"{k} {round(v * 100)}" for k, v in g.parts.items())
        notes = []
        if g.matched:
            notes.append("matches: " + ", ".join(g.matched))
        notes += g.flags
        more = f"<div class='more'>+{g.more_at_company} more at {escape(g.employer)}</div>" if g.more_at_company else ""
        soon = " soon" if g.closing_soon else ""
        rows.append(
            f"<tr data-id='{escape(g.job_id, quote=True)}'>"
            f"<td class='num'>{rank}</td>"
            f"<td class='num score' title='{escape(breakdown, quote=True)}'>{round(g.score)}</td>"
            f"<td><a href='{escape(g.url, quote=True)}' target='_blank' rel='noopener'>{escape(g.title)}</a>"
            f"<div class='why'>{escape('; '.join(notes))}</div></td>"
            f"<td>{escape(g.employer)}{more}</td>"
            f"<td>{escape(g.location)}</td>"
            f"<td class='num'>{escape(g.pay)}</td>"
            f"<td class='num{soon}'>{escape(g.deadline)}</td>"
            f"<td>{escape(g.family)}</td>"
            f"<td>{page_bits.button(g.job_id, g.title, g.employer, g.url, 'ranked')}</td>"
            "</tr>"
        )
    weights = ", ".join(f"{k} {round(w * 100)}%" for k, w in deep_rank.WEIGHTS.items())
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>All internships, ranked</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6068; --line:#e3e3e8; --bg:#fff; --accent:#1f5fd6; --warn:#b3261e; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ececf1; --muted:#a4a5ad; --line:#34343b; --bg:#17171b; --accent:#8ab4ff; --warn:#f2b8b5; }} }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:1200px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p {{ color:var(--muted); margin:0 0 12px; }}
  .bar {{ display:flex; gap:10px; flex-wrap:wrap; margin:0 0 14px; }}
  input {{ font:inherit; padding:6px 10px; border:1px solid var(--line); border-radius:6px; background:var(--bg); color:var(--fg); min-width:240px; }}
  .wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  a {{ color:var(--accent); font-weight:600; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .score {{ font-weight:700; cursor:help; }}
  .why, .more {{ color:var(--muted); font-size:13px; margin-top:2px; }}
  .soon {{ color:var(--warn); font-weight:600; }}
{page_bits.CSS}
</style></head>
<body><main>
{page_bits.nav("/ranked")}
<h1>All internships, ranked</h1>
<p>The best {len(ranked)} of {total} postings that apply on the employer's own site, best fit first, at most {per_company} per company.
Hover a score for its parts ({escape(weights)}). Deadlines in red close within 10 days.</p>
<div class="bar"><input id="filter" placeholder="Filter by title, employer, city..." aria-label="Filter"></div>
{page_bits.tools()}
<div class="wrap"><table>
<thead><tr><th>#</th><th>Fit</th><th>Internship</th><th>Employer</th><th>Location</th><th>Pay</th><th>Deadline</th><th>Kind</th><th></th></tr></thead>
<tbody>
{chr(10).join(rows) or "<tr><td colspan='9'>Nothing to rank yet.</td></tr>"}
</tbody></table></div>
</main>
<script>
document.getElementById('filter').addEventListener('input', function () {{
  var q = this.value.toLowerCase();
  document.querySelectorAll('tbody tr[data-id]').forEach(function (row) {{
    row.hidden = q && row.textContent.toLowerCase().indexOf(q) === -1;
  }});
}});
</script>
{page_bits.SCRIPT}
</body></html>
"""
    out = folder / "all_ranked.html"
    out.write_text(page, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rank every employer-site posting found so far.")
    parser.add_argument("--no-fetch", action="store_true", help="rank only postings already saved")
    parser.add_argument("--per-company", type=int, default=2, help="most postings from one employer (default 2)")
    parser.add_argument("--resume", help="your resume, for extra skill terms (optional)")
    args = parser.parse_args(argv)

    posting_cache.import_tailored(tailor.OUT_DIR)
    ledger = employer_site_postings()
    listing = manual_list()
    done = applied_myself.ids()
    ids = [i for i in ledger if not listing.is_removed(i) and i not in done]
    if not args.no_fetch:
        fetch_missing(ids)

    postings = []
    for job_id in ids:
        saved = posting_cache.load(job_id)
        if saved is None:
            continue
        entry = ledger[job_id]
        saved.setdefault("url", entry.get("url", ""))
        saved["url"] = saved.get("url") or entry.get("url", "")
        saved["location"] = saved.get("location") or entry.get("location", "")
        saved["score"] = entry.get("score", 0.5)
        postings.append(saved)
    unread = len(ids) - len(postings)

    profile = tailor.load_profile(tailor.PROFILE_PATH) if tailor.PROFILE_PATH.exists() else {}
    resume = resume_parser.extract(args.resume) if args.resume else None
    terms = deep_rank.student_terms(resume, tailor.PROFILE_PATH)
    graded = deep_rank.grade_all(postings, profile, terms)
    expired = len(postings) - len(graded)
    ranked = deep_rank.spread(graded, args.per_company)

    page = write_outputs(ranked, listing.folder, len(postings), args.per_company)
    print(f"\n{len(postings)} postings graded: {expired} past their deadline or closed, "
          f"{len(graded) - len(ranked)} left out to keep at most {args.per_company} per company.")
    if unread:
        print(f"{unread} couldn't be read yet; run again to include them.")
    print(f"Top 10:")
    for rank, g in enumerate(ranked[:10], start=1):
        print(f"  {rank:2d}. {round(g.score):3d}  {g.title[:55]} @ {g.employer[:30]}")
    print(f"\nRanked list: {page}")
    if not os.environ.get("HSBOT_NO_OPEN"):
        try:
            os.startfile(str(page))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. Postings read so far are saved; run again to continue.")
        sys.exit(130)
