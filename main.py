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
import os
import random
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import majors
import matcher
import resume_parser
import tailor
from handshake import Documents, HandshakeSession, Job
from storage import MANUAL_FOLDER_NAME, Ledger, ManualList, write_followups, write_report

HERE = Path(__file__).resolve().parent


def manual_list() -> ManualList:
    """The lasting list of good internships to apply to by hand."""
    folder = os.environ.get("HSBOT_MANUAL_DIR") or str(HERE / MANUAL_FOLDER_NAME)
    return ManualList(folder)


class ResumeTailor:
    """Makes a resume for each job when tailoring is switched on."""

    def __init__(self, config: dict[str, Any]) -> None:
        self.enabled = bool(config.get("tailor_resume", False))
        self.use_ai = False
        self.profile_path = Path(str(config.get("profile_path") or tailor.PROFILE_PATH))
        if not self.enabled:
            return
        if not self.profile_path.exists():
            print(f"[warn] Resume tailoring is on, but there is no profile at {self.profile_path}.")
            print("       Using your default resume instead.")
            self.enabled = False
            return
        if config.get("use_ai_for_tailoring", True):
            self.use_ai, why = tailor.claude_ready()
            how = "Claude, checked against your profile" if self.use_ai else "rules from your profile"
            print(f"Resume tailoring: {how}. {why}")
        else:
            print("Resume tailoring: rules from your profile (AI switched off).")

    def saved_answers(self) -> list[dict[str, Any]]:
        """Simple application answers from the profile, if there are any."""
        if not self.profile_path.exists():
            return []
        try:
            profile = tailor.load_profile(self.profile_path)
        except Exception:
            return []
        answers = profile.get("application_answers", [])
        return answers if isinstance(answers, list) else []

    def for_job(self, job: Job) -> Path | None:
        if not self.enabled:
            return None
        print("  tailoring your resume for this job...")
        try:
            result = tailor.tailor_resume(
                job.job_id, job.title, job.employer, job.description,
                use_ai=self.use_ai, profile_path=self.profile_path,
            )
        except Exception as exc:  # never let tailoring stop an application run
            print(f"  [warn] tailoring failed ({type(exc).__name__}: {exc}); using your default resume")
            return None
        how = {"ai": "Claude", "rules": "rules", "saved": "made earlier"}.get(result.method, result.method)
        print(f"  tailored resume ({how}): {result.path}")
        for note in result.notes[:3]:
            print(f"    {note}")
        return result.path


