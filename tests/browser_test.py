"""Drive the Playwright layer against a local mock of Handshake.

This never touches the real site or any account. Run from the project root:
    python tests/browser_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import majors
import matcher
import resume_parser
from fake_handshake import serve
from handshake import HandshakeSession

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"

selectors = json.loads((ROOT / "selectors.json").read_text(encoding="utf-8"))
selectors = {k: v for k, v in selectors.items() if isinstance(v, list)}

config = {
    "handshake_base_url": BASE,
    "search_url_template": "{base}/stu/postings?query={query}&page={page}",
    "headless": True,
}

server, _thread = serve(PORT)
print(f"Mock Handshake serving at {BASE}\n")

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


try:
    with tempfile.TemporaryDirectory() as tmp:
        with HandshakeSession(config, selectors, Path(tmp) / "profile") as session:
            print("=" * 70)
            print("1. BROWSER LAUNCH AND SIGN-IN DETECTION")
            print("=" * 70)
            check("browser context started", session.page is not None)
            check("ensure_logged_in passes", session.ensure_logged_in(timeout_seconds=20))

            print()
            print("=" * 70)
            print("2. SEARCH RESULT SCRAPING")
            print("=" * 70)
            ids = session.collect_job_ids(["software engineering intern"], max_pages=3)
            print(f"  ids: {ids}")
            check("found all four postings", sorted(ids) == ["1001", "1002", "1003", "1004"])
            check("no duplicates", len(ids) == len(set(ids)))

            print()
            print("=" * 70)
            print("3. JOB DETAIL EXTRACTION")
            print("=" * 70)
            job = session.load_job("1001")
            print(f"  title    : {job.title}")
            print(f"  employer : {job.employer}")
            print(f"  location : {job.location}")
            print(f"  apply    : {job.apply_kind}")
            print(f"  body len : {len(job.description)}")
            check("title parsed", job.title.startswith("Software Engineering Intern"))
            check("employer parsed", job.employer == "Acme Cloud")
            check("location parsed", "Seattle" in job.location)
            check("description captured", "distributed systems" in job.description)
            check("quick apply detected", job.apply_kind == "quick")

            external = session.load_job("1002")
            check(
                "external posting detected",
                external.apply_kind == "external",
                external.apply_kind,
            )

            print()
            print("=" * 70)
            print("4. FILTERS AND SCORING ON LIVE PAGES")
            print("=" * 70)
            profile = resume_parser.extract(Path(__file__).with_name("sample_resume.txt"))
            cs = majors.resolve("Computer Science")
            weights = matcher.build_weights(profile, cs.keywords, [])

            vet = session.load_job("1003")
            senior = session.load_job("1004")

            swe_score = matcher.score_job(job.search_text, weights)
            vet_score = matcher.score_job(vet.search_text, weights)
            print(f"  SWE intern     {swe_score.percent}%")
            print(f"  vet intern     {vet_score.percent}%")
            check("SWE outranks vet posting", swe_score.score > vet_score.score)
            check("SWE clears threshold", swe_score.score >= 0.22, f"{swe_score.percent}%")
            check("vet fails threshold", vet_score.score < 0.22, f"{vet_score.percent}%")
            check(
                "full-time role rejected",
                not matcher.is_internship(senior.title, senior.description),
            )
            check("summer detected", matcher.is_summer(job.title, job.description))

            print()
            print("=" * 70)
            print("5. DRY RUN LEAVES THE APPLICATION UNSUBMITTED")
            print("=" * 70)
            status, note = session.apply(job, "nonexistent.pdf", "", dry_run=True)
            print(f"  status={status}  note={note}")
            check("dry run reported", status == "dry_run", status)
            check("resume picked from saved documents", "Jane Doe Resume" in note, note)
            body = session.page.inner_text("body")
            check("nothing was submitted", "Application submitted" not in body)

            print()
            print("=" * 70)
            print("6. REAL SUBMISSION PATH")
            print("=" * 70)
            job = session.load_job("1001")
            status, note = session.apply(job, "nonexistent.pdf", "", dry_run=False)
            print(f"  status={status}  note={note}")
            check("application submitted", status == "applied", status)
            picked = session.page.get_attribute("#done", "data-picked")
            check("correct document attached", picked == "r1", f"value={picked}")

            print()
            print("=" * 70)
            print("7. EXTERNAL POSTING IS SKIPPED, NOT HALF-APPLIED")
            print("=" * 70)
            status, note = session.apply(external, "nonexistent.pdf", "", dry_run=False)
            print(f"  status={status}  note={note}")
            check("external skipped", status == "skipped_external", status)
finally:
    server.shutdown()

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL BROWSER TESTS PASSED")
print("=" * 70)
