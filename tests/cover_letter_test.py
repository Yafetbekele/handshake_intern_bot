"""Checks for cover letters: made-up facts are cut, and the letter fits one page.

Uses the made-up student in tests/sample_profile.json and a fake Claude, so it
never reads the real profile or spends Claude usage. Run from the project root:
    python tests/cover_letter_test.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

WORK = Path(tempfile.mkdtemp(prefix="hsbot_letters_"))
_fake = HERE / "fake_claude.py"
if os.name == "nt":
    FAKE = WORK / "claude.cmd"
    FAKE.write_text(f'@"{sys.executable}" "{_fake}" %*\r\n', encoding="utf-8")
else:
    FAKE = WORK / "claude"
    FAKE.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_fake}" "$@"\n', encoding="utf-8")
    FAKE.chmod(0o755)
os.environ["HSBOT_CLAUDE"] = str(FAKE)

import cover_letter  # noqa: E402

PROFILE_PATH = HERE / "sample_profile.json"
PROFILE = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
JOB = "Backend internship. Build API services in Python and Kubernetes, write tests, join code review."
failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


try:
    print("=" * 70)
    print("1. MADE-UP FACTS ARE CUT")
    print("=" * 70)
    kept, notes = cover_letter.check_letter(
        [
            "I built a Raspberry Pi weather station with Python.",
            "I won first place at a hackathon.",
            "I have 5 years of Kubernetes experience.",
            "Your team works with Kubernetes and code review.",
        ],
        PROFILE, "", "Backend Intern", "Delta Systems", JOB,
    )
    text = " ".join(kept)
    print(f"  kept: {kept}")
    check("a true sentence stays", "Raspberry Pi weather station" in text)
    check("an invented award is cut", "first place" not in text)
    check("a skill only the job mentions can't be claimed", "5 years" not in text and "I have" not in text)
    check("the job's own words may describe the job", "Your team works with Kubernetes" in text)
    check("every cut is explained", len(notes) == 2, str(notes))

    solo = dict(PROFILE, sections=[{"heading": "PROJECTS", "entries": [
        {"id": "electric-skateboard", "organization": "Electric Skateboard", "facts": ["Built alone"],
         "never_claim": ["team", "sold"]},
    ]}])
    kept, _ = cover_letter.check_letter(
        ["I built my electric skateboard with a team.", "I would be glad to join your team at Acme."],
        solo, "", "Intern", "Acme", "",
    )
    check("an experience's own never-claim list is honored", not any("skateboard" in k for k in kept), str(kept))
    check("that list doesn't block ordinary sentences", any("join your team" in k for k in kept), str(kept))

    kept, _ = cover_letter.check_letter(
        ["I'd like to help build the Pod platform.", "I have used Pod in class.",
         "My advisor is Dr. Jane Smith. She runs the lab."],
        PROFILE, "Dr. Jane Smith", "Intern", "Acme", "Acme builds the Pod platform.",
    )
    check("the job's products may be named", any("build the Pod platform" in k for k in kept), str(kept))
    check("but not claimed as experience", not any("used Pod" in k for k in kept), str(kept))
    check("'Dr.' doesn't end a sentence", any("Dr. Jane Smith" in k for k in kept), str(kept))

    check("numbers with commas match the profile",
          cover_letter.check_letter(["I sold 12,000 dollars of repairs."], PROFILE, "about 12000 in sales", "t", "e", "")[0] != [])

    print("=" * 70)
    print("2. THE BASELINE LETTER")
    print("=" * 70)
    base = (
        "Dear Hiring Manager,\n\n"
        "I'm applying for the {role} role at {company}. I'm a junior in computer science.\n\n"
        "I built a Raspberry Pi weather station and help students at the campus help desk.\n\n"
        "Sincerely,\nJane Doe"
    )
    body = cover_letter.base_paragraphs(base)
    check("greeting and sign-off removed", len(body) == 2 and "Dear" not in body[0] and "Sincerely" not in " ".join(body),
          str(body))
    filled = cover_letter.fill_placeholders(body[0], "Backend Intern", "Delta Systems")
    check("company and role filled in", "Backend Intern role at Delta Systems" in filled, filled)

    paragraphs, method, notes = cover_letter.write_letter(PROFILE, "Backend Intern", "Delta Systems", JOB,
                                                          base=base, use_ai=False)
    check("without Claude the baseline is used", method == "baseline" and "Delta Systems" in paragraphs[0], method)
    paragraphs, method, notes = cover_letter.write_letter(PROFILE, "Backend Intern", "Delta Systems", JOB,
                                                          base="Dear team,\n\nI loved working at Acme.", use_ai=False)
    check("a baseline naming another company isn't sent as is", method == "profile", method)
    check("the fallback letter names this job", "Backend Intern" in paragraphs[0] and "Delta Systems" in paragraphs[0])
    check("the fallback letter uses the profile's own facts",
          "weather" in " ".join(paragraphs).lower() or "help desk" in " ".join(paragraphs).lower(), " ".join(paragraphs))

    print("=" * 70)
    print("3. WITH (FAKE) CLAUDE")
    print("=" * 70)
    result = cover_letter.cover_letter(
        "1005", "Backend Intern", "Delta Systems", JOB,
        profile_path=PROFILE_PATH, base_path=WORK / "no_base.txt", out_dir=WORK / "out",
    )
    letter_text = (result.path.parent / "cover_letter.txt").read_text(encoding="utf-8")
    print(f"  method: {result.method}")
    for note in result.notes:
        print(f"    {note}")
    check("written by Claude", result.method == "ai", result.method)
    check("the invented award was cut", "first place" not in letter_text)
    check("the claimed Kubernetes experience was cut", "Kubernetes experience" not in letter_text)
    check("the true parts stayed", "weather station" in letter_text and "help desk" in letter_text)
    check("named for the student", result.path.name == "Jane_Doe_Cover_Letter.pdf", result.path.name)

    import pdfplumber

    with pdfplumber.open(result.path) as pdf:
        pages = len(pdf.pages)
        pdf_text = pdf.pages[0].extract_text() or ""
    check("fits on one page", pages == 1, str(pages))
    check("addressed to the employer", "Delta Systems" in pdf_text)
    check("signed with the student's name", pdf_text.strip().endswith("Jane Doe"), pdf_text.strip()[-40:])

    again = cover_letter.cover_letter(
        "1005", "Backend Intern", "Delta Systems", JOB,
        profile_path=PROFILE_PATH, base_path=WORK / "no_base.txt", out_dir=WORK / "out",
    )
    check("the same job reuses its letter", again.method == "saved" and again.path == result.path)
    other = cover_letter.cover_letter(
        "2002", "Backend Intern", "Other Corp", JOB,
        profile_path=PROFILE_PATH, base_path=WORK / "no_base.txt", out_dir=WORK / "out",
    )
    check("another employer gets its own letter", other.path != result.path and other.path.parent.name.startswith("2002-"))
finally:
    shutil.rmtree(WORK, ignore_errors=True)

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL COVER LETTER TESTS PASSED")
print("=" * 70)
