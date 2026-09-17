"""Checks for the double-click launcher. Never touches the real Handshake.

Run from the project root:
    python tests/launcher_test.py
"""

from __future__ import annotations

import csv
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Keep the student's real "apply yourself" list untouched.
os.environ["HSBOT_MANUAL_DIR"] = tempfile.mkdtemp(prefix="hsbot_manual_")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import launcher
from launcher import LaunchSettings, build_args, load_settings, save_settings, validate

RESUME = str(Path(__file__).with_name("sample_resume.txt"))
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


print("=" * 70)
print("1. FORM TO COMMAND TRANSLATION")
print("=" * 70)
base = dict(resume=RESUME, major="Computer Science", max_applications=7)

args = build_args(LaunchSettings(mode="search", **base))
print(f"  search : {args}")
check("search mode runs search", args[0] == "search")
check("search has no apply-only flags", "--max" not in args and "--dry-run" not in args)

args = build_args(LaunchSettings(mode="dry_run", **base))
print(f"  dry_run: {args}")
check("practice run is apply --dry-run", args[0] == "apply" and "--dry-run" in args)
check("practice run never auto-submits", "--auto-submit" not in args)
check("max passed through", args[args.index("--max") + 1] == "7")

args = build_args(LaunchSettings(mode="confirm", **base))
check("confirm mode has neither flag", "--dry-run" not in args and "--auto-submit" not in args)

args = build_args(LaunchSettings(mode="auto", **base))
check("automatic mode adds --auto-submit", "--auto-submit" in args)
check("tailoring is off unless ticked", "--tailor-resume" not in args)
check(
    "ticking tailoring adds --tailor-resume",
    "--tailor-resume" in build_args(LaunchSettings(mode="confirm", tailor_resume=True, **base)),
)
check("tailoring works for the matches-only mode too",
      "--tailor-resume" in build_args(LaunchSettings(mode="search", tailor_resume=True, **base)))

args = build_args(
    LaunchSettings(
        mode="confirm",
        cover_letter=RESUME,
        transcript=RESUME,
        locations="Chicago,  Texas , ",
        base_url="https://school.joinhandshake.com/",
        **base,
    )
)
print(f"  extras : {args}")
check("cover letter passed", "--cover-letter" in args)
check("transcript passed", "--transcript" in args)
check(
    "locations split and trimmed",
    [args[i + 1] for i, a in enumerate(args) if a == "--location"] == ["Chicago", "Texas"],
)
check("trailing slash removed from school URL", "https://school.joinhandshake.com" in args)
spaced = build_args(LaunchSettings(mode="search", resume=r"C:\My Files\Jane Resume.pdf", major="Biology"))
check("paths with spaces stay one argument", r"C:\My Files\Jane Resume.pdf" in spaced)

print()
print("=" * 70)
print("2. FORM VALIDATION")
print("=" * 70)
check("valid form has no problems", validate(LaunchSettings(mode="search", **base)) == [])
problems = validate(LaunchSettings())
print(f"  empty form: {problems}")
check("empty form asks for resume", any("resume" in p for p in problems))
check("empty form asks for major", any("major" in p for p in problems))
check(
    "missing resume file reported",
    any("can't be found" in p for p in validate(LaunchSettings(resume="C:/nope/resume.pdf", major="x"))),
)
check(
    "wrong resume type reported",
    any("PDF" in p for p in validate(LaunchSettings(resume=str(ROOT / "main.py"), major="x"))),
)
check(
    "out-of-range max reported",
    any("between" in p for p in validate(LaunchSettings(mode="auto", resume=RESUME, major="x", max_applications=0))),
)
check(
    "max ignored for search",
    validate(LaunchSettings(mode="search", resume=RESUME, major="x", max_applications=0)) == [],
)
check(
    "bad school URL reported",
    any("https" in p for p in validate(LaunchSettings(mode="search", base_url="school.edu", **base))),
)

print()
print("=" * 70)
print("3. REMEMBERED SETTINGS")
print("=" * 70)
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / "settings.json"
    check("no file gives defaults", load_settings(path) == LaunchSettings())
    original = LaunchSettings(mode="confirm", locations="Remote", **base)
    save_settings(original, path)
    check("settings round trip", load_settings(path) == original)
    path.write_text("{not json", encoding="utf-8")
    check("corrupt file falls back to defaults", load_settings(path) == LaunchSettings())
    path.write_text('{"mode": "hack", "max_applications": "x", "unknown": 1}', encoding="utf-8")
    loaded = load_settings(path)
    check("bad values are repaired", loaded.mode == "search" and loaded.max_applications == 10)

print()
print("=" * 70)
print("4. FULL LAUNCH AGAINST THE MOCK HANDSHAKE")
print("=" * 70)
from fake_handshake import serve

PORT = 8767
DATA = ROOT / "data"
BACKUP = ROOT / "data_backup_during_launcher_test"
if DATA.exists():
    if BACKUP.exists():
        shutil.rmtree(BACKUP)
    DATA.rename(BACKUP)

server, _thread = serve(PORT)
calls: list[tuple[str, float]] = []
try:
    with tempfile.TemporaryDirectory() as tmp:
        chosen = LaunchSettings(mode="search", base_url=f"http://127.0.0.1:{PORT}", **base)
        code = launcher.run(
            argv=[],
            ask=lambda _initial: chosen,
            after_run=lambda mode, started: calls.append((mode, started)),
            settings_path=Path(tmp) / "settings.json",
            check_setup=False,
            extra_args=("--headless", "--pages", "1"),
        )
        check("launch exited cleanly", code == 0, f"exit={code}")
        check("choices were remembered", load_settings(Path(tmp) / "settings.json") == chosen)
        check("results offered afterwards", calls and calls[0][0] == "search")
        report = DATA / "matches.csv"
        check("ranked matches written", report.exists())
        if report.exists():
            titles = [r["title"] for r in csv.DictReader(report.open(encoding="utf-8"))]
            check("real matches found", any("Software Engineering Intern" in t for t in titles), str(titles))

        cancelled = launcher.run(
            argv=[],
            ask=lambda _initial: None,
            after_run=lambda *_: calls.append(("unexpected", 0.0)),
            settings_path=Path(tmp) / "settings.json",
            check_setup=False,
        )
        check("cancel exits cleanly without running", cancelled == 0 and len(calls) == 1)
finally:
    server.shutdown()
    shutil.rmtree(DATA, ignore_errors=True)
    if BACKUP.exists():
        BACKUP.rename(DATA)

print()
print("=" * 70)
print("5. WINDOW OPENS")
print("=" * 70)
try:
    import tkinter

    tkinter.Tk().destroy()
    display_available = True
except Exception as exc:  # no display, e.g. a Linux CI runner
    display_available = False
    print(f"  skipped, no display available ({type(exc).__name__})")

if display_available:
    with tempfile.TemporaryDirectory() as tmp:
        code = launcher.run(argv=["--self-test"], settings_path=Path(tmp) / "none.json")
        check("form builds and closes", code == 0)

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL LAUNCHER TESTS PASSED")
print("=" * 70)
