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
os.environ["HSBOT_KEYWORD_DIR"] = str(WORK / "keywords")


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
print("5b. SHORT PLANS FILL THE PAGE WITH MORE OF THE PROFILE")
print("=" * 70)
short_plan = tailor.rule_plan(profile, EMBEDDED_JOB[1], EMBEDDED_JOB[0])
short_plan.sections = [{"heading": short_plan.sections[0]["heading"],
                        "entries": [dict(short_plan.sections[0]["entries"][0],
                                         bullets=short_plan.sections[0]["entries"][0]["bullets"][:1])]}]
short_plan.skills = short_plan.skills[:2]
short_plan.coursework = short_plan.coursework[:1]


def text_bottom(pdf_path: Path) -> float:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[0]
        return max(w["bottom"] for w in page.extract_words()) / page.height


bare = tailor.render_pdf(profile, short_plan, WORK / "bare.pdf", fill=False)
filled = tailor.render_pdf(profile, short_plan, WORK / "filled.pdf", job_text=EMBEDDED_JOB[1], title=EMBEDDED_JOB[0])
pages, filled_text = pdf_text(filled)
print(f"  without filling the text ends at {text_bottom(bare):.0%}, filled at {text_bottom(filled):.0%}")
check("filling uses more of the page", text_bottom(filled) > text_bottom(bare) + 0.1)
check("still one page", pages == 1, f"pages={pages}")
flat_text = " ".join(filled_text.split())
all_bullets = [b["text"] for e in tailor.profile_entries(profile).values() for b in e["bullets"]]
check("with room to spare, every bullet in the profile is used",
      all(b[:40] in flat_text for b in all_bullets), str([b[:30] for b in all_bullets if b[:40] not in flat_text]))
check("and every skill", all(s["name"] in flat_text for s in profile["skills"]))
check("skills come before the experience sections",
      filled_text.index("SKILLS") < filled_text.index(short_plan.sections[0]["heading"]))

big_filled = tailor.render_pdf(big, short_plan, WORK / "big_filled.pdf", job_text=EMBEDDED_JOB[1], title=EMBEDDED_JOB[0])
print(f"  a long profile from the same short plan ends at {text_bottom(big_filled):.0%}")
check("a long profile fills the page", text_bottom(big_filled) > 0.85, f"{text_bottom(big_filled):.0%}")
check("without spilling onto a second page", pdf_text(big_filled)[0] == 1)


def skill_rows(pdf_path: Path) -> list[str]:
    lines = pdf_text(pdf_path)[1].splitlines()
    start = lines.index("SKILLS") + 1
    rows = []
    for row in lines[start:]:
        if row.isupper():  # the next heading
            break
        rows.append(row)
    return rows


lone = []
names = ["C++", "Verilog", "Oscilloscopes", "Soldering", "MATLAB", "Linux", "Git", "KiCad", "PCB layout", "SPI",
         "I2C", "UART", "Logic analyzers", "Python", "Embedded C", "LTspice", "Multimeters", "Arduino", "FPGA", "VHDL"]
for extra in range(4, len(names) + 1):
    many = copy.deepcopy(profile)
    many["skills"] = list(many["skills"]) + [{"name": f"{n} {extra}", "tags": []} for n in names[:extra]]
    pdf = tailor.render_pdf(many, short_plan, WORK / f"skills_{extra}.pdf", job_text=EMBEDDED_JOB[1], title=EMBEDDED_JOB[0])
    rows = skill_rows(pdf)
    if len(rows) > 1 and "," not in rows[-1]:
        lone.append((extra, rows[-1]))
check("no skill is left alone on the last line of the skills", not lone, str(lone))

old_folder = WORK / "tailored" / "5003-old-co"
old_folder.mkdir(parents=True)
(old_folder / "Jane_Doe_Resume.pdf").write_bytes(bare.read_bytes())
(old_folder / "plan.json").write_text(json.dumps(tailor.asdict(short_plan)), encoding="utf-8")  # no layout version
redrawn = tailor.tailor_resume("5003", EMBEDDED_JOB[0], "Old Co", EMBEDDED_JOB[1], use_ai=False)
check("a resume from the old layout is redrawn", "redrew" in " ".join(redrawn.notes), str(redrawn.notes))
again = tailor.tailor_resume("5003", EMBEDDED_JOB[0], "Old Co", EMBEDDED_JOB[1], use_ai=False)
check("and then reused as it is", "reused" in " ".join(again.notes), str(again.notes))
real_ai_plan, asked = tailor.ai_plan, []
tailor.ai_plan = lambda *a, **k: (asked.append(1), (None, "Claude is away"))[1]
try:
    remade = tailor.tailor_resume("5003", EMBEDDED_JOB[0], "Old Co", EMBEDDED_JOB[1], use_ai=True)
finally:
    tailor.ai_plan = real_ai_plan
check("one the rules made while Claude was away is made again once Claude can be used",
      asked and "reused" not in " ".join(remade.notes), str(remade.notes))
check("courses are shown by title, with the ones still being taken starred",
      tailor.coursework_line(["CMPE 310 (Systems Design and Programming, in progress)", "CMPE 212 (Digital Design)"])
      == "Systems Design and Programming*, Digital Design (*in progress)")
check("a bullet that repeats one already there is recognised",
      tailor._same_point("Built an electric skateboard around an Arduino Uno, a BLDC motor, and an ESC, sourcing all of the parts.",
                         "Designed and built an electric skateboard, integrating purchased motors and battery packs with custom parts."))
check("the redrawn resume is filled", text_bottom(redrawn.path) >= text_bottom(filled) - 0.01,
      f"{text_bottom(redrawn.path):.0%}")

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

