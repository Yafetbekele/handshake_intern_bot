"""Handshake summer internship assistant.

Reads a resume, asks for the student's major, searches Handshake for summer
internships in that field, ranks them against the resume, and submits Quick
Apply applications.

Commands
    python main.py login                      sign in once, save the session
    python main.py search --resume r.pdf      rank matches, write a CSV, apply to nothing
    python main.py apply  --resume r.pdf      apply, asking before each submission
    python main.py apply  --resume r.pdf --auto-submit     apply without asking
    python main.py history                    export everything done so far

Read the README before the first run. Handshake's terms of service restrict
automated access, so keep volumes low and review what goes out in your name.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path
from typing import Any

import majors
import matcher
import resume_parser
from handshake import HandshakeSession, Job
from storage import Ledger, write_report

HERE = Path(__file__).resolve().parent

DEFAULT_CONFIG: dict[str, Any] = {
    "handshake_base_url": "https://app.joinhandshake.com",
    "search_url_template": "{base}/stu/postings?query={query}&page={page}&per_page=25",
    "resume_path": "resume.pdf",
    "resume_doc_name": "",
    "major": "",
    "extra_keywords": [],
    "locations": [],
    "summer_only": True,
    "internship_only": True,
    "skip_external_applications": True,
    "min_match_score": 0.22,
    "max_applications_per_run": 15,
    "max_search_pages": 4,
    "delay_between_applications_seconds": [20, 45],
    "auto_submit": False,
    "headless": False,
}


# --------------------------------------------------------------------- plumbing


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{path.name} is not valid JSON: {exc}")


def load_config(args: argparse.Namespace) -> dict[str, Any]:
    config = dict(DEFAULT_CONFIG)
    user_config = load_json(HERE / "config.json", {})
    if isinstance(user_config, dict):
        config.update({k: v for k, v in user_config.items() if not k.startswith("_")})

    # Command line beats the config file.
    for key, value in (
        ("resume_path", getattr(args, "resume", None)),
        ("major", getattr(args, "major", None)),
        ("handshake_base_url", getattr(args, "base_url", None)),
        ("min_match_score", getattr(args, "min_score", None)),
        ("max_applications_per_run", getattr(args, "max", None)),
        ("max_search_pages", getattr(args, "pages", None)),
    ):
        if value is not None:
            config[key] = value

    if getattr(args, "auto_submit", False):
        config["auto_submit"] = True
    if getattr(args, "headless", False):
        config["headless"] = True
    if getattr(args, "location", None):
        config["locations"] = args.location
    return config


def load_selectors() -> dict[str, list[str]]:
    data = load_json(HERE / "selectors.json", {})
    if not isinstance(data, dict):
        raise SystemExit("selectors.json must contain a JSON object.")
    return {k: v for k, v in data.items() if isinstance(v, list)}


def banner(text: str) -> None:
    print("\n" + text)
    print("-" * min(len(text), 70))


# ------------------------------------------------------------------ candidates


def gather_candidates(
    session: HandshakeSession,
    config: dict[str, Any],
    profile: resume_parser.ResumeProfile | None,
    major: majors.MajorProfile,
    ledger: Ledger,
    limit_scan: int,
) -> list[tuple[Job, matcher.MatchResult]]:
    """Search, open each posting, filter it, and return survivors ranked by score."""
    weights = matcher.build_weights(
        profile, major.keywords, list(config.get("extra_keywords", []))
    )
    print(f"Scoring vocabulary: {len(weights)} terms")

    queries = list(major.queries) or [f"{major.name} intern"]
    banner(f"Searching Handshake for {major.name} internships")
    job_ids = session.collect_job_ids(queries, int(config.get("max_search_pages", 4)))
    print(f"\n{len(job_ids)} unique postings found across {len(queries)} searches.")

    fresh = [j for j in job_ids if not ledger.applied(j)]
    if len(fresh) != len(job_ids):
        print(f"{len(job_ids) - len(fresh)} already applied to in a previous run.")
    fresh = fresh[:limit_scan]

    banner(f"Reviewing {len(fresh)} postings")
    kept: list[tuple[Job, matcher.MatchResult]] = []
    min_score = float(config.get("min_match_score", 0.22))

    for index, job_id in enumerate(fresh, start=1):
        job = session.load_job(job_id)
        prefix = f"[{index}/{len(fresh)}]"

        if job.error:
            print(f"{prefix} {job_id}: {job.error}")
            continue
        if not job.title:
            print(f"{prefix} {job_id}: no title found, skipping")
            continue

        if config.get("internship_only", True) and not matcher.is_internship(
            job.title, job.description
        ):
            print(f"{prefix} skip (not an internship): {job.title[:58]}")
            continue
        if config.get("summer_only", True) and not matcher.is_summer(
            job.title, job.description
        ):
            print(f"{prefix} skip (not summer): {job.title[:58]}")
            continue
        if not matcher.location_ok(job.location, list(config.get("locations", []))):
            print(f"{prefix} skip (location {job.location[:26]}): {job.title[:40]}")
            continue
        if config.get("skip_external_applications", True) and job.apply_kind == "external":
            print(f"{prefix} skip (external application): {job.title[:48]}")
            ledger.record(
                job_id, "skipped_external", job.title, job.employer,
                job.location, job.url, 0.0, "external ATS",
            )
            continue

        result = matcher.score_job(job.search_text, weights)
        if result.score < min_score:
            print(f"{prefix} skip ({result.percent}% match): {job.title[:52]}")
            continue

        print(f"{prefix} KEEP ({result.percent}%): {job.label()[:68]}")
        kept.append((job, result))

    kept.sort(key=lambda pair: -pair[1].score)
    return kept


# -------------------------------------------------------------------- commands


def resolve_inputs(
    config: dict[str, Any], need_resume: bool = True
) -> tuple[resume_parser.ResumeProfile | None, majors.MajorProfile]:
    profile: resume_parser.ResumeProfile | None = None
    resume_path = Path(str(config.get("resume_path", ""))).expanduser()

    if need_resume:
        if not str(resume_path):
            raise SystemExit("No resume given. Use --resume path/to/resume.pdf")
        if not resume_path.is_absolute():
            candidate = HERE / resume_path
            if candidate.exists():
                resume_path = candidate
        if not resume_path.exists():
            raise SystemExit(
                f"Resume not found: {resume_path}\n"
                "Pass a real path with --resume, for example:\n"
                '  python main.py apply --resume "C:\\Users\\you\\Documents\\resume.pdf"'
            )
        banner("Reading resume")
        profile = resume_parser.extract(resume_path)
        print(profile.summary())
        config["resume_path"] = str(resume_path)

    major = majors.prompt_for_major(str(config.get("major", "")))
    return profile, major


def cmd_login(args: argparse.Namespace) -> int:
    config = load_config(args)
    with HandshakeSession(config, load_selectors(), HERE / "data" / "browser_profile") as session:
        ok = session.ensure_logged_in()
        if ok:
            print("Session stored. Later runs will reuse it.")
            time.sleep(2)
    return 0 if ok else 1


def cmd_search(args: argparse.Namespace) -> int:
    config = load_config(args)
    profile, major = resolve_inputs(config)
    ledger = Ledger(HERE / "data" / "applied.json")

    with HandshakeSession(config, load_selectors(), HERE / "data" / "browser_profile") as session:
        if not session.ensure_logged_in():
            return 1
        kept = gather_candidates(
            session, config, profile, major, ledger, int(args.scan)
        )

    banner(f"{len(kept)} matching summer internships, best first")
    rows = []
    for rank, (job, result) in enumerate(kept, start=1):
        print(f"{rank:3d}. {result.percent:3d}%  {job.label()}")
        print(f"      {job.url}")
        print(f"      matched: {', '.join(result.matched[:10]) or 'none'}")
        rows.append(
            {
                "rank": rank,
                "score_percent": result.percent,
                "title": job.title,
                "employer": job.employer,
                "location": job.location,
                "apply_kind": job.apply_kind,
                "url": job.url,
                "top_matches": "; ".join(result.matched[:12]),
            }
        )

    if rows:
        out = write_report(rows, HERE / "data" / args.report)
        print(f"\nReport written to {out}")
    else:
        print("\nNothing cleared the filters. Try --min-score 0.12 or more --pages.")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    config = load_config(args)
    profile, major = resolve_inputs(config)
    ledger = Ledger(HERE / "data" / "applied.json")
    auto = bool(config.get("auto_submit", False))
    dry_run = bool(args.dry_run)

    banner("Run settings")
    print(f"  major            : {major.name}")
    print(f"  minimum match    : {round(float(config['min_match_score']) * 100)}%")
    print(f"  cap this run     : {config['max_applications_per_run']} applications")
    print(f"  mode             : "
          + ("dry run, nothing submitted" if dry_run
             else "auto-submit" if auto else "confirm each application"))
    print(f"  already applied  : {ledger.total_applied()} total, {ledger.applied_today()} today")

    if auto and not dry_run:
        print(
            "\nAuto-submit is on. Applications will be sent in your name without "
            "further prompts."
        )
        if input("Type 'yes' to continue: ").strip().lower() != "yes":
            print("Cancelled.")
            return 1

    with HandshakeSession(config, load_selectors(), HERE / "data" / "browser_profile") as session:
        if not session.ensure_logged_in():
            return 1

        kept = gather_candidates(
            session, config, profile, major, ledger, int(args.scan)
        )
        if not kept:
            print("\nNo postings cleared the filters. Nothing to apply to.")
            return 0

        cap = int(config.get("max_applications_per_run", 15))
        delay_low, delay_high = (
            list(config.get("delay_between_applications_seconds", [20, 45])) + [45]
        )[:2]

        banner(f"Applying to up to {cap} of {len(kept)} matches")
        submitted = 0
        counts: dict[str, int] = {}

        for position, (job, result) in enumerate(kept):
            is_last = position == len(kept) - 1
            if submitted >= cap:
                print(f"\nReached the cap of {cap} applications for this run.")
                break

            print(f"\n{result.percent}% match  {job.label()}")
            print(f"  {job.url}")
            print(f"  matched: {', '.join(result.matched[:8]) or 'none'}")

            if not auto and not dry_run:
                answer = input("  Apply to this one? [y]es / [n]o / [q]uit: ").strip().lower()
                if answer.startswith("q"):
                    print("  Stopping here.")
                    break
                if not answer.startswith("y"):
                    ledger.record(
                        job.job_id, "declined", job.title, job.employer,
                        job.location, job.url, result.score, "student declined",
                    )
                    counts["declined"] = counts.get("declined", 0) + 1
                    continue

            status, note = session.apply(
                job,
                config["resume_path"],
                str(config.get("resume_doc_name", "")),
                dry_run=dry_run,
            )
            counts[status] = counts.get(status, 0) + 1
            ledger.record(
                job.job_id, status, job.title, job.employer,
                job.location, job.url, result.score, note,
            )
            print(f"  -> {status}: {note}")

            if status in {"applied", "dry_run", "uncertain"}:
                submitted += 1
                # Pace real submissions only, and never after the last one.
                if not dry_run and not is_last and submitted < cap:
                    pause = random.uniform(float(delay_low), float(delay_high))
                    print(f"  waiting {pause:.0f}s before the next one")
                    time.sleep(pause)

    banner("Run summary")
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:24s} {count}")
    print(f"\nLedger: {ledger.total_applied()} applications recorded in total.")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    ledger = Ledger(HERE / "data" / "applied.json")
    out = ledger.export_csv(HERE / "data" / args.out)
    print(f"{ledger.total_applied()} applications recorded.")
    print(f"Full history written to {out}")
    return 0


def cmd_majors(args: argparse.Namespace) -> int:
    print("Majors with tuned search terms:\n")
    for name in majors.list_majors():
        print(f"  {name}")
    print("\nAny other major works too; the tool searches on the name you type.")
    return 0


# ------------------------------------------------------------------------- cli


def add_shared_arguments(sub: argparse.ArgumentParser) -> None:
    sub.add_argument("--resume", help="path to your resume (PDF, DOCX or TXT)")
    sub.add_argument("--major", help="skip the prompt and target this major")
    sub.add_argument(
        "--location",
        action="append",
        help="preferred city or state; repeatable. Remote postings always pass.",
    )
    sub.add_argument(
        "--min-score",
        type=float,
        dest="min_score",
        help="match threshold from 0 to 1 (default 0.22)",
    )
    sub.add_argument(
        "--pages", type=int, help="search result pages to read per query (default 4)"
    )
    sub.add_argument(
        "--scan",
        type=int,
        default=60,
        help="maximum postings to open and score in one run (default 60)",
    )
    sub.add_argument("--base-url", dest="base_url", help="your school's Handshake URL")
    sub.add_argument(
        "--headless", action="store_true", help="hide the browser window"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="handshake-intern-bot",
        description="Find and apply to summer internships on Handshake for a given major.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Handshake restricts automated access. Keep volumes modest, review what "
            "goes out in your name, and stop if your school asks you to."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    login = subparsers.add_parser("login", help="sign in once and save the session")
    add_shared_arguments(login)
    login.set_defaults(func=cmd_login)

    search = subparsers.add_parser(
        "search", help="rank matching internships without applying"
    )
    add_shared_arguments(search)
    search.add_argument(
        "--report", default="matches.csv", help="CSV filename written into data/"
    )
    search.set_defaults(func=cmd_search)

    apply_cmd = subparsers.add_parser("apply", help="apply to matching internships")
    add_shared_arguments(apply_cmd)
    apply_cmd.add_argument(
        "--max", type=int, help="cap applications this run (default 15)"
    )
    apply_cmd.add_argument(
        "--auto-submit",
        action="store_true",
        dest="auto_submit",
        help="submit without asking before each application",
    )
    apply_cmd.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="open and fill each application but never submit",
    )
    apply_cmd.set_defaults(func=cmd_apply)

    history = subparsers.add_parser("history", help="export the application ledger")
    history.add_argument("--out", default="history.csv", help="CSV filename in data/")
    history.set_defaults(func=cmd_history)

    majors_cmd = subparsers.add_parser("majors", help="list majors with tuned searches")
    majors_cmd.set_defaults(func=cmd_majors)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nInterrupted. Progress so far is saved in data/applied.json.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
