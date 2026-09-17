"""End-to-end run of the `search` and `apply --dry-run` commands against the mock.

Never touches the real Handshake. Run from the project root:
    python tests/cli_test.py
"""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Keep the student's real "apply yourself" list, profile and resumes untouched,
# and use a fake Claude so no plan usage is spent.
MANUAL_DIR = Path(tempfile.mkdtemp(prefix="hsbot_manual_"))
os.environ["HSBOT_MANUAL_DIR"] = str(MANUAL_DIR)
TAILORED_DIR = MANUAL_DIR.parent / (MANUAL_DIR.name + "_tailored")
os.environ["HSBOT_TAILORED_DIR"] = str(TAILORED_DIR)
os.environ["HSBOT_PROFILE"] = str(Path(__file__).with_name("sample_profile.json"))
_fake = Path(__file__).with_name("fake_claude.py")
if os.name == "nt":
    _launcher = MANUAL_DIR.parent / (MANUAL_DIR.name + "_claude.cmd")
    _launcher.write_text(f'@"{sys.executable}" "{_fake}" %*\r\n', encoding="utf-8")
else:
    _launcher = MANUAL_DIR.parent / (MANUAL_DIR.name + "_claude")
    _launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_fake}" "$@"\n', encoding="utf-8")
    _launcher.chmod(0o755)
os.environ["HSBOT_CLAUDE"] = str(_launcher)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main as cli
from fake_handshake import serve

PORT = 8766
BASE = f"http://127.0.0.1:{PORT}"
RESUME = str(Path(__file__).with_name("sample_resume.txt"))
DATA = ROOT / "data"

# Keep any real ledger out of harm's way while the test runs.
BACKUP = ROOT / "data_backup_during_test"
if DATA.exists():
    if BACKUP.exists():
        shutil.rmtree(BACKUP)
    DATA.rename(BACKUP)

server, _thread = serve(PORT)
print(f"Mock Handshake serving at {BASE}\n")

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


try:
    print("=" * 70)
    print("COMMAND: search")
    print("=" * 70)
    code = cli.main(
        [
            "search",
            "--resume", RESUME,
            "--major", "Computer Science",
            "--base-url", BASE,
            "--pages", "2",
            "--scan", "10",
            "--headless",
            "--report", "test_matches.csv",
        ]
    )

    print()
    print("=" * 70)
    print("ASSERTIONS")
    print("=" * 70)
    check("search exited cleanly", code == 0, f"exit={code}")

    report = DATA / "test_matches.csv"
    check("report written", report.exists())
    if report.exists():
        rows = list(csv.DictReader(report.open(encoding="utf-8")))
        titles = [r["title"] for r in rows]
        print(f"  ranked: {titles}")
        check("the SWE internship was kept", any("Software Engineering Intern" in t for t in titles))
        check("the veterinary posting was dropped", not any("Veterinary" in t for t in titles))
        check("the full-time role was dropped", not any("Senior Software Engineer" in t for t in titles))
        check("the posting needing a written answer is listed", any("Backend Engineering Intern" in t for t in titles))
        check("the transcript posting is listed", any("Cloud Software Intern" in t for t in titles))
        check("the spring internship was dropped", not any("Platform Engineering Intern" in t for t in titles))
        external_rows = [r for r in rows if "Machine Learning Intern" in r["title"]]
        check(
            "an external match, if listed, is labeled external",
            all(r["apply_kind"] == "external" for r in external_rows),
        )

    print()
    print("=" * 70)
    print("COMMAND: apply --dry-run")
    print("=" * 70)
    code = cli.main(
        [
            "apply",
            "--resume", RESUME,
            "--major", "Computer Science",
            "--base-url", BASE,
            "--pages", "2",
            "--scan", "10",
            "--headless",
            "--dry-run",
            "--tailor-resume",
        ]
    )
    check("dry-run apply exited cleanly", code == 0, f"exit={code}")

    ledger_file = DATA / "applied.json"
    check("ledger written", ledger_file.exists())
    if ledger_file.exists():
        import json

        records = json.loads(ledger_file.read_text(encoding="utf-8"))
        statuses = sorted({r["status"] for r in records.values()})
        print(f"  ledger statuses: {statuses}")
        check("dry run recorded, nothing marked applied", "applied" not in statuses, str(statuses))
        check("a dry_run entry exists", "dry_run" in statuses, str(statuses))
        check("incomplete form marked needs_manual", "needs_manual" in statuses, str(statuses))

    followup_file = DATA / "follow_up.csv"
    check("follow-up list written", followup_file.exists())
    if followup_file.exists():
        followups = list(csv.DictReader(followup_file.open(encoding="utf-8")))
        for row in followups:
            print(f"  follow-up: {row['title']}  [{row['reason']}]")
        check(
            "posting with an unanswered question is in the follow-up list",
            any("Backend Engineering Intern" in r["title"] for r in followups),
        )
        check(
            "dry-run successes are not in the follow-up list",
            not any("Software Engineering Intern" in r["title"] for r in followups),
        )

    print()
    print("=" * 70)
    print("APPLY-YOURSELF LIST")
    print("=" * 70)
    saved = json.loads((MANUAL_DIR / "list.json").read_text(encoding="utf-8")) if (MANUAL_DIR / "list.json").exists() else {}
    for item in saved.values():
        print(f"  {item['title']}  [{item['reason']}]  {item['url']}")
    page = MANUAL_DIR / "apply_yourself.html"
    check("list page written", page.exists())
    check("list spreadsheet written", (MANUAL_DIR / "apply_yourself.csv").exists())
    check(
        "external match kept with its link",
        any(i["job_id"] == "1002" and i["url"].endswith("/job-search/1002") for i in saved.values()),
    )
    check(
        "form needing an answer kept with the reason",
        any(i["job_id"] == "1005" and "Why do you want to intern" in i["reason"] for i in saved.values()),
    )
    check("postings the tool can handle stay off the list", "1001" not in saved and "1006" not in saved)
    if page.exists():
        text = page.read_text(encoding="utf-8")
        check("page links to postings", "href='http://127.0.0.1:8766/job-search/1002'" in text)
        check("page links each posting's tailored resume", text.count(">Open</a>") >= 2, str(text.count(">Open</a>")))

    print()
    print("=" * 70)
    print("TAILORED RESUMES")
    print("=" * 70)
    made = sorted(p.parent.name for p in TAILORED_DIR.glob("*/Jane_Doe_Resume.pdf"))
    print(f"  made: {made}")
    check("resume tailored for the job it practiced applying to", any(m.startswith("1001-") for m in made))
    check("resume tailored for an employer-site match", any(m.startswith("1002-") for m in made))
    check("resume tailored for the form needing an answer", any(m.startswith("1005-") for m in made))
    check("no resume for skipped postings", not any(m.startswith(("1003-", "1004-", "1007-")) for m in made))
    if made:
        plan = json.loads((TAILORED_DIR / made[0] / "plan.json").read_text(encoding="utf-8"))
        check("tailored through the (fake) Claude", plan.get("method") == "ai", plan.get("method"))
finally:
    shutil.rmtree(MANUAL_DIR, ignore_errors=True)
    shutil.rmtree(TAILORED_DIR, ignore_errors=True)
    for leftover in MANUAL_DIR.parent.glob(MANUAL_DIR.name + "_claude*"):
        leftover.unlink(missing_ok=True)
    server.shutdown()
    if DATA.exists():
        shutil.rmtree(DATA, ignore_errors=True)
    if BACKUP.exists():
        BACKUP.rename(DATA)

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL CLI TESTS PASSED")
print("=" * 70)
