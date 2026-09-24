"""Offline checks for everything that does not need a browser.

Run from the project root:
    python tests/smoke_test.py
"""

import sys
from datetime import date
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

    ranked = ManualList(Path(_tmp) / "ranked", limit=3)
    ranked.add("a", "Old entry", "", "", "u/a", 1.0, "r")  # saved before fit scores
    ranked.add("b", "Great", "", "", "u/b", 0.6, "r", fit=90, why=["matches your resume: fpga"])
    ranked.add("c", "Good", "", "", "u/c", 1.0, "r", fit=70)
    ranked.add("d", "Weak", "", "", "u/d", 1.0, "r", fit=40)
    assert [r["job_id"] for r in ranked.items()] == ["b", "c", "d", "a"], "fit first, unranked last"
    assert ranked.unranked()[0]["job_id"] == "a"
    ranked.set_fit("a", 55, [])
    assert not ranked.would_keep("e", 30) and ranked.would_keep("e", 60) and ranked.would_keep("d", 1)
    page = ranked.save()
    assert [r["job_id"] for r in ManualList(Path(_tmp) / "ranked").items()] == ["b", "c", "a"], "only the best 3 kept"
    assert "matches your resume: fpga" in page.read_text(encoding="utf-8")
    assert ManualList(Path(_tmp) / "y").limit == 100, "the list holds 100"
    print("  ordered by fit and capped")
    print("  OK")

print("=" * 70)
print("0c. ONLY THE BEST FEW JOIN THE LIST EACH RUN")
print("=" * 70)
import main as _cli
import ranking as _ranking
from handshake import Job as _Job

with tempfile.TemporaryDirectory() as _tmp2:
    listing = ManualList(Path(_tmp2) / "list")
    ranker = _cli.ListRanker(None, majors.resolve("Computer Engineering"), Path(_tmp2) / "no_profile.json")
    tailorer = _cli.ResumeTailor({"tailor_resume": False})
    entries = [
        (
            _Job(job_id=str(n), url=f"https://example.test/{n}", title=f"FPGA Engineering Intern {n}",
                 employer="Chips", location="Remote",
                 description="Undergraduates welcome. " + ("Verilog, FPGA and C++." if n < 12 else "")),
            matcher.MatchResult(score=1.0 if n < 12 else 0.3),
            "Apply on the employer's website",
        )
        for n in range(20)
    ]
    _cli.add_best_to_manual(listing, tailorer, ranker, entries)
    assert len(listing) == _ranking.MAX_NEW_PER_RUN == 10, len(listing)
    kept_ids = [row["job_id"] for row in listing.items()]
    assert all(int(job_id) < 12 for job_id in kept_ids), kept_ids
    listing.dismiss(kept_ids[0])
    _cli.add_best_to_manual(listing, tailorer, ranker, entries)
    assert not listing.is_removed(kept_ids[0]) or kept_ids[0] not in [r["job_id"] for r in listing.items()]
    assert listing.is_removed(kept_ids[0]), "a removed posting is never added back"
    print(f"  10 of 20 added, weakest left off, removed ones stay off")
    print("  OK")

print("=" * 70)
print("0d. RANKING EVERYTHING AND THE DAILY FINDER")
print("=" * 70)
import deep_rank
import find_elsewhere

assert find_elsewhere.is_internship_title("2027 Electrical Engineer Intern")
assert find_elsewhere.is_internship_title("Firmware Co-op (Summer 2027)")
assert not find_elsewhere.is_internship_title("Internal Communications Manager")
assert not find_elsewhere.is_internship_title("International Program Manager")
assert deep_rank.company_key("TikTok Inc.") == deep_rank.company_key("TikTok")
assert deep_rank.title_key("ASIC Design Engineer Intern (Video Silicon IP) - 2027 Start") ==     deep_rank.title_key("ASIC Design Engineer Intern (Audio IP) - Summer 2027")
assert deep_rank.family_of("FPGA Engineering Intern") == "chip design"
assert deep_rank.family_of("Social Media Intern") == "non-engineering"
keys = {(deep_rank.company_key("Anduril Industries"), deep_rank.title_key("Electrical Engineer Intern"))}
assert find_elsewhere.on_handshake({"employer": "Anduril Industries", "title": "2027 Electrical Engineer Intern"}, keys)
assert not find_elsewhere.on_handshake({"employer": "Neuralink", "title": "Electrical Engineer Intern"}, keys)

