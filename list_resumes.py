"""Make a tailored resume for every posting on the apply-yourself list, and link each one there.

    python list_resumes.py                 the posting's keywords added, best fit first
    python list_resumes.py --no-keywords   only what your profile backs
    python list_resumes.py --no-ai         rules only, without Claude (fast)
    python list_resumes.py --top 20        just the best 20

The posting's keywords are added even where your profile doesn't back them,
because you send these yourself and check them first. Each resume's folder has
a "CHECK BEFORE SENDING.txt" listing them, and the list page shows them beside
the resume link. Resumes for applications the program sends never get these.

Safe to run again: a resume already made the same way is kept, and one the
rules made while Claude couldn't be reached is made again with Claude.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import posting_cache
import tailor
from main import manual_list

# Parts of the program that use Claude or change the list; this waits for them to finish.
OTHER_RUNS = ("main.py", "employer_apply.py", "overnight.py", "find_elsewhere.py", "letters_for_list.py")


def other_runs() -> list[str]:
    """Other parts of the program running now (Windows), so two don't share Claude or the list."""
    if os.name != "nt":
        return []
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name like 'python%'\" | "
             "ForEach-Object { \"$($_.ProcessId) $($_.CommandLine)\" }"],
            capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    found = []
    for line in out.splitlines():
        pid, _, command = line.strip().partition(" ")
        if pid == str(os.getpid()):
            continue
        name = next((n for n in OTHER_RUNS if n in command), "")
        if name:
            found.append(name)
    return found


class Log:
    """Prints to the window and keeps a copy in the list folder."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self.handle = path.open("w", encoding="utf-8")
        except OSError:
            self.handle = None

    def __call__(self, text: str = "") -> None:
        print(text, flush=True)
        if self.handle:
            self.handle.write(text + "\n")
            self.handle.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tailored resumes for the apply-yourself list.")
    parser.add_argument("--no-keywords", action="store_true", help="only what your profile backs")
    parser.add_argument("--no-ai", action="store_true", help="rules only, without Claude")
    parser.add_argument("--top", type=int, default=0, help="only the best N postings")
    parser.add_argument("--again", action="store_true", help="make every resume again, even ones made already")
    parser.add_argument("--anyway", action="store_true", help="start even if another part of the program is running")
    args = parser.parse_args(argv)

    busy = [] if args.anyway else other_runs()
    if busy:
        print(f"Another part of the program is running ({', '.join(sorted(set(busy)))}).")
        print("It uses Claude and the list too, so start this once it has finished.")
        return 1

    say = Log(manual_list().folder / "list_resumes_log.txt")
    rows = manual_list().items()  # best fit first
    if args.top:
        rows = rows[: args.top]
    keywords = not args.no_keywords
    use_ai = False
    if not args.no_ai:
        use_ai, why = tailor.claude_ready()
        if not use_ai:
            say(f"Claude can't be used right now ({why}), so the rules make them. Run this again later.")
    say(f"Started {datetime.now():%b %d, %I:%M %p}. Resumes for {len(rows)} postings on your list, "
        f"{'with the posting keywords added' if keywords else 'with only what your profile backs'}, "
        f"{'tailored with Claude' if use_ai else 'by the rules (no Claude)'}.")
    say("Stop any time with Ctrl+C; finished resumes are kept, and running it again carries on.\n")

    made: dict[str, int] = {}
    started = time.monotonic()
    for n, row in enumerate(rows, start=1):
        job_id = row["job_id"]
        text = (posting_cache.load(job_id) or {}).get("description", "")
        if len(text) < 200:  # no saved posting: the title and employer are all there is to go on
            text = f"{row['title']}\n{row['employer']}\n{row.get('location', '')}\n{text}"
        try:
            result = tailor.tailor_resume(job_id, row["title"], row["employer"], text, use_ai=use_ai,
                                          reuse=not args.again, add_keywords=keywords)
        except Exception as exc:  # one bad posting never stops the rest
            say(f"  [{n}/{len(rows)}] {row['title'][:50]}: couldn't make it ({type(exc).__name__}: {exc})")
            continue
        made[result.method] = made.get(result.method, 0) + 1
        # Read the list again just before saving, so Remove or Applied pressed meanwhile is kept.
        listing = manual_list()
        if listing.get(job_id) is not None and not listing.is_removed(job_id):
            listing.add(job_id, row["title"], row["employer"], row.get("location", ""), row.get("url", ""),
                        float(row.get("score", 0)), row.get("reason", ""), resume_path=str(result.path),
                        fit=row.get("fit"), why=row.get("why"), checks=result.added)
            listing.save()
        left = (len(rows) - n) * (time.monotonic() - started) / n / 60
        check = f"  check: {', '.join(result.added)}" if result.added else ""
        say(f"  [{n}/{len(rows)}] {row['employer'][:24]:24s} {row['title'][:42]:42s} {result.method:5s}"
            f"  ~{left:.0f} min left{check}")

    say(f"\nDone {datetime.now():%I:%M %p}: {', '.join(f'{v} {k}' for k, v in made.items()) or 'nothing made'}.")
    if use_ai and (made.get("rules") or made.get("ai-earlier")):
        say("Some were made without Claude because it couldn't be reached (its usage limit, most likely). "
            "Run this again after it resets to tailor them; the finished ones are kept.")
    say(f"Open the list to see them: {manual_list().html_path}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nStopped. Run it again to carry on; finished resumes are kept.")
        sys.exit(130)
