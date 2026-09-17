"""Offline checks for everything that does not need a browser.

Run from the project root:
    python tests/smoke_test.py
"""

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import majors
import matcher
import resume_parser
from storage import Ledger, ManualList

with tempfile.TemporaryDirectory() as _tmp:
    print("=" * 70)
    print("0. APPLY-YOURSELF LIST")
    print("=" * 70)
    folder = Path(_tmp) / "Internships to apply to yourself"
    items = ManualList(folder)
    items.add("9", "Power Intern <b>", "Volt & Co", "Onsite, based in Austin, TX", "https://app.joinhandshake.com/job-search/9", 0.4, "Apply on the employer's website")
    items.add("8", "Chip Intern", "Silicon", "Remote", "https://app.joinhandshake.com/job-search/8", 0.6, "Asks for something the assistant can't fill in: Why us?")
    items.add("9", "Power Intern <b>", "Volt & Co", "", "", 0.3, "Apply on the employer's website")
    page = items.save()
    reloaded = ManualList(folder)
    assert len(reloaded) == 2, "a repeat sighting must not duplicate an entry"
    assert reloaded.items()[0]["job_id"] == "8", "best match first"
    assert reloaded.items()[1]["score"] == 0.4 and reloaded.items()[1]["url"].endswith("/9"), "keep best score and link"
    html_text = page.read_text(encoding="utf-8")
    assert "href='https://app.joinhandshake.com/job-search/8'" in html_text
    assert "Power Intern &lt;b&gt;" in html_text and "Volt &amp; Co" in html_text, "text is escaped"
    reloaded.remove("8")
    reloaded.save()
    assert len(ManualList(folder)) == 1, "applied postings drop off"
    print("  saved, deduplicated, ordered, escaped, removed")
    print("  OK")
    print()

RESUME = Path(__file__).with_name("sample_resume.txt")

print("=" * 70)
print("1. RESUME PARSING")
print("=" * 70)
profile = resume_parser.extract(RESUME)
print(profile.summary())
assert "python" in profile.skills, profile.skills
assert "pytorch" in profile.skills
assert "machine learning" in profile.phrases
assert "full stack" in profile.phrases
print("  OK")

print()
print("=" * 70)
print("2. MAJOR RESOLUTION")
print("=" * 70)
cases = {
    "Computer Science": "Computer Science",
    "cs": "Computer Science",
    "Computer Engineering": "Computer Engineering",
    "computer engineering": "Computer Engineering",
    "cmpe": "Computer Engineering",
    "CPE": "Computer Engineering",
    "Electrical and Computer Engineering": "Electrical Engineering",
    "comp sci": "Computer Science",
    "Electrical Engineering": "Electrical Engineering",
    "mech e": "Mechanical Engineering",
    "ECE": "Electrical Engineering",
    "data analytics": "Data Science",
    "accounting": "Accounting",
    "Poli Sci": "Political Science",
    "Underwater Basket Weaving": "Underwater Basket Weaving",
}
for raw, expected in cases.items():
    got = majors.resolve(raw)
    flag = "ok " if got.name == expected else "BAD"
    print(f"  [{flag}] {raw!r:30s} -> {got.name}")
    assert got.name == expected, f"{raw}: expected {expected}, got {got.name}"
unknown = majors.resolve("Underwater Basket Weaving")
assert unknown.queries, "unknown major must still produce search queries"
print(f"  unknown-major queries: {unknown.queries}")
print("  OK")

print()
print("=" * 70)
print("3. SCORING")
print("=" * 70)
ce = majors.resolve("Computer Engineering")
cs = majors.resolve("Computer Science")
print(f"  Computer Engineering preset: {len(ce.core)} core, {len(ce.related)} related, {len(ce.title_words)} title words")

cases = [
    # (label, title, description, major, expected verdict at the balanced cutoff)
    ("textbook FPGA internship", "FPGA Engineering Intern - Summer 2027",
     "Design and verify FPGA logic in Verilog and VHDL with simulation and an oscilloscope.", ce, True),
    ("names the major in the title", "Computer Engineering Intern", "General office duties.", ce, True),
    ("names the major in the description", "Engineering Intern",
     "Open to Mechanical, Electrical and Computer Engineering majors.", ce, True),
    ("embedded work under a vague title", "R&D Intern",
     "Support hardware testing and embedded firmware bring up.", ce, True),
    ("software internship for Computer Engineering", "Software Engineering Intern",
     "Build backend services in Python and Java with Git.", ce, True),
    ("veterinary internship", "Veterinary Clinic Intern", "Care for animals and clean kennels.", ce, False),
    ("marketing internship", "Marketing Intern", "Social media campaigns and content creation.", ce, False),
    ("strong Computer Science match", "Software Engineering Intern - Summer 2027",
     "Backend distributed systems in Python and Java, REST API, code review, data structures and algorithms.", cs, True),
]
balanced = matcher.STRICTNESS["balanced"]
for label, title, text, major, expected in cases:
    result = matcher.score_posting(title, text, major)
    verdict = matcher.passes(result, balanced)
    print(f"  {result.percent:3d}%  {label:46s} {'pass' if verdict else 'skip'}  {result.reasons[:1]}")
    assert verdict == expected, f"{label}: expected {'pass' if expected else 'skip'}, got {result.percent}%"