# A reworded bullet isn't shown next to the one it came from.
check("a bullet sharing a run of words with another is the same point", tailor._same_point(
    "Manage each repair from customer intake to completion; expanding into computer repairs.",
    "Grew the business to replace a restaurant job's income; expanding into computer repairs."))
check("so is one that only changes 'apply' to 'applying'", tailor._same_point(
    "Troubleshoot issues while applying hardware security to the CAN protocol.",
    "Apply hardware security concepts to the CAN protocol used in embedded systems."))
check("two different points about one project aren't", not tailor._same_point(
    "Build and simulate CAN bus attacks using Verilog and C++ on FPGA hardware.",
    "Develop attack logic in Verilog and run simulations to evaluate its behavior on CAN networks."))

print()
print("=" * 70)
print("7. JOB KEYWORDS, FOR RESUMES THE STUDENT SENDS HIMSELF")
print("=" * 70)
KEYWORD_JOB = ("Embedded Verification Intern",
               "Raspberry Pi sensors, UART and I2C drivers, SystemVerilog and UVM testbenches for PCIe, "
               "Python scripting, Linux.")
found = tailor.job_keywords(KEYWORD_JOB[1], KEYWORD_JOB[0])
check("finds the posting's keywords", {"SystemVerilog", "UVM", "PCIe", "I2C", "UART"} <= set(found), str(found))

with_kw = tailor.tailor_resume("7001", KEYWORD_JOB[0], "Chip Co", KEYWORD_JOB[1], use_ai=False, add_keywords=True)
plain = tailor.tailor_resume("7001", KEYWORD_JOB[0], "Chip Co", KEYWORD_JOB[1], use_ai=False)
kw_plan = json.loads((with_kw.path.parent / "plan.json").read_text(encoding="utf-8"))
plain_plan = json.loads((plain.path.parent / "plan.json").read_text(encoding="utf-8"))
check("keywords go on the skills line", "SystemVerilog" in kw_plan["skills"] and "UVM" in kw_plan["skills"], str(kw_plan["skills"]))
check("and are listed to check", "SystemVerilog" in with_kw.added, str(with_kw.added))
check("a skill the profile has isn't listed to check", "Python" not in with_kw.added)
check_text = (with_kw.path.parent / tailor.CHECK_FILE).read_text(encoding="utf-8")
check("CHECK BEFORE SENDING.txt names them", "SystemVerilog" in check_text and "check each one" in check_text.lower())
check("still one page", pdf_text(with_kw.path)[0] == 1)
check("kept apart from the resumes the appliers use",
      with_kw.path.parent.parent == tailor.KEYWORD_DIR and tailor.OUT_DIR not in with_kw.path.parents)
check("the usual resume has none of them", "SystemVerilog" not in plain_plan["skills"] and not plain.added)
check("and no check file", not (plain.path.parent / tailor.CHECK_FILE).exists())

fallback = tailor.rule_plan(profile, KEYWORD_JOB[1], KEYWORD_JOB[0])
plan, reason = tailor.ai_plan(profile, KEYWORD_JOB[1], KEYWORD_JOB[0], "Chip Co", fallback, keywords=True)
check("keyword plan returned", plan is not None, reason)
if plan is not None:
    bullets = {e["id"]: e["bullets"] for s in plan.sections for e in s["entries"]}
    station = " ".join(bullets.get("weather-station", []))
    desk = " ".join(bullets.get("campus-help-desk", []))
    for note in plan.notes:
        print(f"  note: {note}")
    check("a posting term worked into a true bullet is kept", "I2C" in station, station)
    check("and listed to check", "I2C" in plan.added_keywords and "Linux" in plan.added_keywords, str(plan.added_keywords))
    check("its digit isn't taken for an invented number", "every 5 minutes" in station)
    check("a duty with nothing to do with the entry is stopped", "PCIe controllers" not in station)
    check("an invented number is still stopped", "500" not in station)
    check("an award is still stopped", "award" not in desk.lower())
    check("suggested skills from the posting go in", "SystemVerilog" in plan.skills and "UVM" in plan.skills, str(plan.skills))
    check("a suggested tool the posting never names doesn't", "Kubernetes" not in plan.skills)

import main  # noqa: E402
from handshake import Job  # noqa: E402

maker = main.ResumeTailor({"tailor_resume": True, "use_ai_for_tailoring": False, "profile_path": str(SAMPLE)})
job = Job(job_id="7002", url="https://example.com/7002", title=KEYWORD_JOB[0], employer="Chip Co",
          description=KEYWORD_JOB[1])
sent_for_him = maker.for_job(job)
for_his_list = maker.for_job(job, for_list=True)
check("applications sent for him never get added keywords",
      sent_for_him is not None and tailor.KEYWORD_DIR not in sent_for_him.parents)
check("his list's resumes do", for_his_list is not None and tailor.KEYWORD_DIR in for_his_list.parents
      and "SystemVerilog" in maker.last_added, str(maker.last_added))

listing.add("7002", KEYWORD_JOB[0], "Chip Co", "Remote", "https://example.com/7002", 0.5, "Needs your own account",
            resume_path=str(for_his_list), checks=maker.last_added)
html = listing.save().read_text(encoding="utf-8")
check("the list page shows what to check", "check: " in html and "SystemVerilog" in html)
listing.add("7002", KEYWORD_JOB[0], "Chip Co", "Remote", "https://example.com/7002", 0.5, "Needs your own account")
check("a later run that doesn't remake it keeps the checks", "SystemVerilog" in listing.get("7002")["resume_check"])

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL TAILORING TESTS PASSED")
print("=" * 70)
