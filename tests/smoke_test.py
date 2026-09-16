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
from storage import Ledger

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
cs = majors.resolve("Computer Science")
weights = matcher.build_weights(profile, cs.keywords, ["internship"])
print(f"  vocabulary size: {len(weights)}")

good = """Software Engineering Intern - Summer 2027
Acme Cloud, Seattle WA
Join our backend team building distributed systems in Python and Java.
You will write REST API endpoints, participate in code review, use Git and
Docker, and work in an agile environment. Coursework in data structures and
algorithms required. This is a paid summer internship for undergraduates."""

bad = """Summer Intern - Veterinary Clinic Assistant
Happy Paws, Dayton OH
Help our veterinary staff care for animals, clean kennels, greet clients,
and assist with feeding schedules. No prior experience necessary."""

mid = """Data Analyst Intern, Summer 2027
Insight Corp, Remote
Analyze datasets with SQL and Python, build dashboards in Tableau, and
present insights to stakeholders."""

for label, text in [("strong CS match", good), ("weak match", bad), ("partial match", mid)]:
    result = matcher.score_job(text, weights)
    print(f"  {label:18s} {result.percent:3d}%  top: {', '.join(result.matched[:6])}")

strong = matcher.score_job(good, weights)
weak = matcher.score_job(bad, weights)
partial = matcher.score_job(mid, weights)
assert strong.score > partial.score > weak.score, (strong.score, partial.score, weak.score)
assert strong.score > 0.22, "strong match should clear the default threshold"
assert weak.score < 0.22, "weak match should fail the default threshold"
print("  OK (ranking is strong > partial > weak)")

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
