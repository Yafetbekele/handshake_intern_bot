"""Find internships on company career sites that aren't on Handshake.

Run it every morning:

    python find_elsewhere.py
    (or double-click "Find internships elsewhere.bat")

It reads the public job boards of the companies in elsewhere_companies.json
(Greenhouse, Lever and Ashby all publish their openings for anyone to read),
keeps summer internships that fit Computer Engineering, drops anything already
found on Handshake, grades the rest with the same fit score as the ranked list
(deep_rank.py, at most 2 per company), and marks the ones that are new since
the last run.

Results: "Internships to apply to yourself/found_elsewhere.html" (and .csv).
Nothing is applied to, and Handshake isn't opened.
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import csv
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.request
from datetime import date, datetime
from html import escape
from pathlib import Path

import applied_myself
import deep_rank
import page_bits
import majors
import matcher
import posting_cache
import tailor
import web_discovery
from main import DATA_DIR, manual_list

HERE = Path(__file__).resolve().parent
COMPANIES_PATH = HERE / "elsewhere_companies.json"
SEEN_PATH = DATA_DIR / "elsewhere_seen.json"
USER_AGENT = "internship-finder/1.0 (personal job search)"

INTERN_TITLE = re.compile(r"\b(intern|interns|internship|internships|co-?op)\b", re.I)
NOT_STUDENT = re.compile(r"\b(internal|international)\b", re.I)
NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|ontario|united kingdom|uk|london|ireland|dublin|germany|berlin|munich|"
    r"france|paris|netherlands|amsterdam|poland|india|bangalore|bengaluru|hyderabad|singapore|japan|tokyo|taiwan|"
    r"korea|seoul|china|shanghai|beijing|israel|tel aviv|australia|sydney|mexico|brazil|spain|madrid|italy|"
    r"sweden|switzerland|zurich|czech|romania|serbia|ukraine|new zealand|philippines|vietnam)\b", re.I)


def get_json(url: str, timeout: int = 25) -> object:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read())


def plain(html_text: str) -> str:
    text = html_lib.unescape(html_text or "")
    text = re.sub(r"<(br|p|li|div|h\d)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"[ \t]+", " ", re.sub(r"\n\s*\n+", "\n", text)).strip()


def is_internship_title(title: str) -> bool:
    return bool(INTERN_TITLE.search(title)) and not (NOT_STUDENT.search(title) and not re.search(r"\bintern(ship)?\b", title, re.I))


# ------------------------------------------------------------------ sources


def from_greenhouse(company: dict) -> list[dict]:
    board = company["board"]
    listed = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs").get("jobs", [])
    found = []
    for job in listed:
        if not is_internship_title(job.get("title", "")):
            continue
        try:
            detail = get_json(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job['id']}")
        except Exception:
            detail = {}
        found.append({
            "id": f"gh-{board}-{job['id']}", "title": job.get("title", ""), "employer": company["name"],
            "location": (job.get("location") or {}).get("name", ""), "url": job.get("absolute_url", ""),
            "description": plain(detail.get("content", "")), "posted": (job.get("first_published") or "")[:10],
        })
    return found


def from_lever(company: dict) -> list[dict]:
    board = company["board"]
    listed = get_json(f"https://api.lever.co/v0/postings/{board}?mode=json")
    found = []
    for job in listed if isinstance(listed, list) else []:
        title = job.get("text", "")
        commitment = (job.get("categories") or {}).get("commitment", "") or ""
        if not (is_internship_title(title) or re.search(r"\bintern", commitment, re.I)):
            continue
        extra = " ".join(f"{block.get('text', '')}\n{plain(block.get('content', ''))}" for block in job.get("lists", []))
        posted = datetime.fromtimestamp(job["createdAt"] / 1000).date().isoformat() if job.get("createdAt") else ""
        found.append({
            "id": f"lv-{board}-{job['id']}", "title": title, "employer": company["name"],
            "location": (job.get("categories") or {}).get("location", ""), "url": job.get("hostedUrl", ""),
            "description": f"{job.get('descriptionPlain', '')}\n{extra}\n{job.get('additionalPlain', '')}",
            "posted": posted,
        })
    return found


def from_ashby(company: dict) -> list[dict]:
    board = company["board"]
    listed = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true").get("jobs", [])
    found = []
    for job in listed:
        title = job.get("title", "")
        if not (is_internship_title(title) or re.search(r"\bintern", job.get("employmentType", "") or "", re.I)):
            continue
        if job.get("isListed") is False:
            continue
        found.append({
            "id": f"ab-{board}-{job['id']}", "title": title, "employer": company["name"],
            "location": job.get("location", "") or ("Remote" if job.get("isRemote") else ""),
            "url": job.get("jobUrl", ""), "description": job.get("descriptionPlain", "") or plain(job.get("descriptionHtml", "")),
            "posted": (job.get("publishedAt") or "")[:10],
        })
    return found


SOURCES = {"greenhouse": from_greenhouse, "lever": from_lever, "ashby": from_ashby}


def gather(companies: list[dict]) -> tuple[list[dict], list[str]]:
    found: list[dict] = []
    problems: list[str] = []

    def one(company: dict) -> tuple[dict, list[dict] | Exception]:
        try:
            return company, SOURCES[company["ats"]](company)
        except Exception as exc:  # one broken board never stops the rest
            return company, exc

    with futures.ThreadPoolExecutor(max_workers=6) as pool:
        for company, result in pool.map(one, companies):
            if isinstance(result, Exception):
                problems.append(f"{company['name']}: {type(result).__name__}")
            else:
                found += result
    return found, problems


# ------------------------------------------------------------------ filters


def handshake_keys() -> set[tuple[str, str]]:
    """(company, job) pairs already seen on Handshake, so they aren't repeated here."""
    keys: set[tuple[str, str]] = set()
    try:
        ledger = json.loads((DATA_DIR / "applied.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ledger = {}
    for entry in ledger.values():
        keys.add((deep_rank.company_key(entry.get("employer", "")), deep_rank.title_key(entry.get("title", ""))))
    folder = posting_cache.folder()
    for path in folder.glob("*.json") if folder.exists() else []:
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        keys.add((deep_rank.company_key(saved.get("employer", "")), deep_rank.title_key(saved.get("title", ""))))
    return keys


def on_handshake(posting: dict, keys: set[tuple[str, str]]) -> bool:
    company = deep_rank.company_key(posting["employer"])
    title = deep_rank.title_key(posting["title"])
    return any(c == company and (t == title or (t and title and (t in title or title in t))) for c, t in keys)


def keep(posting: dict, major: majors.MajorProfile, min_score: float, us_only: bool) -> tuple[bool, str]:
    title, text = posting["title"], posting["description"]
    if us_only and NON_US.search(posting["location"] or "") and not re.search(r"\b(us|usa|united states|remote)\b",
                                                                              posting["location"], re.I):
        return False, "outside the US"
    status = matcher.summer_status(title, text)
    if status == "other":
        return False, "not summer"
    result = matcher.score_posting(title, f"{posting['employer']} {posting['location']} {text}", major)
    posting["score"] = result.score
    if not matcher.passes(result, min_score):
        return False, f"{result.percent}% match"
    return True, ""


# ------------------------------------------------------------------ output


def load_seen() -> dict[str, str]:
    try:
        return json.loads(SEEN_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_page(ranked: list[deep_rank.Graded], new_ids: set[str], folder: Path, stats: dict) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "found_elsewhere.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "new", "score", "title", "company", "location", "pay", "kind of job", "matches",
                         "notes", "url"])
        for rank, g in enumerate(ranked, start=1):
            writer.writerow([rank, "new" if g.job_id in new_ids else "", g.score, g.title, g.employer, g.location,
                             g.pay, g.family, "; ".join(g.matched), "; ".join(g.flags), g.url])
    rows = []
    for rank, g in enumerate(ranked, start=1):
        is_new = g.job_id in new_ids
        notes = (["matches: " + ", ".join(g.matched)] if g.matched else []) + g.flags
        breakdown = " · ".join(f"{k} {round(v * 100)}" for k, v in g.parts.items())
        more = f"<div class='why'>+{g.more_at_company} more at {escape(g.employer)}</div>" if g.more_at_company else ""
        rows.append(
            f"<tr class='{'new' if is_new else ''}'><td class='num'>{rank}</td>"
            f"<td class='num score' title='{escape(breakdown, quote=True)}'>{round(g.score)}</td>"
            f"<td>{'<span class=badge>NEW</span> ' if is_new else ''}"
            f"<a href='{escape(g.url, quote=True)}' target='_blank' rel='noopener'>{escape(g.title)}</a>"
            f"<div class='why'>{escape('; '.join(notes))}</div></td>"
            f"<td>{escape(g.employer)}{more}</td><td>{escape(g.location)}</td>"
            f"<td class='num'>{escape(g.pay)}</td><td>{escape(g.family)}</td>"
            f"<td>{page_bits.button(g.job_id, g.title, g.employer, g.url, 'elsewhere')}</td></tr>"
        )
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Internships found elsewhere</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6068; --line:#e3e3e8; --bg:#fff; --accent:#1f5fd6; --new:#e8f5e9; --badge:#1b7f3b; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ececf1; --muted:#a4a5ad; --line:#34343b; --bg:#17171b; --accent:#8ab4ff; --new:#1f2b21; --badge:#7fd191; }} }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:1200px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  p {{ color:var(--muted); margin:0 0 12px; }}
  .wrap {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; }}
  th, td {{ text-align:left; padding:8px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ font-size:12px; text-transform:uppercase; letter-spacing:.04em; color:var(--muted); }}
  a {{ color:var(--accent); font-weight:600; text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .num {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .score {{ font-weight:700; cursor:help; }}
  .why {{ color:var(--muted); font-size:13px; margin-top:2px; }}
  tr.new td {{ background:var(--new); }}
  .badge {{ font-size:11px; font-weight:700; color:var(--badge); border:1px solid currentColor; border-radius:4px; padding:0 4px; }}
{page_bits.CSS}
</style></head>
<body><main>
{page_bits.nav("/elsewhere")}
<h1>Internships found elsewhere</h1>
<p>Checked {stats['companies']} company career sites on {date.today().strftime('%B %d, %Y').replace(' 0', ' ')}.
{stats['internships']} internship postings; {stats['kept']} fit a summer Computer Engineering search and aren't on Handshake.
Best fit first, at most 2 per company. <strong>{len(new_ids)} new since the last run</strong>, highlighted.
Hover a score for its parts. Links go to each company's own application page.</p>
{page_bits.tools()}
<div class="wrap"><table>
<thead><tr><th>#</th><th>Fit</th><th>Internship</th><th>Company</th><th>Location</th><th>Pay</th><th>Kind</th><th></th></tr></thead>
<tbody>
{chr(10).join(rows) or "<tr><td colspan='8'>Nothing matched today.</td></tr>"}
</tbody></table></div>
</main>
{page_bits.SCRIPT}
</body></html>
"""
    out = folder / "found_elsewhere.html"
    out.write_text(page, encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Find internships on company career sites that aren't on Handshake.")
    parser.add_argument("--major", default="", help="major to match (default: the launcher's saved major)")
    parser.add_argument("--strictness", choices=list(matcher.STRICTNESS), default="broad",
                        help="how picky the major match is (default broad; the fit score does the ranking)")
    parser.add_argument("--per-company", type=int, default=2)
    parser.add_argument("--anywhere", action="store_true", help="include postings outside the US")
    parser.add_argument("--no-open", action="store_true", help="don't open the page when done")
    parser.add_argument("--searches", type=int, default=4,
                        help="web searches a day, different every day (default 4; 0 turns them off)")
    args = parser.parse_args(argv)

    try:
        settings = json.loads((DATA_DIR / "launcher_settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    major = majors.resolve(args.major or settings.get("major") or "Computer Engineering")
    companies = json.loads(COMPANIES_PATH.read_text(encoding="utf-8"))["companies"]
    day = date.today()

    # Grow the company list: boards on today's community list, and from past web searches.
    listing_text = web_discovery.community_list(day)
    known = {(c["ats"], c["board"].lower()) for c in companies}
    added = web_discovery.add_boards(web_discovery.boards_in(listing_text), known, "community list", day)
    web_found: list[dict] = []
    if args.searches > 0 and tailor.find_claude():
        print(f"Searching the web ({args.searches} searches, different every day)...")
        names = [c["name"] for c in companies] + [b["name"] for b in web_discovery.load_discovered().values()]
        web_found, _, web_boards = web_discovery.web_postings(day, args.searches, names)
        added += web_discovery.add_boards(web_boards, known, "web search", day)
        print(f"  {len(web_found)} postings from web searches (kept for two weeks)")
    elif args.searches > 0:
        print("Claude Code isn't set up, so there are no web searches today.")
    discovered = [b for b in web_discovery.load_discovered().values() if (b["ats"], b["board"].lower()) not in known]
    companies = companies + discovered
    if added:
        print(f"  {added} new company career sites added to the daily check")

    started = time.monotonic()
    print(f"Checking {len(companies)} company career sites for {major.name} internships...")
    found, problems = gather(companies)
    print(f"  {len(found)} internship postings found in {time.monotonic() - started:.0f}s")
    if problems:
        print(f"  {len(problems)} sites couldn't be read (moved or closed boards are skipped)")
    board_ids = {p["url"] for p in found}
    found += [p for p in web_found if p["url"] not in board_ids]

    keys = handshake_keys()
    done = applied_myself.ids()
    kept, dropped = [], {}
    for posting in found:
        if posting["id"] in done:
            dropped["you applied"] = dropped.get("you applied", 0) + 1
            continue
        if on_handshake(posting, keys):
            dropped["also on Handshake"] = dropped.get("also on Handshake", 0) + 1
            continue
        ok, why = keep(posting, major, matcher.STRICTNESS[args.strictness], not args.anywhere)
        if ok:
            kept.append(posting)
        else:
            key = why if not why.endswith("match") else "low match"
            dropped[key] = dropped.get(key, 0) + 1
    print(f"  kept {len(kept)}; left out: " + ", ".join(f"{n} {why}" for why, n in sorted(dropped.items(), key=lambda kv: -kv[1])))

    profile = tailor.load_profile(tailor.PROFILE_PATH) if tailor.PROFILE_PATH.exists() else {}
    terms = deep_rank.student_terms(None, tailor.PROFILE_PATH)
    graded = deep_rank.grade_all([dict(p, job_id=p["id"]) for p in kept], profile, terms)
    # Lesser-known postings rank higher: widely shared ones get a small penalty.
    is_popular = web_discovery.popular_marker(listing_text)
    sources = {p["id"]: p.get("source", "") for p in kept}
    for g in graded:
        if is_popular(g.url, g.employer, g.title):
            g.score = round(g.score * 0.9, 1)
            g.flags.append("on a popular list")
        if sources.get(g.job_id) == "web search":
            g.flags.append("found by web search")
    graded.sort(key=lambda g: -g.score)
    ranked = deep_rank.spread(graded, args.per_company)

    seen = load_seen()
    today = date.today().isoformat()
    new_ids = {g.job_id for g in ranked if g.job_id not in seen}
    for posting in kept:
        seen.setdefault(posting["id"], today)
    SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEEN_PATH.write_text(json.dumps(seen, indent=1), encoding="utf-8")

    page = write_page(ranked, new_ids, manual_list().folder,
                      {"companies": len(companies), "internships": len(found), "kept": len(kept)})
    print(f"\n{len(ranked)} ranked, {len(new_ids)} new since last time. Top 10:")
    for rank, g in enumerate(ranked[:10], start=1):
        print(f"  {rank:2d}. {round(g.score):3d} {'NEW ' if g.job_id in new_ids else '    '}{g.title[:50]} @ {g.employer}")
    print(f"\nSaved to {page}")
    if not args.no_open and not os.environ.get("HSBOT_NO_OPEN"):
        try:
            os.startfile(str(page))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
