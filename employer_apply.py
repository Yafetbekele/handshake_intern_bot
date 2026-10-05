"""Apply on employers' own sites, working down the ranked lists.

    python employer_apply.py                     the ranked Handshake list, asking before each one is sent
    python employer_apply.py --list elsewhere    the "Found elsewhere" list instead
    python employer_apply.py --list both --max 20
    python employer_apply.py --dry-run           fill each form in, send nothing
    python employer_apply.py --auto-submit       send complete forms without asking
    python employer_apply.py --links-only        only look up where each posting applies

rank_all.py and find_elsewhere.py make the lists. For each posting, best fit
first, this finds the employer's application form (for a Handshake posting, by
reading the employer's address from the posting's own page), fills it in from
your saved answers, attaches your resume, and shows you the filled form in the
browser before anything is sent. See employer_sites.py for what it will and
won't fill in.

What happened to each posting is kept in data/employer_applications.json and
"employer_site_results.csv" in the results folder. A posting it applied to is
marked Applied, exactly like the button on the ranked pages.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import applied_myself
import cover_letter
import essay_answers
import form_answers
import posting_cache
import prompts
import tailor
from employer_sites import (
    FORM_SITES,
    DocumentSource,
    EmployerSite,
    EssayWriter,
    WorkOut,
    pick_option,
    profile_answers,
    remember_answers,
    site_kind,
)
from handshake import HandshakeSession
from main import DATA_DIR, banner, load_config, load_selectors, manual_list

# Not offered again on later runs (--retry brings back everything but "applied").
SETTLED = {"applied", "declined", "uncertain", "needs_account", "closed", "no_form"}

CLOSED_MESSAGE = ("\nThe browser window was closed, so the run stops here. "
                  "What was done is saved; run again to carry on.")

SAYS = {
    "applied": "applied",
    "dry_run": "filled in, not sent (practice run)",
    "declined": "skipped, you said no",
    "later": "left for another run",
    "uncertain": "sent, but no confirmation was seen",
    "needs_manual": "left for you to finish",
    "needs_account": "needs your own account on the employer's site",
    "closed": "no longer open",
    "no_form": "no application form found",
    "no_link": "couldn't find the employer's link",
    "failed": "didn't work",
}


# ------------------------------------------------------------------- the lists


def ranked_postings() -> list[dict[str, Any]]:
    """The Handshake postings that apply on the employer's site, in rank_all.py's order."""
    path = manual_list().folder / "all_ranked.csv"
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except OSError:
        raise SystemExit("There's no ranked list yet. Run rank_all.py first, then try again.")
    postings = []
    for row in rows:
        url = row.get("url", "")
        job_id = url.rstrip("/").rsplit("/", 1)[-1].split("?")[0]
        if not job_id:
            continue
        saved = posting_cache.load(job_id) or {}
        postings.append({
            "id": job_id, "title": row.get("title", ""), "employer": row.get("employer", ""),
            "location": row.get("location", ""), "url": url, "description": saved.get("description", ""),
            "score": float(row.get("score") or 0), "source": "ranked",
        })
    return postings


def elsewhere_postings() -> list[dict[str, Any]]:
    """The postings find_elsewhere.py found on company career sites, in its order."""
    try:
        found = json.loads((DATA_DIR / "elsewhere_ranked.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise SystemExit('There\'s no "Found elsewhere" list yet. Run find_elsewhere.py first, then try again.')
    return [dict(p, score=float(p.get("score") or 0), source="elsewhere") for p in found if p.get("id") and p.get("url")]


def chosen_postings(which: str) -> list[dict[str, Any]]:
    if which == "ranked":
        return ranked_postings()
    if which == "elsewhere":
        return elsewhere_postings()
    return sorted(ranked_postings() + elsewhere_postings(), key=lambda p: -p["score"])


# ------------------------------------------------------------------ the record


class Record:
    """What happened to each posting, so later runs carry on instead of starting over."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.entries: dict[str, dict[str, Any]] = raw if isinstance(raw, dict) else {}

    def status(self, posting_id: str) -> str:
        return str(self.entries.get(posting_id, {}).get("status", ""))

    def note(self, posting: dict[str, Any], status: str, note: str, link: str = "", site: str = "",
             filled: list[str] | None = None, answers: int = 0) -> None:
        self.entries[posting["id"]] = {
            "status": status, "note": note[:400], "title": posting["title"], "employer": posting["employer"],
            "listing": posting["url"], "link": link, "site": site, "list": posting["source"],
            # Which questions were answered, never the answers themselves.
            "answered": [f.split(":", 1)[0] for f in (filled or [])][:60],
            "answers": answers,  # how many saved answers there were, to know when a retry could go better
            "when": datetime.now().isoformat(timespec="seconds"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=1, ensure_ascii=False), encoding="utf-8")

    def forget(self, posting_id: str, status: str) -> None:
        """Drop a posting's entry if it still says `status`, now that it no longer holds."""
        if self.status(posting_id) == status:
            del self.entries[posting_id]
            self.path.write_text(json.dumps(self.entries, indent=1, ensure_ascii=False), encoding="utf-8")

    def write_csv(self, folder: Path) -> Path:
        folder.mkdir(parents=True, exist_ok=True)
        out = folder / "employer_site_results.csv"
        order = {status: rank for rank, status in enumerate(SAYS)}
        rows = sorted(self.entries.values(), key=lambda e: (order.get(e["status"], 99), e.get("when", "")))
        with out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["what happened", "title", "employer", "site", "where to apply", "note", "when"])
            for e in rows:
                writer.writerow([SAYS.get(e["status"], e["status"]), e["title"], e["employer"], e.get("site", ""),
                                 e.get("link") or e.get("listing", ""), e.get("note", ""), e.get("when", "")])
        return out


