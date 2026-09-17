"""Checks for resume tailoring. Uses a made-up student and a fake Claude program,
so it never reads the real profile or spends any Claude usage.

Run from the project root:
    python tests/tailor_test.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
import tempfile
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="hsbot_tailor_"))
SAMPLE = TESTS / "sample_profile.json"
os.environ["HSBOT_PROFILE"] = str(SAMPLE)
os.environ["HSBOT_TAILORED_DIR"] = str(WORK / "tailored")


def fake_claude_command() -> str:
    script = TESTS / "fake_claude.py"
    if os.name == "nt":
        launcher = WORK / "claude.cmd"
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        launcher = WORK / "claude"
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)
    return str(launcher)


os.environ["HSBOT_CLAUDE"] = fake_claude_command()

import pdfplumber  # noqa: E402

import tailor  # noqa: E402
from storage import ManualList  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


def pdf_text(path: Path) -> tuple[int, str]:
    with pdfplumber.open(path) as doc:
        return len(doc.pages), "\n".join(page.extract_text() or "" for page in doc.pages)


profile = tailor.load_profile(SAMPLE)
entries = tailor.profile_entries(profile)
corpus = tailor._profile_corpus(profile)

EMBEDDED_JOB = ("Embedded Systems Intern", "Raspberry Pi sensors, embedded hardware, computer architecture, IoT devices, Python scripting.")
SUPPORT_JOB = ("IT Support Intern", "Help desk support, troubleshooting hardware and software, customer service for staff.")

print("=" * 70)
print("1. RULE-BASED TAILORING")
print("=" * 70)
embedded = tailor.rule_plan(profile, EMBEDDED_JOB[1], EMBEDDED_JOB[0])
support = tailor.rule_plan(profile, SUPPORT_JOB[1], SUPPORT_JOB[0])
print(f"  embedded: sections {[s['heading'] for s in embedded.sections]}, courses {embedded.coursework[:1]}, skills {embedded.skills[:3]}")
print(f"  support : sections {[s['heading'] for s in support.sections]}, courses {support.coursework[:1]}, skills {support.skills[:3]}")
check("projects lead for the embedded job", embedded.sections[0]["heading"] == "PROJECTS")
check("work leads for the support job", support.sections[0]["heading"] == "WORK EXPERIENCE")
check("relevant course first for the embedded job", embedded.coursework[0].startswith("CS 330"))
check("relevant skill first for the support job", support.skills[0] == "Troubleshooting", support.skills[0])
all_rule_bullets = [b for plan in (embedded, support) for s in plan.sections for e in s["entries"] for b in e["bullets"]]
profile_bullets = {b["text"] for e in entries.values() for b in e["bullets"]}
check("rules only ever use the profile's own wording", set(all_rule_bullets) <= profile_bullets)

print()
print("=" * 70)
print("2. FACT CHECKS")
print("=" * 70)
wrongly_flagged = [
    b["text"] for e in entries.values() for b in e["bullets"]
    if tailor.ungrounded_terms(b["text"], e, corpus) or tailor.false_claims(b["text"], e)
    or tailor.unsupported_share(b["text"], e) > tailor.MAX_UNSUPPORTED_SHARE
]
check("every true profile bullet passes", not wrongly_flagged, str(wrongly_flagged))
invented = {
    "Wrote the data logger in Rust on an Arduino.": "weather-station",
    "Logs readings every 30 seconds to PostgreSQL.": "weather-station",
    "Used machine learning to predict storms.": "weather-station",
    "Supervised new student technicians.": "campus-help-desk",
    "Won the campus innovation award for support work.": "campus-help-desk",
}
for text, entry_id in invented.items():
    e = entries[entry_id]
    caught = bool(tailor.ungrounded_terms(text, e, corpus) or tailor.false_claims(text, e)
                  or tailor.unsupported_share(text, e) > tailor.MAX_UNSUPPORTED_SHARE)
    check(f"catches: {text}", caught)

print()
print("=" * 70)
print("3. AI TAILORING THROUGH A FAKE CLAUDE")
print("=" * 70)
ready, why = tailor.claude_ready()
check("sign-in detected", ready, why)
plan, reason = tailor.ai_plan(profile, EMBEDDED_JOB[1], EMBEDDED_JOB[0], "Sensor Co", embedded)
check("plan returned", plan is not None, reason)
if plan is not None:
    bullets = {e["id"]: e["bullets"] for s in plan.sections for e in s["entries"]}
    for note in plan.notes:
        print(f"  note: {note}")
    check("marked as AI", plan.method == "ai")
    check("honest rewording kept", any("logs three sensors" in b for b in bullets.get("weather-station", [])), str(bullets.get("weather-station")))
    check("machine learning claim removed", not any("machine learning" in b.lower() for b in bullets.get("weather-station", [])))
    check("team of 12 claim removed", not any("12" in b for b in bullets.get("campus-help-desk", [])))
    check("invented job removed", "invented-internship" not in bullets)
    check("invented course removed", "Quantum Basket Theory" not in plan.coursework, str(plan.coursework))
    check("invented skill removed", "Kubernetes" not in plan.skills, str(plan.skills))
    check("section order follows the AI", plan.sections[0]["heading"] == "PROJECTS")

print()
print("=" * 70)
print("4. END TO END: PDF, SAVED FILES, REUSE")
print("=" * 70)
result = tailor.tailor_resume("5001", EMBEDDED_JOB[0], "Sensor Co", EMBEDDED_JOB[1])
pages, text = pdf_text(result.path)
print(f"  {result.path} ({result.method})")
check("resume made with the AI plan", result.method == "ai", result.method)
check("one page", pages == 1, f"pages={pages}")
check("file named for the student", result.path.name == "Jane_Doe_Resume.pdf", result.path.name)
check("contact details on the page", "Jane Doe" in text and "jane.doe@example.edu" in text)
check("invented claims not on the page", "machine learning" not in text.lower() and "Kubernetes" not in text)
check("plan saved for review", (result.path.parent / "plan.json").exists())
check("job posting saved alongside", (result.path.parent / "job_posting.txt").exists())
again = tailor.tailor_resume("5001", EMBEDDED_JOB[0], "Sensor Co", EMBEDDED_JOB[1])
check("second request reuses the same resume", again.path == result.path and "reused" in " ".join(again.notes))

rules_only = tailor.tailor_resume("5002", SUPPORT_JOB[0], "Campus IT", SUPPORT_JOB[1], use_ai=False)
check("rules used when AI is off", rules_only.method == "rules")
check("rules resume is one page", pdf_text(rules_only.path)[0] == 1)

print()
print("=" * 70)
print("5. LONG PROFILES STILL FIT ONE PAGE")
print("=" * 70)
big = copy.deepcopy(profile)
for section in big["sections"]:
    for entry in section["entries"]:
        long_bullets = []
        for i in range(6):
            for bullet in entry["bullets"]:
                long_bullets.append(dict(bullet, text=f"{bullet['text']} Variation {i} of the same true fact, written long enough to wrap onto a second line."))
        entry["bullets"] = long_bullets
big_plan = tailor.rule_plan(big, SUPPORT_JOB[1], SUPPORT_JOB[0])
for section in big_plan.sections:
    for entry in section["entries"]:
        entry["bullets"] = [b["text"] for b in next(e for e in tailor.profile_entries(big).values() if e["id"] == entry["id"])["bullets"]][:12]
big_pdf = tailor.render_pdf(big, big_plan, WORK / "big.pdf")
pages, text = pdf_text(big_pdf)
check("trimmed down to one page", pages == 1, f"pages={pages}")
check("the entry marked trim_first went first", "Corner Cafe" not in text)
check("required entries survived", "Home Weather Station" in text and "IT Help Desk" in text)

print()
print("=" * 70)
print("6. APPLY-YOURSELF LIST LINKS THE RESUME")
print("=" * 70)
listing = ManualList(WORK / "Internships to apply to yourself")
listing.add("5001", EMBEDDED_JOB[0], "Sensor Co", "Remote", "https://example.com/job-search/5001", 0.5,
            "Apply on the employer's website", resume_path=str(result.path))
page = listing.save()
html = page.read_text(encoding="utf-8")
check("resume column present", "Tailored resume" in html)
check("relative link to the PDF", "../tailored/5001-sensor-co/Jane_Doe_Resume.pdf" in html)
check("CSV includes the resume path", "Jane_Doe_Resume.pdf" in (listing.csv_path.read_text(encoding="utf-8")))

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL TAILORING TESTS PASSED")
print("=" * 70)
