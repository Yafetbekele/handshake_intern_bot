"""Make a tailored resume or cover letter for one posting, on its own.

Nothing here searches, applies or touches an application. It takes one posting,
from a Handshake link or from text you paste, and writes the documents into
tailored_resumes/<job>/.

    python make_documents.py            both, asking for the posting
    python make_documents.py letter     cover letter only
    python make_documents.py resume     resume only
    python make_documents.py both --url https://app.joinhandshake.com/job-search/123
    python make_documents.py letter --text posting.txt --title "SWE Intern" --employer "IEX"

The double-click files "Make a cover letter.bat" and "Make a resume.bat" run
this for you.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import cover_letter
import tailor
from main import DATA_DIR, load_config, load_selectors

HERE = Path(__file__).resolve().parent


def read_pasted_posting() -> tuple[str, str, str]:
    """Ask for a title, employer and the posting text pasted into the window."""
    print("\nPaste the posting below.")
    title = input("  Job title    : ").strip()
    employer = input("  Employer     : ").strip()
    print("  Job description: paste it, then press Enter on an empty line twice to finish.")
    lines: list[str] = []
    blanks = 0
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line.strip():
            blanks = 0
            lines.append(line)
        else:
            blanks += 1
            if blanks >= 2 and lines:
                break
            lines.append("")
    return title, employer, "\n".join(lines).strip()


def job_id_from(url: str) -> str:
    match = re.search(r"/(\d{4,})", url)
    return match.group(1) if match else ""


def load_from_handshake(url: str) -> tuple[str, str, str, str]:
    """Open the posting in the saved Handshake session and read it. Applies to nothing."""
    from handshake import HandshakeSession

    job_id = job_id_from(url)
    if not job_id:
        raise SystemExit("That doesn't look like a Handshake posting link.")
    config = load_config(argparse.Namespace())
    print(f"Opening posting {job_id} on Handshake to read it...")
    with HandshakeSession(config, load_selectors(), DATA_DIR / "browser_profile") as session:
        if not session.ensure_logged_in():
            raise SystemExit("Not signed in to Handshake. Run the launcher once and sign in.")
        job = session.load_job(job_id)
    if not job.title:
        raise SystemExit(f"Could not read that posting ({job.error or 'no title found'}).")
    print(f"Read: {job.title} @ {job.employer}")
    return job_id, job.title, job.employer, job.description


def slug_id(title: str, employer: str) -> str:
    """A folder name for a pasted posting; the employer is added to it later."""
    return tailor._slug(title, 40) or "posting"


def make(what: str, job_id: str, title: str, employer: str, text: str, use_ai: bool = True) -> list[Path]:
    made: list[Path] = []
    if what in ("resume", "both"):
        print("\nTailoring your resume for this posting...")
        result = tailor.tailor_resume(job_id, title, employer, text, use_ai=use_ai, reuse=False)
        print(f"  resume ({result.method}): {result.path}")
        for note in result.notes[:3]:
            print(f"    {note}")
        made.append(result.path)
    if what in ("letter", "both"):
        print("\nWriting a cover letter for this posting...")
        letter = cover_letter.cover_letter(job_id, title, employer, text, use_ai=use_ai, reuse=False)
        how = {"ai": "Claude, fact-checked", "baseline": "your baseline letter", "profile": "your profile"}
        print(f"  cover letter ({how.get(letter.method, letter.method)}): {letter.path}")
        for note in letter.notes[:4]:
            print(f"    {note}")
        made.append(letter.path)
    return made


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Make a tailored resume or cover letter for one posting.")
    parser.add_argument("what", nargs="?", default="both", choices=["letter", "resume", "both"])
    parser.add_argument("--url", help="Handshake posting link; the posting is read, never applied to")
    parser.add_argument("--text", help="file with the posting text, instead of a link")
    parser.add_argument("--title", default="", help="job title, with --text")
    parser.add_argument("--employer", default="", help="employer, with --text")
    parser.add_argument("--no-ai", action="store_true", help="use rules and your baseline letter, without Claude")
    parser.add_argument("--open", dest="open_folder", action="store_true", help="open the folder when finished")
    args = parser.parse_args(argv)

    print("=" * 60)
    print(" Make a tailored resume or cover letter")
    print("=" * 60)

    if args.url:
        job_id, title, employer, text = load_from_handshake(args.url)
    elif args.text:
        text = Path(args.text).read_text(encoding="utf-8")
        title, employer = args.title, args.employer
        job_id = slug_id(title, employer)
    else:
        url = input("\nPaste the Handshake link, or press Enter to paste the posting yourself: ").strip()
        if url:
            job_id, title, employer, text = load_from_handshake(url)
        else:
            title, employer, text = read_pasted_posting()
            job_id = slug_id(title, employer)

    if not title or not employer or not text:
        raise SystemExit("A job title, an employer and the posting text are all needed.")

    made = make(args.what, job_id, title, employer, text, use_ai=not args.no_ai)
    if not made:
        return 1
    folder = made[0].parent
    print(f"\nSaved in {folder}")
    print("The .txt copy of a letter is there too, for pasting into a web form.")
    if args.open_folder or not (args.url or args.text):
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except (AttributeError, OSError):
            pass
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(130)