class Links:
    """Where each Handshake posting's "Apply externally" button leads, looked up once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = {}
        self.known: dict[str, str] = raw if isinstance(raw, dict) else {}

    def get(self, job_id: str) -> str:
        return self.known.get(job_id, "")

    def keep(self, job_id: str, address: str) -> None:
        self.known[job_id] = address
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.known, indent=1), encoding="utf-8")


# ------------------------------------------------------------------- documents


class Papers:
    """The resume, cover letter and transcript to attach, per posting."""

    def __init__(self, config: dict[str, Any], args: argparse.Namespace) -> None:
        try:
            settings = json.loads((DATA_DIR / "launcher_settings.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            settings = {}
        self.resume = self._existing(getattr(args, "resume", None), config.get("resume_path"), settings.get("resume"))
        self.transcript = self._existing(config.get("transcript_path"), settings.get("transcript"))
        self.own_letter = self._existing(config.get("cover_letter_path"), settings.get("cover_letter"))
        self.tailor = bool(config.get("tailor_resume"))
        self.letters = "never" if not config.get("write_cover_letters", True) else (
            "always" if getattr(args, "cover_letters", False) else "required")
        self.allow_ai = bool(config.get("use_ai_for_tailoring", True))
        self._ai: bool | None = None
        self.can_write = tailor.PROFILE_PATH.exists()
        self.essays = self.can_write and self.allow_ai and not getattr(args, "no_essays", False)
        self.working_out = self.can_write and self.allow_ai and not getattr(args, "ask_me", False)

    @staticmethod
    def _existing(*paths: Any) -> Path | None:
        for raw in paths:
            if str(raw or "").strip():
                path = Path(str(raw)).expanduser()
                if path.is_file():
                    return path
        return None

    def _use_ai(self) -> bool:
        if self._ai is None:  # checked on first use, only if something is to be written
            self._ai = self.allow_ai and tailor.claude_ready()[0]
        return self._ai

    @staticmethod
    def _folder_id(posting: dict[str, Any]) -> str:
        return posting["id"].replace("/", "-")[:60]

    def _made_earlier(self, posting: dict[str, Any], ending: str) -> Path | None:
        for folder in tailor.OUT_DIR.glob(f"{self._folder_id(posting)}-*"):
            for path in folder.glob(f"*{ending}"):
                return path
        return None

    def _resume_for(self, posting: dict[str, Any]) -> Path | None:
        made = self._made_earlier(posting, "_Resume.pdf")
        if made is not None:
            return made
        if self.tailor and self.can_write and posting["description"]:
            print("  tailoring your resume for this job...")
            try:
                return tailor.tailor_resume(self._folder_id(posting), posting["title"], posting["employer"],
                                            posting["description"], use_ai=self._use_ai()).path
            except Exception as exc:  # never let tailoring stop an application
                print(f"  [warn] tailoring failed ({type(exc).__name__}); using your usual resume")
        return self.resume

    def _letter_for(self, posting: dict[str, Any], required: bool) -> Path | None:
        if self.own_letter is not None:
            return self.own_letter
        made = self._made_earlier(posting, "_Cover_Letter.pdf")
        if made is not None:
            return made
        if self.letters == "never" or not self.can_write or not posting["description"]:
            return None
        if not required and self.letters != "always":
            return None  # an optional slot is left empty unless letters were asked for
        print("  writing a cover letter for this job...")
        try:
            return cover_letter.cover_letter(self._folder_id(posting), posting["title"], posting["employer"],
                                             posting["description"], use_ai=self._use_ai()).path
        except Exception as exc:
            print(f"  [warn] couldn't write the cover letter ({type(exc).__name__})")
            return None

    def essays_for(self, posting: dict[str, Any]) -> EssayWriter | None:
        """Writes answers to this posting's open questions from the profile, or None when that's off."""
        if not self.essays:
            return None

        def write(question: str, max_chars: int, single_line: bool) -> str | None:
            if not self._use_ai():  # Claude Code missing or signed out: the question is asked instead
                return None
            print(f"  writing an answer to \"{question[:70]}\"...")
            try:
                result = essay_answers.answer(self._folder_id(posting), question, posting["title"],
                                              posting["employer"], posting["description"],
                                              max_chars=max_chars, single_line=single_line)
            except Exception as exc:  # never let an essay stop an application
                print(f"  [warn] couldn't write it ({type(exc).__name__})")
                return None
            for note in result.notes[:3]:
                print(f"    {note}")
            return result.text or None

        return write

    def answers_for(self, posting: dict[str, Any]) -> WorkOut | None:
        """Settles a form's leftover questions from the profile, or None when that's off."""
        if not self.working_out:
            return None
        found_on = "Handshake" if posting["source"] == "ranked" else "the company's own careers website"

        def settle(questions: list[dict[str, Any]]) -> dict[int, tuple[Any, str]]:
            if not self._use_ai():  # Claude Code missing or signed out: the questions are asked instead
                return {}
            print(f"  working out {len(questions)} more answer{'s' if len(questions) != 1 else ''} from your profile...")
            # Read afresh, so answers you typed earlier in this run count too.
            profile = tailor.load_profile(tailor.PROFILE_PATH)
            settled, notes = form_answers.work_out(questions, profile, posting["title"], posting["employer"], found_on)
            for note in notes[:4]:
                print(f"    not used: {note}")
            return settled

        return settle

    def for_posting(self, posting: dict[str, Any]) -> DocumentSource:
        def give(kind: str, required: bool) -> Path | None:
            if kind == "resume":
                return self._resume_for(posting)
            if kind == "cover_letter":
                return self._letter_for(posting, required)
            if kind == "transcript":
                return self.transcript
            return None

        return give


# ------------------------------------------------------------------- questions


class Questions:
    """Puts a form's unanswered questions to the student at the keyboard."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.quiet = False  # nobody answered; stop asking about this form

    def reset(self) -> None:
        self.quiet = False

    def __call__(self, question: str, options: list[str] | None = None, many: bool = False) -> Any:
        if self.quiet:
            return None
        print(f"\n  This application asks: {question}")
        how = "your answer"
        if options:
            for number, option in enumerate(options, start=1):
                print(f"    {number}. {option}")
            how = "the numbers, with commas between them" if many else "the number"
        print(f"  Type {how} and press Enter. Just Enter leaves it for you to fill in in the browser.")
        reply = prompts.ask("  Your answer: ", self.timeout)
        if reply is None:
            print("  No answer, so the rest of this form is left for you.")
            self.quiet = True
            return None
        reply = reply.strip()
        if not reply or not options:
            return reply or None
        picked = []
        for part in ([p.strip() for p in reply.split(",")] if many else [reply]):
            if part.isdigit() and 1 <= int(part) <= len(options):
                picked.append(options[int(part) - 1])
            elif pick_option(options, part) is not None:
                picked.append(options[pick_option(options, part)])  # type: ignore[index]
        if not picked:
            print("  That isn't one of the choices, so it's left for you.")
            return None
        return picked if many else picked[0]