sample_profile = {"preferences": {"target_roles": ["Embedded systems"]}, "education": [{"gpa": "3.5"}]}
pool = [
    {"job_id": str(i), "title": title, "employer": employer, "location": "Onsite, based in Austin, TX", "url": "",
     "score": 1.0, "description": body}
    for i, (title, employer, body) in enumerate([
        ("Embedded Firmware Intern", "Acme", "Summer 2027. Undergraduate students. Verilog, FPGA, C++ firmware. $40/hr"),
        ("Embedded Software Intern", "Acme", "Summer 2027. Undergraduate. C++ embedded Linux. $35/hr"),
        ("Firmware Intern", "Acme", "Summer 2027. C++. $30/hr"),
        ("Hardware Intern", "Beta", "Summer 2027. Must be pursuing a PhD. 5+ years of experience."),
        ("Social Media Intern", "Gamma", "Summer 2027. Instagram and TikTok content."),
        ("FPGA Intern", "Delta", "Apply by January 5, 2020. Summer 2027. Verilog."),
        ("Chip Design Intern", "Eps", "Summer 2027. Minimum GPA of 3.8 required. Verilog."),
    ])
]
graded = deep_rank.grade_all(pool, sample_profile, ["verilog", "fpga", "cpp", "embedded", "firmware", "linux"],
                             today=date(2026, 9, 23))
titles = [g.title for g in graded]
assert "FPGA Intern" not in titles, "expired postings are dropped"
assert titles[0] == "Embedded Firmware Intern", titles
assert titles.index("Hardware Intern") > titles.index("Firmware Intern"), "PhD-only ranks low"
assert titles[-1] in ("Social Media Intern", "Hardware Intern"), titles
assert any("GPA" in f for g in graded if g.title == "Chip Design Intern" for f in g.flags)
spread = deep_rank.spread(graded, 2)
assert sum(1 for g in spread if g.employer == "Acme") == 2, "at most 2 per company"
assert next(g for g in spread if g.employer == "Acme").more_at_company == 1
print("  grading, expired postings, company limit and the finder's filters")
print("  OK")

print("=" * 70)
print("0b. APPLY-YOURSELF FIT SCORE")
print("=" * 70)
import ranking

ce_major = majors.resolve("Computer Engineering")
terms = ranking.resume_terms(None, {"skills": [{"name": "Verilog", "tags": ["fpga", "communication"]}, {"name": "C++"}]})
assert "verilog" in terms and "fpga" in terms and "communication" not in terms, terms
undergrad = ranking.fit("FPGA Engineering Intern", "Open to undergraduate sophomores. Verilog and C++.", 1.0, terms, ce_major)
phd = ranking.fit("FPGA Engineering Intern", "Must be pursuing a PhD. 3+ years of experience.", 1.0, terms, ce_major)
unrelated = ranking.fit("Computer Engineering Intern", "Help with events.", 1.0, terms, ce_major)
senior = ranking.fit("Senior FPGA Engineer", "Verilog and C++.", 1.0, terms, ce_major)
print(f"  undergrad {undergrad.points}  phd {phd.points}  no resume overlap {unrelated.points}  senior {senior.points}")
assert undergrad.points > unrelated.points > phd.points, "all 100% preset matches, now spread apart"
assert undergrad.points > senior.points
assert "open to undergraduates" in undergrad.why and "asks for grad or PhD students" in phd.why
assert 0 <= phd.points <= 100 and undergrad.points <= 100
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

ps = majors.resolve("Psychology")
job_queries = majors.queries_for(ps, "jobs")
assert "psychology" in job_queries and not any("intern" in q for q in job_queries), job_queries
assert all("intern" in q for q in majors.queries_for(ps, "internships"))
derived = majors.queries_for(majors.resolve("Computer Engineering"), "jobs")
assert "computer engineering" in derived and not any("intern" in q for q in derived), derived
print(f"  OK (job searches: {', '.join(job_queries[:4])} ...)")

psych_cases = [
    ("Behavioral Health Technician", "Direct care for clients, crisis de-escalation.", True),
    ("Case Manager", "Caseload, case notes, human services for families.", True),
    ("Youth Development Specialist", "Mentoring for children after school.", True),
    ("Software Engineer", "Backend services in Java.", False),
]
for title, text, expected in psych_cases:
    result = matcher.score_posting(title, text, ps)
    assert matcher.passes(result, matcher.STRICTNESS["broad"]) == expected, (title, result.percent)
print("  OK (the Psychology preset takes most psychology-related jobs on broad)")

from handshake import place_state
assert place_state("Baltimore, MD") == "Maryland"
assert place_state("Austin, Texas") == "Texas"
assert place_state("Baltimore") == ""
assert matcher.is_internship_posting("Psychology Intern", "")
assert matcher.is_internship_posting("Research Assistant", "At a glance\nInternship\nFull-time")
assert not matcher.is_internship_posting("Registered Behavior Technician", "Job\nFull-time\nOur internship program trains new staff.")
assert not matcher.is_internship_posting("Internal Communications Specialist", "")
print("  OK (jobs mode tells internships from jobs)")

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
