"""End-to-end run of the `search` and `apply --dry-run` commands against the mock.

Never touches the real Handshake. Run from the project root:
    python tests/cli_test.py
"""

from __future__ import annotations

import csv
import shutil
import sys
from pathlib import Path

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
        check("the external posting was dropped", not any("Machine Learning Intern" in t for t in titles))

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
finally:
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