# ------------------------------------------------------------------------ runs


def load_answers() -> tuple[list[dict[str, Any]], Path]:
    path = tailor.PROFILE_PATH
    if not path.exists():
        print(f"[note] No career profile at {path}, so there are no saved answers; every question will be asked.")
        return [], path
    profile = tailor.load_profile(path)
    saved = profile.get("application_answers", [])
    # Saved answers come after the profile's own facts so they win when both fit.
    return profile_answers(profile) + (saved if isinstance(saved, list) else []), path


def mark_applied(posting: dict[str, Any], link: str) -> None:
    """The same as pressing Applied on a ranked page."""
    applied_myself.mark(posting["id"], {"title": posting["title"], "employer": posting["employer"],
                                        "url": link or posting["url"], "page": "employer site"})
    listing = manual_list()
    if listing.get(posting["id"]) is not None and not listing.is_removed(posting["id"]):
        listing.dismiss(posting["id"])
        listing.save()


def note_link_problem(posting: dict[str, Any], why: str, seen: str) -> None:
    """Keep what the posting's Apply area looked like, to work out a Handshake layout change."""
    path = DATA_DIR / "employer_link_problems.txt"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now().isoformat(timespec='seconds')}  {posting['id']}  {why}\n{seen}\n\n")
    except OSError:
        return
    print(f"     what the page showed is saved in {path}")


