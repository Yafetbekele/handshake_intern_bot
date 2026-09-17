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
from fake_handshake import JOBS, serve
from handshake import Documents, HandshakeSession, classify_document_label

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
            check(
                "found every posting",
                sorted(ids) == sorted(JOBS),
            )
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
            check("title is the job, not the page heading", job.title != "Jobs", job.title)
            check("location read from At a glance", job.location == "Onsite, based in Seattle, WA", job.location)
            check("Show more was expanded", "algorithms is required" in job.description)

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
            spring = session.load_job("1007")
            check("spring internship recognised as not summer", matcher.summer_status(spring.title, spring.description) == "other")
            quick = session.load_job("1006")
            check("'Quick apply' button recognised", quick.apply_kind == "quick", quick.apply_kind)
            status, note = session.apply(quick, Documents(resume_path="nonexistent.pdf"), dry_run=True)
            check("practice run never clicks Quick apply", status == "dry_run" and "not clicked" in note, note)
            check("Quick apply form was not opened", not session.page.locator("#dialog").is_visible())

            print()
            print("=" * 70)
            print("5. DRY RUN LEAVES THE APPLICATION UNSUBMITTED")
            print("=" * 70)
            docs = Documents(resume_path="nonexistent.pdf")
            status, note = session.apply(job, docs, dry_run=True)
            print(f"  status={status}  note={note}")
            check("dry run reported", status == "dry_run", status)
            check("resume already attached is recognised", "Academic Resume.pdf" in note, note)
            check("form closed after the practice run", not session.page.locator("#dialog").is_visible())
            body = session.page.inner_text("body")
            check("nothing was submitted", "Application submitted" not in body)

            print()
            print("=" * 70)
            print("6. REAL SUBMISSION PATH")
            print("=" * 70)
            job = session.load_job("1001")
            status, note = session.apply(job, docs, dry_run=False)
            print(f"  status={status}  note={note}")
            check("application submitted", status == "applied", status)
            picked = session.page.get_attribute("#done", "data-resume")
            check("resume went with the application", picked == "Academic Resume.pdf", f"value={picked}")
            check("success recognised from the Withdraw button", session._application_succeeded())

            print()
            print("=" * 70)
            print("7. EXTERNAL POSTING IS SKIPPED, NOT HALF-APPLIED")
            print("=" * 70)
            status, note = session.apply(external, docs, dry_run=False)
            print(f"  status={status}  note={note}")
            check("external skipped", status == "skipped_external", status)

            print()
            print("=" * 70)
            print("8. REQUIRED QUESTION BLOCKS SUBMISSION")
            print("=" * 70)
            needs_answer = session.load_job("1005")
            status, note = session.apply(needs_answer, docs, dry_run=False)
            print(f"  status={status}  note={note}")
            check("flagged for manual follow-up", status == "needs_manual", status)
            check("names the unanswered question", "Why do you want to intern" in note, note)
            check("unnamed cover letter is not guessed", "attach your cover letter" in note, note)
            check("form closed without submitting", "Application submitted" not in session.page.inner_text("body"))

            with_letter = Documents(resume_path="nonexistent.pdf", cover_letter_name="Cover Letter")
            needs_answer = session.load_job("1005")
            status, note = session.apply(needs_answer, with_letter, dry_run=False)
            print(f"  with a named cover letter: status={status}  note={note}")
            check("named cover letter gets attached", "cover letter" not in note.lower().split("required:")[-1], note)
            check("still held back for the written question", status == "needs_manual" and "Why do you want" in note, note)
            body = session.page.inner_text("body")
            check("nothing was submitted", "Application submitted" not in body)

            print()
            print("=" * 70)
            print("9. REQUIRED TRANSCRIPT IS ATTACHED")
            print("=" * 70)
            transcript_job = session.load_job("1006")
            status, note = session.apply(transcript_job, docs, dry_run=False)
            print(f"  status={status}  note={note}")
            check("transcript posting submitted", status == "applied", status)
            check(
                "transcript chosen",
                session.page.get_attribute("#done", "data-transcript") == "Transcript Spring 2026.pdf",
            )
            check(
                "resume still chosen",
                session.page.get_attribute("#done", "data-resume") == "Academic Resume.pdf",
            )

            print()
            print("=" * 70)
            print("10. DOCUMENT SLOT LABELS")
            print("=" * 70)
            for label, expected in [
                ("Resume *", "resume"),
                ("Upload your CV", "resume"),
                ("Cover letter (required)", "cover_letter"),
                ("Unofficial transcript", "transcript"),
                ("Why do you want to intern with us?", "unknown"),
            ]:
                got = classify_document_label(label)
                check(f"{label!r} -> {expected}", got == expected, got)

            print()
            print("=" * 70)
            print("11. APPLIES TO THE RIGHT POSTING AFTER BROWSING OTHERS")
            print("=" * 70)
            target = session.load_job("1001")
            session.load_job("1005")
            session.load_job("1006")  # browser now sits on a different posting
            status, note = session.apply(target, docs, dry_run=False)
            print(f"  status={status}  note={note}")
            print(f"  final url: {session.page.url}")
            check("navigated back to the target posting", session.page.url.rstrip("/").endswith("/1001"))
            check("target posting submitted", status == "applied", status)
            check("no transcript from the other posting", "transcript" not in note, note)

            print()
            print("=" * 70)
            print("12. SEARCH PAGE MOVED IS REPORTED, NOT SILENT")
            print("=" * 70)
            original_template = session.config["search_url_template"]
            session.config["search_url_template"] = "{base}/old/postings?query={query}&page={page}"
            try:
                session.collect_job_ids(["anything"], max_pages=1)
                check("redirected search stops the run", False, "no stop")
            except SystemExit as exc:
                message = str(exc.code)
                print("  " + message.strip().splitlines()[0])
                check("redirected search stops the run", "redirected the job search" in message)
                check("message names where it went", "/home" in message)

            print()
            print("=" * 70)
            print("13. SECURITY CHECK STOPS THE RUN")
            print("=" * 70)
            session.config["search_url_template"] = "{base}/guarded/postings?query={query}&page={page}"
            try:
                session.collect_job_ids(["anything"], max_pages=1)
                check("security check stops the run", False, "no stop")
            except SystemExit as exc:
                message = str(exc.code)
                print("  " + message.strip().splitlines()[0])
                check("security check stops the run", "security check" in message)
            session.config["search_url_template"] = original_template

            session.page.goto(BASE + "/stu/postings")
            check("normal pages are not mistaken for a check", not session.security_check_showing())

            print()
            print("=" * 70)
            print("14. SEARCH BY TYPING ON THE JOB SEARCH PAGE")
            print("=" * 70)
            session.config["search_url_template"] = ""
            session._learned_template = ""
            ids = session.collect_job_ids(["software engineering intern"], max_pages=3)
            print(f"  ids: {ids}")
            check("typed search finds every posting", sorted(ids) == sorted(JOBS))
            check("search address learned", "{query}" in session._learned_template, session._learned_template)
            check("learned address keeps the path", "/job-search?" in session._learned_template)
            check("learned address pages", "{page}" in session._learned_template)

            ids_again = session.collect_job_ids(["backend intern"], max_pages=2)
            check("second search reuses the learned address", len(ids_again) == len(JOBS), str(ids_again))
            check("reuse went through the address, not the box", "query=backend" in session.page.url.replace("+", "%20").replace("%20", "+") or "backend" in session.page.url)

            print()
            print("=" * 70)
            print("15. CLICKABLE RESULT CARDS WITHOUT LINKS")
            print("=" * 70)
            session.config["search_url_template"] = "{base}/job-search?query={query}&page={page}&style=buttons"
            ids = session.collect_job_ids(["anything"], max_pages=1)
            print(f"  ids: {ids}")
            check("ids read by clicking cards", sorted(ids) == sorted(JOBS))
            session.config["search_url_template"] = original_template

            print()
            print("=" * 70)
            print("16. ONLY THE JOB'S OWN PANEL IS READ")
            print("=" * 70)
            detail = session.load_job("1001")
            print(f"  url={detail.url}  apply={detail.apply_kind}  employer={detail.employer}")
            check("job opened at the current address", "/job-search/1001" in detail.url)
            check("a neighbouring 'You applied' badge is ignored", detail.apply_kind == "quick", detail.apply_kind)
            check("description excludes the results list", "Veterinary" not in detail.description)
            check("employer read from the panel", detail.employer == "Acme Cloud", detail.employer)
            check("job URL recognised", session._on_job_id("1001"))

            session.page.goto(BASE + "/visibility-settings")
            check("setup screens are not treated as signed in", not session.looks_logged_in())

            print()
            print("=" * 70)
            print("17. TAILORED RESUME REPLACES THE DEFAULT FOR ONE APPLICATION")
            print("=" * 70)
            tailored_pdf = Path(tmp) / "Jane_Doe_Resume.pdf"
            tailored_pdf.write_bytes(b"%PDF-1.4\n% tailored resume stand-in\n")
            tailored_docs = Documents(resume_path=str(tailored_pdf), tailored_resume=True)

            job = session.load_job("1001")
            status, note = session.apply(job, tailored_docs, dry_run=True)
            print(f"  practice run: status={status}  note={note}")
            check("practice run does not upload", status == "dry_run" and "not uploaded" in note, note)
            check("default resume left attached in a practice run",
                  session.page.evaluate("() => window.picked.resume") == "Academic Resume.pdf")

            job = session.load_job("1001")
            status, note = session.apply(job, tailored_docs, dry_run=False)
            print(f"  real run: status={status}  note={note}")
            check("application submitted with the tailored resume", status == "applied", status)
            check("tailored file is what went out",
                  session.page.get_attribute("#done", "data-resume") == "Jane_Doe_Resume.pdf",
                  str(session.page.get_attribute("#done", "data-resume")))

            print()
            print("=" * 70)
            print("18. SIMPLE QUESTIONS ANSWERED FROM SAVED ANSWERS")
            print("=" * 70)
            saved_answers = [
                {"match": ["phone", "mobile"], "value": "(555) 010-0000"},
                {"match": ["authorized to work", "legally authorized"], "value": "Yes", "kind": "yesno"},
                {"match": ["sponsorship"], "value": "No", "kind": "yesno"},
            ]
            simple = session.load_job("1008")
            status, note = session.apply(simple, docs, dry_run=False, answers=saved_answers)
            print(f"  status={status}  note={note}")
            check("application went through", status == "applied", status)
            typed = session.page.get_attribute("#done", "data-answers") or ""
            print(f"  form received: {typed}")
            check("phone number filled in", "(555) 010-0000" in typed, typed)
            check("work authorization answered Yes", "q-1-y=Yes" in typed, typed)

            print()
            print("=" * 70)
            print("19. SENSITIVE QUESTIONS ARE LEFT FOR THE STUDENT")
            print("=" * 70)
            sensitive = session.load_job("1009")
            status, note = session.apply(sensitive, docs, dry_run=False, answers=saved_answers[:1])
            print(f"  no answer saved: status={status}  note={note}")
            check("held back for the student", status == "needs_manual", status)
            check("names the sponsorship question", "sponsorship" in note.lower(), note)
            check("nothing submitted", "Application submitted" not in session.page.inner_text("body"))

            print()
            print("=" * 70)
            print("20. THE STUDENT'S OWN ANSWER TO A PERSONAL QUESTION IS USED")
            print("=" * 70)
            with_sponsorship = saved_answers + [{"match": ["sponsorship"], "value": "No"},
                                                {"match": ["social security"], "value": "123-45-6789"}]
            sensitive = session.load_job("1009")
            status, note = session.apply(sensitive, docs, dry_run=False, answers=with_sponsorship)
            print(f"  status={status}  note={note}")
            check("sponsorship answered from the student's words", "sponsorship" in note.lower() and "No" in note, note)
            check("social security number never filled in", "never fills this in" in note, note)
            check("still held back because of it", status == "needs_manual", status)
            typed = session.page.get_attribute("#done", "data-answers") or ""
            check("nothing was submitted", "Application submitted" not in session.page.inner_text("body"), typed)

            print()
            print("=" * 70)
            print("21. ASKING THE STUDENT DURING THE RUN")
            print("=" * 70)
            asked: list[str] = []

            def ask_stub(question: str) -> str | None:
                asked.append(question)
                if "sponsorship" in question.lower():
                    return "No"
                return None

            sensitive = session.load_job("1009")
            status, note = session.apply(sensitive, docs, dry_run=False, answers=saved_answers[:1], ask=ask_stub)
            print(f"  asked: {asked}")
            print(f"  status={status}  note={note}")
            check("the student was asked about sponsorship", any("sponsorship" in q.lower() for q in asked))
            check("the student was never asked for a social security number",
                  not any("social security" in q.lower() for q in asked))
            check("their answer was used", "No" in note, note)
            check("the answer was kept for next time",
                  any("sponsorship" in " ".join(a["match"]).lower() for a in session.last_learned_answers),
                  str(session.last_learned_answers))
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