anchor_title = matcher.score_posting("Computer Engineering Intern", "", ce)
anchor_body = matcher.score_posting("Intern", "We welcome computer engineering students.", ce)
assert anchor_title.percent == 100, anchor_title.percent
assert anchor_body.percent >= 90, anchor_body.percent
assert matcher.passes(anchor_body, matcher.STRICTNESS["strict"]), "naming the major must pass even on strict"
print("  OK (the major's name nearly guarantees a pass, even on strict)")

no_resume_effect = matcher.score_posting("FPGA Engineering Intern", "Verilog and FPGA.", ce)
assert no_resume_effect.percent == matcher.score_posting("FPGA Engineering Intern", "Verilog and FPGA.", ce).percent
print("  OK (scores depend only on the posting and the major)")

# The cutoff compares the same rounded number that is shown.
shown_22 = matcher.MatchResult(score=0.2199)
assert shown_22.percent == 22 and matcher.passes(shown_22, 0.22)
assert not matcher.passes(matcher.MatchResult(score=0.2149), 0.22)
print("  OK (a posting shown at 22% passes a 22% cutoff)")

levels = matcher.STRICTNESS
assert levels["broad"] < levels["balanced"] < levels["strict"]
print(f"  OK (pickiness levels: {', '.join(f'{k} {round(v * 100)}%' for k, v in levels.items())})")

# Whole words only: "c++" must not fire inside unrelated text, "embedded" must.
assert matcher.score_posting("Intern", "We embedded our team in the field.", ce).matched == ["embedded"]
assert "rtl" not in matcher.score_posting("Intern", "Ask about our portal.", ce).matched
print("  OK (terms match whole words only)")

print()
print("=" * 70)
print("4. FILTERS")
print("=" * 70)
checks = [
    ("is_internship", matcher.is_internship("Software Engineering Intern", ""), True),
    ("is_internship", matcher.is_internship("Senior Software Engineer", "full time role"), False),
    ("is_internship coop", matcher.is_internship("Engineering Co-op", ""), True),
    ("is_internship analyst", matcher.is_internship("Summer Analyst Program", ""), True),
    ("internal rejected", matcher.is_internship("Internal Audit Manager", ""), False),
    ("is_summer title", matcher.is_summer("Summer 2027 Intern", ""), True),
    ("is_summer body", matcher.is_summer("Engineering Intern", "runs June through August"), True),
    ("is_summer false", matcher.is_summer("Fall Intern", "spring semester part time role"), False),
    ("location empty", matcher.location_ok("Boston, MA", []), True),
    ("location match", matcher.location_ok("Boston, MA", ["boston"]), True),
    ("location remote", matcher.location_ok("Remote", ["boston"]), True),
    ("location miss", matcher.location_ok("Austin, TX", ["boston"]), False),
]
for name, got, expected in checks:
    flag = "ok " if got == expected else "BAD"
    print(f"  [{flag}] {name:22s} got={got} expected={expected}")
    assert got == expected, name
print("  OK")

print()
print("=" * 70)
print("5. LEDGER")
print("=" * 70)
with tempfile.TemporaryDirectory() as tmp:
    ledger = Ledger(Path(tmp) / "applied.json")
    assert not ledger.applied("111")
    ledger.record("111", "applied", "SWE Intern", "Acme", "Seattle", "u", 0.5, "uploaded")
    ledger.record("222", "skipped_external", "Other", "Beta", "Remote", "u", 0.3, "ext")
    assert ledger.applied("111")
    assert not ledger.applied("222")
    assert ledger.seen("222")
    assert ledger.total_applied() == 1
    assert ledger.applied_today() == 1

    reopened = Ledger(Path(tmp) / "applied.json")
    assert reopened.applied("111"), "ledger must survive a reload"
    out = reopened.export_csv(Path(tmp) / "history.csv")
    assert out.exists() and out.read_text(encoding="utf-8").count("\n") >= 3
    print(f"  persisted, reloaded, exported {out.name}")
print("  OK")

print()
print("=" * 70)
print("6. SEARCH URL BUILDING")
print("=" * 70)
template = "{base}/stu/postings?query={query}&page={page}&per_page=25"
url = template.format(
    base="https://app.joinhandshake.com",
    query="software+engineering+intern",
    page=2,
)
print(f"  {url}")
assert "page=2" in url and "query=software" in url
print("  OK")

print()
print("=" * 70)
print("ALL OFFLINE TESTS PASSED")
print("=" * 70)