def make_question_asker(interactive: bool) -> Any:
    """Ask the student an application question during the run, or skip it."""
    if not interactive:
        return None

    def ask(question: str) -> str | None:
        print(f"\n  This application asks: {question}")
        print("  Type your answer and press Enter, or press Enter to leave it blank.")
        try:
            reply = input("  Your answer: ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return reply or None

    return ask


def save_learned_answers(profile_path: Path, learned: list[dict[str, Any]]) -> int:
    """Add answers the student typed during the run to their profile."""
    if not learned or not profile_path.exists():
        return 0
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    existing = profile.setdefault("application_answers", [])
    known = {str(p).lower() for a in existing for p in a.get("match", [])}
    added = 0
    for answer in learned:
        phrases = [str(p).lower() for p in answer.get("match", [])]
        if any(p in known for p in phrases):
            continue
        existing.append(answer)
        known.update(phrases)
        added += 1
    if added:
        try:
            profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            return 0
    return added


def add_to_manual_list(
    manual: ManualList, tailorer: ResumeTailor, job: Job, result: matcher.MatchResult, reason: str
) -> None:
    resume = tailorer.for_job(job) if tailorer.enabled else None
    manual.add(job.job_id, job.title, job.employer, job.location, job.url,
               result.score, reason, resume_path=str(resume or ""))


def manual_reason(status: str, note: str) -> str:
    if status == "skipped_external":
        return "Apply on the employer's website"
    if status == "no_apply_button":
        return "No Apply button found on Handshake"
    if status == "needs_manual":
        detail = note.split("unanswered required:", 1)[-1].strip()
        return f"Asks for something the assistant can't fill in: {detail}"
    if status == "uncertain":
        return f"May already be submitted, check the posting before applying ({note})"
    return f"The assistant couldn't finish this one ({note})"

DEFAULT_CONFIG: dict[str, Any] = {
    "handshake_base_url": "https://app.joinhandshake.com",
    "search_url_template": "",
    "resume_path": "resume.pdf",
    "resume_doc_name": "",
    "cover_letter_path": "",
    "cover_letter_doc_name": "",
    "transcript_path": "",
    "transcript_doc_name": "",
    "major": "",
    "extra_keywords": [],
    "locations": [],
    "summer_only": True,
    "include_undated_internships": True,
    "internship_only": True,
    "min_match_score": 0.25,
    "max_applications_per_run": 15,
    "max_search_pages": 4,
    "delay_between_applications_seconds": [20, 45],
    "auto_submit": False,
    "tailor_resume": False,
    "answer_questions": True,
    "use_ai_for_tailoring": True,
    "profile_path": "",
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
    strictness = getattr(args, "strictness", None)
    if strictness:
        config["min_match_score"] = matcher.STRICTNESS[strictness]

    for key, value in (
        ("resume_path", getattr(args, "resume", None)),
        ("cover_letter_path", getattr(args, "cover_letter", None)),
        ("transcript_path", getattr(args, "transcript", None)),
        ("major", getattr(args, "major", None)),
        ("handshake_base_url", getattr(args, "base_url", None)),
        ("min_match_score", getattr(args, "min_score", None)),
        ("max_applications_per_run", getattr(args, "max", None)),
        ("max_search_pages", getattr(args, "pages", None)),
    ):
        if value is not None:
            config[key] = value

    if getattr(args, "tailor_resume", False):
        config["tailor_resume"] = True
    if getattr(args, "no_ai", False):
        config["use_ai_for_tailoring"] = False
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
    # Postings are scored on the major's fixed preset, never on the resume.
    extra = [str(k) for k in config.get("extra_keywords", []) if str(k).strip()]
    if extra:
        major = replace(major, core=list(dict.fromkeys(major.core + extra)))
    min_score = float(config.get("min_match_score", matcher.STRICTNESS["balanced"]))
    print(f"Scoring on the {major.name} preset: {len(major.core)} core terms, "
          f"{len(major.related)} related terms, {len(major.title_words)} title words.")
    print(f"Postings that name the major ({', '.join(major.anchors[:2])}) nearly always pass.")
    print(f"Minimum match: {round(min_score * 100)}%")

    queries = list(major.queries) or [f"{major.name} intern"]
    banner(f"Searching Handshake for {major.name} internships")
    job_ids = session.collect_job_ids(queries, int(config.get("max_search_pages", 4)))
    print(f"\n{len(job_ids)} unique postings found across {len(queries)} searches.")
    if not job_ids:
        print(
            "No postings were listed. If Handshake shows internships for these searches\n"
            "in your normal browser, its page layout has probably changed. See\n"
            "'When it breaks' in the README."
        )

    fresh = [j for j in job_ids if not ledger.applied(j)]
    if len(fresh) != len(job_ids):
        print(f"{len(job_ids) - len(fresh)} already applied to in a previous run.")
    fresh = fresh[:limit_scan]

    banner(f"Reviewing {len(fresh)} postings")
    kept: list[tuple[Job, matcher.MatchResult]] = []

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
            job.title, job.description, bool(config.get("include_undated_internships", True))
        ):
            print(f"{prefix} skip (not summer): {job.title[:58]}")
            continue
        if not matcher.location_ok(job.location, list(config.get("locations", []))):
            print(f"{prefix} skip (location {job.location[:26]}): {job.title[:40]}")
            continue
        if job.apply_kind in {"already_applied", "closed"}:
            print(f"{prefix} skip ({job.apply_kind.replace('_', ' ')}): {job.title[:48]}")
            continue

        result = matcher.score_posting(job.title, f"{job.employer} {job.location} {job.description}", major)
        if not matcher.passes(result, min_score):
            print(f"{prefix} skip ({result.percent}% match): {job.title[:52]}")
            continue

        tag = "KEEP, apply on employer site" if job.apply_kind == "external" else "KEEP"
        print(f"{prefix} {tag} ({result.percent}%): {job.label()[:60]}")
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

        for key, label in (("cover_letter_path", "Cover letter"), ("transcript_path", "Transcript")):
            raw = str(config.get(key, "") or "").strip()
            if not raw:
                continue
            path = Path(raw).expanduser()
            if not path.is_absolute() and (HERE / path).exists():
                path = HERE / path
            if path.exists():
                config[key] = str(path)
                print(f"  {label.lower():16s}: {path.name}")
            else:
                print(f"[warn] {label} not found, ignoring: {path}")
                config[key] = ""

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

    manual = manual_list()
    tailorer = ResumeTailor(config)
    for job, result in kept:
        if job.apply_kind in {"external", "unknown"}:
            status = "skipped_external" if job.apply_kind == "external" else "no_apply_button"
            if tailorer.enabled:
                print(f"\n{job.label()}")
            add_to_manual_list(manual, tailorer, job, result, manual_reason(status, ""))
    if len(manual):
        page = manual.save()
        print(f"\nInternships to apply to yourself ({len(manual)}): {page}")

    if rows:
        out = write_report(rows, HERE / "data" / args.report)
        print(f"\nReport written to {out}")
    else:
        print("\nNothing cleared the filters. Try --strictness broad or more --pages.")
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

    tailorer = ResumeTailor(config)

    with HandshakeSession(config, load_selectors(), HERE / "data" / "browser_profile") as session:
        if not session.ensure_logged_in():
            return 1

        matches = gather_candidates(
            session, config, profile, major, ledger, int(args.scan)
        )
        if not matches:
            print("\nNo postings cleared the filters. Nothing to apply to.")
            return 0

        # Postings that hand off to the employer's own site can't be filled in
        # here, but good matches are still worth the student's time.
        followups: list[dict[str, Any]] = []
        manual = manual_list()
        # Filled in after the applications, so tailoring never delays them.
        pending_manual: list[tuple[Job, matcher.MatchResult, str]] = []
        kept = []
        for job, result in matches:
            if job.apply_kind == "external":
                ledger.record(
                    job.job_id, "skipped_external", job.title, job.employer,
                    job.location, job.url, result.score, "apply on employer site",
                )
                followups.append(followup_row(job, result, "apply on employer site"))
                pending_manual.append((job, result, manual_reason("skipped_external", "")))
            else:
                kept.append((job, result))

        documents = Documents(
            resume_path=str(config.get("resume_path", "")),
            resume_name=str(config.get("resume_doc_name", "")),
            cover_letter_path=str(config.get("cover_letter_path", "")),
            cover_letter_name=str(config.get("cover_letter_doc_name", "")),
            transcript_path=str(config.get("transcript_path", "")),
            transcript_name=str(config.get("transcript_doc_name", "")),
        )

        answer_questions = bool(config.get("answer_questions", True))
        answers = tailorer.saved_answers() if answer_questions else []
        interactive = answer_questions and sys.stdin is not None and sys.stdin.isatty()
        ask = make_question_asker(interactive)
        learned_answers: list[dict[str, Any]] = []
        if answer_questions:
            print(f"\nSaved answers available for {len(answers)} kinds of question.")
            if interactive:
                print("Anything else it asks, it will ask you here, and remember your answer.")
            else:
                print("Unanswered questions will hold an application back for you to finish.")
            print("Social security numbers and financial details are never filled in.")

        cap = int(config.get("max_applications_per_run", 15))
        delay_low, delay_high = (
            list(config.get("delay_between_applications_seconds", [20, 45])) + [45]
        )[:2]

        banner(f"Applying to up to {cap} of {len(kept)} Handshake-hosted matches")
        if followups:
            print(f"{len(followups)} more matches apply on the employer's own site.")
        submitted = 0
        counts: dict[str, int] = {}
        if followups:
            counts["skipped_external"] = len(followups)

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

            job_documents = documents
            tailored = tailorer.for_job(job)
            if tailored is not None:
                job_documents = replace(documents, resume_path=str(tailored), tailored_resume=True)

            status, note = session.apply(
                job, job_documents, dry_run=dry_run, answers=answers, ask=ask
            )
            if session.last_learned_answers:
                learned_answers += session.last_learned_answers
                answers = answers + session.last_learned_answers
            counts[status] = counts.get(status, 0) + 1
            ledger.record(
                job.job_id, status, job.title, job.employer,
                job.location, job.url, result.score, note,
            )
            print(f"  -> {status}: {note}")

            if status in {"needs_manual", "failed", "uncertain"}:
                followups.append(followup_row(job, result, f"{status}: {note}"))
                pending_manual.append((job, result, manual_reason(status, note)))
            elif status == "applied":
                manual.remove(job.job_id)

            if status in {"applied", "dry_run", "uncertain"}:
                submitted += 1
                # Pace real submissions only, and never after the last one.
                if not dry_run and not is_last and submitted < cap:
                    pause = random.uniform(float(delay_low), float(delay_high))
                    print(f"  waiting {pause:.0f}s before the next one")
                    time.sleep(pause)

    if pending_manual:
        if tailorer.enabled:
            banner(f"Tailoring resumes for {len(pending_manual)} internships to apply to yourself")
        for job, result, reason in pending_manual:
            if tailorer.enabled:
                print(f"\n{job.label()}")
            add_to_manual_list(manual, tailorer, job, result, reason)

    saved = save_learned_answers(tailorer.profile_path, learned_answers)
    if saved:
        print(f"\nSaved {saved} of your answers for next time in {tailorer.profile_path}")

    banner("Run summary")
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {status:24s} {count}")
    print(f"\nLedger: {ledger.total_applied()} applications recorded in total.")

    if followups:
        followups.sort(key=lambda row: -int(row["score_percent"]))
        out = write_followups(followups, HERE / "data" / "follow_up.csv")
        banner(f"{len(followups)} good matches to finish by hand")
        for row in followups:
            print(f"  {row['score_percent']:3d}%  {row['title']} @ {row['employer']}")
            print(f"        {row['reason']}")
            print(f"        {row['url']}")
        print(f"\nSaved to {out}")

    if len(manual) or manual.json_path.exists():
        page = manual.save()
        print(f"\nAll internships to apply to yourself ({len(manual)} so far): {page}")
    return 0


def followup_row(job: Job, result: matcher.MatchResult, reason: str) -> dict[str, Any]:
    return {
        "score_percent": result.percent,
        "title": job.title,
        "employer": job.employer,
        "location": job.location,
        "reason": reason,
        "url": job.url,
    }


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
    sub.add_argument(
        "--cover-letter",
        dest="cover_letter",
        help="local cover letter uploaded when a posting requires one",
    )
    sub.add_argument(
        "--transcript",
        help="local transcript uploaded when a posting requires one",
    )
    sub.add_argument("--major", help="skip the prompt and target this major")
    sub.add_argument(
        "--tailor-resume",
        action="store_true",
        dest="tailor_resume",
        help="make a resume for each job from profile/career_profile.json",
    )
    sub.add_argument(
        "--no-ai",
        action="store_true",
        dest="no_ai",
        help="tailor with rules only, without Claude",
    )
    sub.add_argument(
        "--location",
        action="append",
        help="preferred city or state; repeatable. Remote postings always pass.",
    )
    sub.add_argument(
        "--strictness",
        choices=sorted(matcher.STRICTNESS),
        help="how picky matching is: broad (15%%), balanced (25%%, default) or strict (40%%)",
    )
    sub.add_argument(
        "--min-score",
        type=float,
        dest="min_score",
        help="exact match threshold from 0 to 1; overrides --strictness",
    )
    sub.add_argument(
        "--pages", type=int, help="search result pages to read per query (default 4)"
    )
    sub.add_argument(
        "--scan",
        type=int,
        default=300,
        help="maximum postings to open and score in one run (default 300)",
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