def ask_settings(args: argparse.Namespace) -> None:
    """The double-click version: three questions instead of command line flags."""
    def choice(prompt: str, allowed: str, default: str) -> str:
        reply = (prompts.ask(prompt, None) or "").strip().lower()[:1]
        return reply if reply and reply in allowed else default

    print("Which list?\n  1. All internships, ranked (from Handshake)\n  2. Found elsewhere (company career sites)\n  3. Both")
    args.list = {"1": "ranked", "2": "elsewhere", "3": "both"}[choice("Type 1, 2 or 3 [1]: ", "123", "1")]
    print("\nWhat should it do?\n  1. Practice run: fill each form in, send nothing\n"
          "  2. Apply, showing me each filled form and asking before it's sent\n"
          "  3. Apply automatically to every form it can complete")
    mode = choice("Type 1, 2 or 3 [2]: ", "123", "2")
    args.dry_run, args.auto_submit = mode == "1", mode == "3"
    reply = (prompts.ask(f"\nHow many applications this run? [{args.max}]: ", None) or "").strip()
    if reply.isdigit() and int(reply) > 0:
        args.max = min(int(reply), 100)
    print()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Apply on employers' own sites, working down the ranked lists.")
    parser.add_argument("--list", choices=["ranked", "elsewhere", "both"], default="ranked",
                        help='"ranked" is the Handshake list from rank_all.py (the default), '
                             '"elsewhere" the list from find_elsewhere.py')
    parser.add_argument("--max", type=int, default=10, help="most forms to fill in this run (default 10)")
    parser.add_argument("--top", type=int, default=100, help="how far down the list to look (default 100)")
    parser.add_argument("--dry-run", action="store_true", help="fill forms in but never send one")
    parser.add_argument("--auto-submit", action="store_true", help="send complete forms without asking each time")
    parser.add_argument("--links-only", action="store_true", help="only look up where each posting applies")
    parser.add_argument("--retry", action="store_true", help="include postings settled on earlier runs")
    parser.add_argument("--resume", help="your resume (default: the one the launcher remembers)")
    parser.add_argument("--tailor-resume", action="store_true", help="make a resume for each job from your profile")
    parser.add_argument("--cover-letters", action="store_true",
                        help="write a cover letter wherever a form takes one, not only where it's required")
    parser.add_argument("--no-cover-letters", action="store_true", help="never write a cover letter")
    parser.add_argument("--ask-me", action="store_true",
                        help="ask me every question my saved answers don't cover, instead of working "
                             "the answer out from my profile with Claude")
    parser.add_argument("--no-essays", action="store_true",
                        help="don't write answers to open questions; ask you, or leave them for you")
    parser.add_argument("--send-essays", action="store_true",
                        help="with --auto-submit, also send forms that hold a written answer you haven't read")
    parser.add_argument("--no-ai", action="store_true",
                        help="tailor with rules only, and write no letters or answers with Claude")
    parser.add_argument("--answer-timeout", type=float, default=None,
                        help="seconds to wait for your answer to a question (default 60; 0 waits for ever)")
    parser.add_argument("--base-url", help="your school's Handshake address, if it has its own")
    parser.add_argument("--headless", action="store_true", help="hide the browser window")
    parser.add_argument("--ask", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    interactive = sys.stdin is not None and sys.stdin.isatty()
    if args.ask and interactive:
        ask_settings(args)
    config = load_config(args)
    dry_run, auto = bool(args.dry_run), bool(args.auto_submit) and not args.dry_run
    if not dry_run and not auto and not args.links_only and not interactive:
        raise SystemExit("Asking before each application needs a window you can type in. "
                         "Use --dry-run to practice or --auto-submit to send without asking.")

    record = Record(DATA_DIR / "employer_applications.json")
    links = Links(DATA_DIR / "employer_links.json")
    answers, profile_path = load_answers()
    done = applied_myself.ids()
    listing = manual_list()
    postings = []
    for posting in chosen_postings(args.list)[: max(args.top, 0)]:
        status = record.status(posting["id"])
        if posting["id"] in done or status == "applied" or listing.is_removed(posting["id"]):
            continue
        if status in SETTLED and not args.retry:
            continue
        # A form left for want of an answer is only worth opening again once there are new answers.
        if (status == "needs_manual" and auto and not args.retry
                and record.entries[posting["id"]].get("answers") == len(answers)
                and str(record.entries[posting["id"]].get("note", "")).startswith("still empty")):
            continue
        postings.append(posting)
    if not postings:
        print("Nothing left to try on that list. Run rank_all.py or find_elsewhere.py for fresh postings, "
              "or add --retry to go back over earlier ones.")
        return 0

    papers = Papers(config, args)
    if papers.resume is None and not args.links_only:
        raise SystemExit("No resume to attach. Pass --resume PATH, or pick one in the launcher once.")
    ask = Questions(float(config.get("answer_timeout", 60))) if interactive and config.get("answer_questions", True) else None
    person_present = interactive and not config.get("headless")

    banner("Applying on employers' own sites")
    if args.links_only:
        print("  mode     : only looking up where each posting applies")
    else:
        print("  mode     : " + ("practice run, nothing is sent" if dry_run
                                else "automatic, complete forms are sent without asking" if auto
                                else "you check each filled form before it's sent"))
        print(f"  resume   : {papers.resume}")
        print(f"  this run : up to {args.max} " + ("applications sent" if auto else "forms")
              + f", from {len(postings)} postings on the list")
        print(f"  answers  : {len(answers)} from your profile and saved answers; "
              + ("the rest are worked out from your profile with Claude, then " if papers.working_out else "the rest are ")
              + ("asked here and remembered" if ask else "left for you"))
        print("  essays   : " + ("open questions are answered from your profile with Claude, fact checked"
                                + ("" if not auto or args.send_essays else "; those forms wait for you to read")
                                if papers.essays else "not written; asked, or left for you"))
        print("  never    : passwords, Social Security numbers, bank details, or a security check")
    if auto:
        print("\nAutomatic mode sends applications in your name without further prompts.")
        if input("Type 'yes' to continue: ").strip().lower() != "yes":
            print("Cancelled.")
            return 1

    low, high = (list(config.get("delay_between_employer_applications_seconds", [8, 20])) + [20])[:2]
    counts: dict[str, int] = {}
    kinds: dict[str, int] = {}
    handled = 0
    signed_in: bool | None = None

    try:
        with HandshakeSession(config, load_selectors(), DATA_DIR / "browser_profile") as session:
            site = EmployerSite(session.new_tab(), answers, ask, person_present,
                                float(config.get("security_check_wait", 300)))
            for number, posting in enumerate(postings, start=1):
                if handled >= args.max and not args.links_only:
                    print(f"\nThat's {args.max}, the most for this run.")
                    break
                print(f"\n[{number}/{len(postings)}] {posting['title'][:70]} @ {posting['employer']}"
                      f"  (fit {round(posting['score'])})")

                link = posting["url"] if posting["source"] == "elsewhere" else links.get(posting["id"])
                if not link:
                    if signed_in is None:
                        signed_in = session.ensure_logged_in()
                    if not signed_in:
                        print("  Not signed in to Handshake, so its postings can't be looked up this run.")
                        break
                    try:
                        link, why = session.external_apply_url(posting["id"])
                    except Exception as exc:
                        if session.page is None or session.page.is_closed() or "closed" in str(exc).lower():
                            print(CLOSED_MESSAGE)
                            break
                        link, why = "", f"couldn't read the posting ({type(exc).__name__})"
                    if link:
                        links.keep(posting["id"], link)
                        record.forget(posting["id"], "no_link")  # an earlier run couldn't find it
                    else:
                        status = "closed" if why == "closed" else "no_link"
                        record.note(posting, status, why)
                        counts[status] = counts.get(status, 0) + 1
                        print(f"  -> {SAYS[status]}: {why}")
                        if status == "no_link":
                            note_link_problem(posting, why, session.apply_area_summary())
                        continue
                    time.sleep(random.uniform(1.0, 2.5))
                kind, site_name = site_kind(link)
                group = site_name if kind != "other" else "other sites"
                kinds[group] = kinds.get(group, 0) + 1
                print(f"  {link}")
                if args.links_only:
                    print(f"  -> {site_name}" + ("  (needs your own account)" if kind == "account" else ""))
                    continue

                state, note = site.open(link)
                if state == "stopped":  # the employer tab was closed; a fresh one, if the browser is still there
                    try:
                        site.home = site.page = session.new_tab()
                        state, note = site.open(link)
                    except Exception:
                        state = "stopped"
                if state == "stopped":
                    print(CLOSED_MESSAGE)
                    break
                if state != "form":
                    record.note(posting, state, note, link, site_name)
                    counts[state] = counts.get(state, 0) + 1
                    print(f"  -> {SAYS.get(state, state)}: {note}")
                    continue
                site_name = note
                if ask is not None:
                    ask.reset()
                report = site.fill(papers.for_posting(posting), papers.essays_for(posting), papers.answers_for(posting))
                saved = remember_answers(profile_path, site.learned)
                site.learned = []
                if not site.alive():
                    print(CLOSED_MESSAGE)
                    break
                print(f"  filled in {len(report.filled)}: " + "; ".join(f[:70] for f in report.filled[:8])
                      + (" ..." if len(report.filled) > 8 else ""))
                for question, text in report.written:
                    print(f"  written for \"{question[:80]}\":")
                    for paragraph in text.split("\n\n"):
                        print(textwrap.fill(paragraph, width=96, initial_indent="    | ", subsequent_indent="    | "))
                if report.worked:
                    print("  worked out from your profile:")
                    for question, value, because in report.worked:
                        print(f"    {question[:56]} -> {value[:44]}   ({because[:80]})")
                if saved:
                    print(f"  saved {saved} of your answers for next time")
                if report.missing:
                    print("  still empty: " + "; ".join(report.missing[:8]))

                status, note = "", ""
                if dry_run:
                    status, note = "dry_run", ""
                    handled += 1
                    if person_present:
                        reply = (prompts.ask("  Nothing was sent. Look it over, then press Enter for the next one"
                                             " (q stops): ", None) or "").strip().lower()
                        if reply.startswith("q"):
                            record.note(posting, status, note, link, site_name, report.filled, len(answers))
                            counts[status] = counts.get(status, 0) + 1
                            break
                elif auto:
                    if report.missing:
                        status, note = "needs_manual", "still empty: " + "; ".join(report.missing[:6])
                    elif "resume" not in report.attached:
                        status, note = "needs_manual", "found nowhere on the form to attach your resume"
                    elif site.flavor not in FORM_SITES:
                        status, note = "needs_manual", ("a kind of form this tool hasn't seen before; "
                                                        "run without --auto-submit to check it yourself")
                    elif report.written and not args.send_essays:
                        status, note = "needs_manual", ("holds a written answer for you to read first; run without "
                                                        "--auto-submit to see it, or add --send-essays")
                    else:
                        status, note = site.submit()
                        handled += 1
                else:
                    stop = False
                    handled += 1
                    while not status:
                        print("  The filled form is in the browser window. Change anything you like there first.")
                        reply = (prompts.ask("  Send this application? [y]es / [n]o, never / [l]ater / [q]uit: ", None)
                                 or "q").strip().lower()
                        if reply.startswith("y"):
                            missing = site.missing_required()
                            if missing:
                                print("  These still look empty: " + "; ".join(missing[:8]))
                                again = (prompts.ask("  Send it anyway? [y]es / [n]o, I'll fill them in: ", None)
                                         or "").strip().lower()
                                if not again.startswith("y"):
                                    continue
                            status, note = site.submit()
                        elif reply.startswith("n"):
                            status, note = "declined", "you said no"
                        elif reply.startswith("l"):
                            status, note = "later", ""
                        elif reply.startswith("q"):
                            status, note, stop = "later", "", True
                    if stop:
                        print("  Stopping here.")
                        break

                counts[status] = counts.get(status, 0) + 1
                if status != "later":
                    record.note(posting, status, note, link, site_name, report.filled, len(answers))
                if status == "applied":
                    mark_applied(posting, link)
                print(f"  -> {SAYS.get(status, status)}" + (f": {note}" if note else ""))
                if status in {"applied", "uncertain"} and handled < args.max and number < len(postings):
                    pause = random.uniform(float(low), float(high))
                    print(f"  waiting {pause:.0f}s before the next one")
                    time.sleep(pause)
    finally:
        results = record.write_csv(listing.folder) if record.entries else None

    banner("Where they apply" if args.links_only else "Run summary")
    if args.links_only:
        for name, count in sorted(kinds.items(), key=lambda kv: -kv[1]):
            print(f"  {count:3d}  {name}")
        print("\nGreenhouse, Lever and Ashby forms can be filled in here. The addresses are saved, "
              "so the next run skips this step.")
    for status, count in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {count:3d}  {SAYS.get(status, status)}")
    if results is not None:
        print(f"\nEvery posting tried so far, with its employer link: {results}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. What was done so far is saved; run again to carry on.")
        sys.exit(130)
