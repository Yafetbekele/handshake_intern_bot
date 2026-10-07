"""Checks for applying on employers' own sites, against local stand-ins.

Nothing here touches a real employer, the real Handshake, or the student's
real profile and lists. Run from the project root:
    python tests/employer_apply_test.py
"""

from __future__ import annotations

import builtins
import csv
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

WORK = Path(tempfile.mkdtemp(prefix="hsbot_employer_"))
PROFILE = WORK / "career_profile.json"
# Never the student's real profile, lists, data or tailored resumes.
os.environ["HSBOT_PROFILE"] = str(PROFILE)
os.environ["HSBOT_DATA_DIR"] = str(WORK / "data")
os.environ["HSBOT_MANUAL_DIR"] = str(WORK / "list")
os.environ["HSBOT_TAILORED_DIR"] = str(WORK / "tailored")
os.environ["HSBOT_COVER_BASE"] = str(WORK / "no_base.txt")
os.environ["HSBOT_NO_OPEN"] = "1"
# A fake Claude, so written answers cost no plan usage.
_fake = HERE / "fake_claude.py"
if os.name == "nt":
    FAKE = WORK / "claude.cmd"
    FAKE.write_text(f'@"{sys.executable}" "{_fake}" %*\r\n', encoding="utf-8")
else:
    FAKE = WORK / "claude"
    FAKE.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_fake}" "$@"\n', encoding="utf-8")
    FAKE.chmod(0o755)
os.environ["HSBOT_CLAUDE"] = str(FAKE)

profile = json.loads((HERE / "sample_profile.json").read_text(encoding="utf-8"))
profile["application_answers"] = [
    {"match": ["phone", "mobile"], "value": "(555) 010-0000"},
    {"match": ["school", "university", "college"], "value": "University of Springfield"},
    {"match": ["state"], "value": "Illinois"},
    {"match": ["degree", "education level"], "value": "Bachelor's"},
    {"match": ["authorized to work", "legally authorized"], "value": "Yes", "kind": "yesno"},
    {"match": ["require sponsorship", "sponsorship"], "value": "No", "sensitive": True},
    {"match": ["which locations would you consider"], "value": "Remote; Austin, Texas"},
    {"match": ["which of the following best describes you"], "value": "A United States Citizen", "sensitive": True},
    {"match": ["work onsite"], "value": "Yes"},
    {"match": ["gender"], "value": "Decline to self-identify", "sensitive": True},
    {"match": ["social security"], "value": "000-00-0000"},
]
PROFILE.write_text(json.dumps(profile, indent=2), encoding="utf-8")
RESUME = WORK / "My_Usual_Resume.pdf"  # a resume tailored to a job is named Jane_Doe_Resume.pdf
RESUME.write_bytes(b"%PDF-1.4 test resume")
TYPED_LETTER = "Dear Hiring Team,\n\nI built a Raspberry Pi weather station.\n\nSincerely,\nJane Doe"

import applied_myself  # noqa: E402
import employer_apply  # noqa: E402
import essay_answers  # noqa: E402
import fake_employer_sites  # noqa: E402
import form_answers  # noqa: E402
import fake_handshake  # noqa: E402
from employer_sites import (  # noqa: E402
    EmployerSite,
    apply_address,
    candidates,
    clean_label,
    document_kind,
    pick_option,
    profile_answers,
    remember_answers,
    site_kind,
)
from handshake import HandshakeSession  # noqa: E402

EMPLOYER_PORT, HANDSHAKE_PORT = 8771, 8772
EMPLOYER = f"http://127.0.0.1:{EMPLOYER_PORT}"
HANDSHAKE = f"http://127.0.0.1:{HANDSHAKE_PORT}"
JOB = "8c6cfcee-c6da-4a73-9bdf-3ef098ff59e6"

failures: list[str] = []


def check(name: str, condition: bool, detail: object = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail != "" and not condition else ""))
    if not condition:
        failures.append(name)


def section(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def answers_now() -> list[dict]:
    saved = json.loads(PROFILE.read_text(encoding="utf-8"))
    return profile_answers(saved) + saved["application_answers"]


def documents(kind: str, required: bool) -> Path | None:
    return RESUME if kind == "resume" else None


def last(path: str) -> dict:
    return next(s for s in reversed(fake_employer_sites.SUBMISSIONS) if s["path"].startswith(path))


section("1. WHICH SITE, AND WHICH SAVED ANSWER FITS A QUESTION")
check("Greenhouse recognised", site_kind("https://job-boards.greenhouse.io/acme/jobs/1")[0] == "greenhouse")
check("Lever recognised", site_kind("https://jobs.lever.co/acme/" + JOB)[0] == "lever")
check("Ashby recognised", site_kind("https://jobs.ashbyhq.com/acme/" + JOB)[0] == "ashby")
check("Workday needs an account", site_kind("https://acme.wd5.myworkdayjobs.com/en-US/careers/job/1") == ("account", "Workday"))
check("a company's own site is 'other'", site_kind("https://careers.acme.com/jobs/1")[0] == "other")
check("a look-alike host isn't Lever", site_kind("https://notlever.co/x")[0] == "other")
check("Lever's form is at /apply", apply_address("https://jobs.lever.co/acme/" + JOB).endswith(JOB + "/apply"))
check("Ashby's form is at /application", apply_address("https://jobs.ashbyhq.com/acme/" + JOB).endswith(JOB + "/application"))
check("an address already on the form is kept", apply_address("https://jobs.lever.co/acme/" + JOB + "/apply").count("/apply") == 1)
check("required marks are dropped", clean_label("First Name*") == "First Name" and clean_label("Email ✱") == "Email"
      and clean_label("Top choice? (required)") == "Top choice?")

check("a field's own code tacked onto its label is dropped",
      clean_label("Location 296526d0") == "Location" and clean_label("Graduation year 20282028") == "Graduation year 20282028"
      and clean_label("Describe the facade") == "Describe the facade")

saved = answers_now()
best = candidates("Are you legally authorized to work in the United States?", saved)
check("'authorized to work' beats 'state' in a work question", bool(best) and best[0]["value"] == "Yes", best[:1])
check("'State' alone still finds the state", candidates("State*", saved)[0]["value"] == "Illinois")
check("one common word in a long question isn't a match",
      candidates("Tell us about a time you worked with a state agency or university on a research project", saved) == [])
check("'Name' is the student's name", candidates("Name", saved)[0]["value"] == "Jane Doe")
check("'Company name' is not", candidates("Company name", saved) == [])
check("a question that only mentions email isn't the email question",
      candidates("Would you like email updates about new roles at Acme and its partners?", saved) == [])
check("a follow-up question doesn't get the main question's answer",
      candidates("If yes, what type of sponsorship?", saved) == [] and candidates("Other: please specify your school", saved) == [])
check("unless an answer was saved for exactly it",
      candidates("If yes, what type of sponsorship?", saved + [{"match": ["if yes, what type of sponsorship?"], "value": "None"}])[0]["value"] == "None")
check("first and last name split", candidates("First Name", saved)[0]["value"] == "Jane" and candidates("Last Name", saved)[0]["value"] == "Doe")
check("a saved answer beats the contact block", candidates("Phone", saved)[0]["value"] == "(555) 010-0000")
cut_short = [{"match": ["will you require sponsorship from acme for employment now or"], "value": "No"}]
check("a long saved question that was cut short still matches",
      bool(candidates("Will you require sponsorship from Acme for employment now or in the future (e.g. H1B)?", cut_short)))
later = saved + [{"match": ["degree"], "value": "Bachelor's Degree"}]
check("the newest answer to exactly this question comes first", candidates("Degree", later)[0]["value"] == "Bachelor's Degree")

check("the major, from the degree in the profile", candidates("Discipline", saved)[0]["value"] == "Computer Science")
check("graduation month and year, separately",
      (candidates("End date month", saved)[0]["value"], candidates("End date year", saved)[0]["value"]) == ("May", "2028"))
check("the GPA", candidates("Undergrad GPA", saved)[0]["value"] == "3.7")
degrees = ["Associate's Degree", "Bachelors", "Bachelor's Degree", "Master's Degree"]
check("the degree, in a form's own words",
      any(pick_option(degrees, str(a["value"])) == 2 for a in candidates("Degree", saved)), [a["value"] for a in candidates("Degree", saved)])
check("a master's GPA is not the GPA", candidates("Masters GPA", saved) == [])
check("nor a master's graduation date the graduation date",
      candidates("Select your anticipated master's degree graduation date", saved) == [])
check("'highest degree completed' is not the degree being studied for", candidates("Highest degree completed", saved) == [])

profile_raw = PROFILE.read_text(encoding="utf-8")
check("a legal question the profile is silent on is never worked out",
      not form_answers.grounded("Have you ever been convicted of a felony?", profile_raw.lower()))
check("one the student has answered before can be", form_answers.grounded("Will you require sponsorship from Acme?", profile_raw.lower()))
check("a plain question always can", form_answers.grounded("How did you hear about us?", profile_raw.lower()))
check("nothing is certified or consented to in the student's name unless they've said so",
      not form_answers.grounded("By submitting my application, I certify that the information provided is true", profile_raw.lower())
      and not form_answers.grounded("Do you consent to receive recruiting text messages?", profile_raw.lower()))
check("a question turned around isn't matched on its phrase",
      candidates("Is there anything that would prevent you from being able to work onsite?", saved) == []
      and candidates("Are you able to work in the United States without sponsorship?", saved) == [])
check("a figure from the profile is fine", form_answers.figures_known("2028", profile_raw, "Graduation year"))
check("a date written another way is fine", form_answers.figures_known("05/2028", profile_raw, "Graduation date"))
check("an invented figure is not", not form_answers.figures_known("3.97", profile_raw, "GPA"))
check("the phone number with other punctuation is fine", form_answers.figures_known("555-010-0000", profile_raw, "Phone"))
check("another phone number is not", not form_answers.figures_known("555-010-9999", profile_raw, "Phone"))

check("exact option", pick_option(["Yes", "No"], "no") == 1)
check("yes matches a longer yes", pick_option(["Yes, I am authorized", "No, I am not"], "Yes") == 0)
check("yes with two yes options is not guessed", pick_option(["Yes - program A", "Yes - program B", "No"], "yes") is None)
check("punctuation doesn't matter", pick_option(["University of Springfield - Shelbyville", "University of Springfield"], "University of Springfield") == 1)
check("a longer option that holds the answer", pick_option(["Bachelor's Degree", "Master's Degree"], "Bachelor's") == 0)
check("no match is no match", pick_option(["Male", "Female"], "Decline") is None)
check("female isn't male", pick_option(["Male", "Female"], "male") == 0)
check("C++ and C# aren't C", (pick_option(["C", "C++", "C#"], "C++"), pick_option(["C", "C++", "C#"], "C#"),
                              pick_option(["C", "C++", "C#"], "C")) == (1, 2, 0))

check("resume slot", document_kind("Resume/CV", "resume") == "resume")
check("cover letter slot", document_kind("Attach", "cover_letter") == "cover_letter")
check("autofill shortcut isn't the resume slot", document_kind("Autofill from resume") is None)
check("unknown upload is left alone", document_kind("Additional files") is None)

section("1b. WHICH QUESTIONS GET A WRITTEN ANSWER, AND WHAT IS WRITTEN")
essay = essay_answers.is_essay
check("'Why do you want to work here?' is one", essay("Why do you want to work here?", "textarea", True))
check("an optional one in a big box is too", essay("Tell us about a project you're proud of", "textarea", False))
check("'Why Acme?' is one", essay("Why Acme?", "textarea", True))
check("an optional one-line box isn't", not essay("Describe your ideal team", "text", False))
check("a follow-up isn't", not essay("If yes, please explain why", "textarea", True))
check("sponsorship is never written", not essay("Please describe why you would require visa sponsorship", "textarea", True))
check("pay is never written", not essay("What are your salary expectations and why?", "textarea", True))
check("how you heard of the job isn't", not essay("How did you hear about us? Tell us more", "textarea", True))
check("a start date isn't", not essay("When are you available to start your internship?", "textarea", True))
check("a yes or no isn't", not essay("Are you interested in working onsite in Austin?", "text", True))
check("a yes or no that asks for more is", essay("Have you worked with FPGAs? If so, please describe the project.", "textarea", True))
check("'Anything else?' isn't", not essay("Is there anything else you would like us to know? Tell us here", "textarea", False))
check("a plain fact isn't", not essay("What full time job(s) are you applying for?", "textarea", True))
check("length follows the question", essay_answers.limits("Why us? (150 words max)")[:2] == (90, 150), essay_answers.limits("Why us? (150 words max)"))
check("and the box's size", essay_answers.limits("Why us?", max_chars=280)[1] <= 40)

sample = json.loads(PROFILE.read_text(encoding="utf-8"))
text, notes = essay_answers.write("Why do you want to work here?", sample, "Embedded Intern", "Acme", "We build embedded systems.")
print("  written:", text)
check("an answer is written from the profile", bool(text) and "weather station" in text and "help desk" in text, notes)
check("an invented award is cut", "hackathon" not in (text or "") and any("first place" in n or "won" in n for n in notes), notes)
check("a skill only the answer invented is cut", "Kubernetes" not in (text or ""), notes)
text, notes = essay_answers.write("What is your favorite color and why?", sample, "Embedded Intern", "Acme", "")
check("nothing to say means no answer", text is None and "nothing in your profile" in notes[0], (text, notes))
text, notes = essay_answers.write("Why do you want to work here?", sample, "Embedded Intern", "Acme", "", max_chars=90)
check("an answer is cut to fit a small box, at a sentence end", text is not None and len(text) <= 90 and text.endswith("."), text)
os.environ["HSBOT_CLAUDE"] = str(WORK / "no_such_claude")
text, notes = essay_answers.write("Why do you want to work here?", sample, "Embedded Intern", "Acme", "")
check("without Claude there is no answer, never a made-up one", text is None, (text, notes))
os.environ["HSBOT_CLAUDE"] = str(FAKE)
first = essay_answers.answer("job-9", "Why do you want to work here?", "Embedded Intern", "Acme", "We build embedded systems.")
again = essay_answers.answer("job-9", "Why do you want to work here?", "Embedded Intern", "Acme", "We build embedded systems.")
check("an answer is kept for its job and reused", first.method == "ai" and again.method == "saved" and again.text == first.text)
check("and saved where the student can read it", (WORK / "tailored" / "job-9-acme" / "written_answers.txt").read_text(encoding="utf-8").startswith("Why do you want"))

section("1c. THE LIST OF QUESTIONS IT COULDN'T ANSWER")
import unanswered  # noqa: E402

acme, beta = {"id": "a-1", "employer": "Acme Robotics"}, {"id": "b-1", "employer": "Beta"}
unanswered.note(acme, [{"label": "Are you related to any current Acme employees?*", "options": ["Yes", "No"]},
                       {"label": "Social Security Number", "options": []}], "empty")
unanswered.note(beta, [{"label": "Are you related to any current Beta employees?", "options": ["Yes", "No"]},
                       {"label": "Favorite tool?", "options": []}], "empty")
unanswered.note(beta, [{"label": "Are you related to any current Beta employees?", "options": []}], "empty")  # a second run
unanswered.note({"id": "c-1", "employer": "Gamma"}, [{"label": "Favorite tool?", "options": []}], "guessed")
rows = unanswered.ranked(answers_now())
check("the same question from two companies is one entry, counted once per posting",
      rows[0]["question"] == "Are you related to any current [company] employees?" and rows[0]["forms"] == 2, rows[:1])
check("with its choices", rows[0]["choices"] == ["Yes", "No"])
check("most common first, left-empty ahead of best judgement", [r["question"] for r in rows[:2]]
      == ["Are you related to any current [company] employees?", "Favorite tool?"], [r["question"] for r in rows])
check("and says what happened", unanswered.what_happened(rows[1]) == "left empty on 1, best judgement used on 1", unanswered.what_happened(rows[1]))
check("something never filled in isn't listed as waiting for an answer", not any("Social Security" in r["question"] for r in rows))
answered = answers_now() + [{"match": ["are you related to any current acme employees?"], "value": "No"},
                            {"match": ["are you related to any current beta employees?"], "value": "No"}]
check("a question leaves the list once the profile answers it", not any("related" in r["question"] for r in unanswered.ranked(answered)))
listed = unanswered.write_page(WORK / "list", rows)
check("the list is written as a page", listed.exists() and "[company]" in listed.read_text(encoding="utf-8"))
unanswered.path().unlink()

server, _ = fake_employer_sites.serve(EMPLOYER_PORT)
from playwright.sync_api import sync_playwright  # noqa: E402

asked: list[tuple[str, list[str] | None]] = []


def make_asker(replies: dict[str, str]):
    def ask(question: str, options: list[str] | None = None, many: bool = False):
        asked.append((question, options))
        for phrase, reply in replies.items():
            if phrase in question.lower():
                return reply
        return None

    return ask


try:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1300, "height": 900})
        said: list[str] = []

        section("2. A GREENHOUSE FORM, NOBODY AT THE KEYBOARD")
        site = EmployerSite(page, answers_now(), ask=None, say=said.append)
        state, note = site.open(f"{EMPLOYER}/gh/acme/jobs/1")
        check("form found", state == "form", (state, note))
        check("recognised as Greenhouse", site.flavor == "greenhouse", site.flavor)
        fields = {f["label"]: f for f in site.scan()}
        check("required dropdown read as required", fields["Country"]["required"] and fields["Country"]["kind"] == "combobox")
        check("optional field read as optional", not fields["LinkedIn Profile"]["required"])
        check("checkbox question read with its choices",
              fields["Which locations would you consider?"]["options"] == ["Seattle, Washington", "Austin, Texas", "Remote"])
        check("resume slot named by its group, not 'Attach'", fields["Resume/CV"]["kind"] == "file" and fields["Resume/CV"]["required"])
        check("the hidden phone-country search isn't a question", not any("Search" in label for label in fields))
        report = site.fill(documents)
        print("  filled:", "; ".join(report.filled))
        check("resume attached", "resume" in report.attached)
        check("no cover letter for an optional slot", "cover_letter" not in report.attached)
        check("the one unanswered required question is reported",
              report.missing == ["How did you hear about this opportunity?"], report.missing)
        values = page.evaluate("() => Object.fromEntries([...document.querySelectorAll('#application-form input[name]')].map(i => [i.name, i.value]))")
        check("name and email from the profile", (values["first_name"], values["last_name"], values["email"]) == ("Jane", "Doe", "jane.doe@example.edu"))
        check("country picked from a long list by typing", values["country"] == "United States", values["country"])
        check("school found by searching", values["school--0"] == "University of Springfield", values["school--0"])
        check("work authorization from the saved answer", values["question_1"] == "Yes")
        check("sponsorship from the saved answer", values["question_2"] == "No")
        check("Social Security number never filled, even with one saved", values["question_6"] == "")
        check("optional gender answered from the student's saved words", values["gender"] == "Decline To Self Identify", values["gender"])
        boxes = page.evaluate("() => [...document.querySelectorAll('input[name=\"question_4[]\"]')].map(i => i.checked)")
        check("both saved locations ticked", boxes == [False, True, True], boxes)
        before = len(fake_employer_sites.SUBMISSIONS)
        check("nothing has been sent by filling", before == 0)

        section("3. THE SAME FORM WITH THE STUDENT ANSWERING")
        asked.clear()
        site = EmployerSite(page, answers_now(), ask=make_asker({"how did you hear": "Handshake"}), say=said.append)
        site.open(f"{EMPLOYER}/gh/acme/jobs/1")
        report = site.fill(documents)
        check("only the unanswered required question was asked", [q for q, _ in asked] == ["How did you hear about this opportunity?"], asked)
        check("its choices were offered", asked and asked[0][1] == ["Company website", "Handshake", "LinkedIn", "Referral"])
        check("nothing left empty", report.missing == [], report.missing)
        check("the answer was learned", site.learned == [{"match": ["how did you hear about this opportunity?"], "value": "Handshake", "sensitive": False}], site.learned)
        check("learned answers are saved to the profile", remember_answers(PROFILE, site.learned) == 1)
        check("saving twice adds nothing", remember_answers(PROFILE, site.learned) == 0)
        status, note = site.submit()
        check("sent and confirmed", status == "applied", (status, note))
        sent = last("/gh/acme")
        check("the form the employer received is right",
              sent["fields"]["first_name"] == ["Jane"] and sent["fields"]["question_5"] == ["Handshake"]
              and sent["fields"]["question_4[]"] == ["1", "2"] and sent["fields"]["country"] == ["United States"], sent["fields"])
        check("with the resume attached", sent["files"].get("resume") == RESUME.name, sent["files"])
        check("and no cover letter", "cover_letter" not in sent["files"])
        check("'Thank you for your interest' in a posting isn't a confirmation", status == "applied" and "confirmed" in note)

        section("4. A LEVER FORM")
        asked.clear()
        site = EmployerSite(page, answers_now(), ask=make_asker({"keep my application": "Yes"}), say=said.append)
        state, note = site.open(f"{EMPLOYER}/lever/acme/{JOB}")
        check("the posting's Apply link was followed to the form", state == "form" and page.url.endswith("/apply"), (state, page.url))
        check("recognised as Lever", site.flavor == "lever", site.flavor)
        fields = {f["label"]: f for f in site.scan()}
        check("radio question read from its card", fields["Are you authorized to work in the United States?"]["options"] == ["Yes", "No"])
        check("required mark read", fields["Full name"]["required"] and not fields["Phone"]["required"])
        report = site.fill(documents)
        print("  filled:", "; ".join(report.filled))
        check("nothing left empty", report.missing == [], report.missing)
        check("the consent box was asked about, not assumed", len(asked) == 1 and "keep my application" in asked[0][0], asked)
        status, note = site.submit()
        check("sent and confirmed", status == "applied", (status, note))
        sent = last("/lever/acme")
        check("the name the site read from the resume was corrected", sent["fields"]["name"] == ["Jane Doe"], sent["fields"]["name"])
        check("radio, select and consent answered",
              sent["fields"]["cards[a][field0]"] == ["Yes"] and sent["fields"]["cards[b][field0]"] == ["A United States Citizen"]
              and sent["fields"]["cards[c][field0]"] == ["Yes"] and sent["fields"]["consent[store]"] == ["true"], sent["fields"])
        check("optional questions with no saved answer left blank",
              sent["fields"]["cards[a][field2]"] == [""] and "cards[c][field1]" not in sent["fields"] and sent["fields"]["org"] == [""])
        check("optional gender from the saved answer", sent["fields"]["eeo[gender]"] == ["Decline to self-identify"])
        check("resume attached", sent["files"].get("resume") == RESUME.name)
        check("with no cover letter to give, the box that takes one is left empty", sent["fields"]["comments"] == [""], sent["fields"]["comments"])
        remember_answers(PROFILE, site.learned)

        # Lever has nowhere to attach a cover letter, only a box that says to add one.
        wanted: list[bool] = []
        site = EmployerSite(page, answers_now(), ask=None, say=said.append)
        site.open(f"{EMPLOYER}/lever/acme/{JOB}")
        report = site.fill(documents, letter=lambda required: wanted.append(required) or TYPED_LETTER)
        box = page.evaluate("() => document.querySelector('textarea[name=comments]').value")
        check("a cover letter is typed into the box that asks for one", box == TYPED_LETTER, box)
        check("which the form didn't require", wanted and not any(wanted), wanted)
        check("and the report says so", any("your cover letter" in line for line in report.filled), report.filled)
        check("the follow-up box beside it isn't given the letter",
              page.evaluate("() => document.querySelector('textarea[name=\"cards[a][field2]\"]').value") == "")
        wanted.clear()
        site = EmployerSite(page, answers_now(), ask=None, say=said.append)
        site.open(f"{EMPLOYER}/gh/acme/jobs/1")
        letter_file = WORK / "Some_Cover_Letter.pdf"
        letter_file.write_bytes(b"%PDF-1.4 test letter")
        report = site.fill(lambda kind, required: {"resume": RESUME, "cover_letter": letter_file}.get(kind),
                           letter=lambda required: wanted.append(required) or TYPED_LETTER)
        check("where a form has a slot for it, the letter is attached as a file",
              "cover_letter" in report.attached and any(line == f"cover letter: {letter_file.name}" for line in report.filled), report.filled)
        check("and not typed anywhere as well", wanted == [] and not any("typed in" in line for line in report.filled), wanted)

        section("5. AN ASHBY FORM")
        asked.clear()
        site = EmployerSite(page, answers_now(), ask=make_asker({"related to any current": "No"}), say=said.append)
        state, note = site.open(f"{EMPLOYER}/ashby/acme/{JOB}/application")
        check("form found without a <form>", state == "form" and site.flavor == "ashby", (state, site.flavor))
        fields = {f["label"]: f for f in site.scan()}
        yes_no = fields["Are you legally authorized to work in the United States?"]
        check("Yes/No buttons read as one required question", yes_no["kind"] == "buttons" and yes_no["required"] and yes_no["options"] == ["Yes", "No"], yes_no)
        check("optional essay read as optional", not fields["Why do you want to work at Acme?"]["required"])
        check("every section of the form is read, not only the one with the most boxes",
              {"Name", "Email", "Resume", "Gender", "Race"} <= set(fields), sorted(fields))
        report = site.fill(documents)
        print("  filled:", "; ".join(report.filled))
        check("nothing left empty", report.missing == [], report.missing)
        check("only the family question was asked", [q for q, _ in asked] == ["Are you related to any current Acme employees?"], asked)
        status, note = site.submit()
        check("confirmed though the address never changed", status == "applied", (status, note))
        sent = last("/ashby")
        check("answers received",
              sent["fields"]["_systemfield_name"] == ["Jane Doe"] and sent["fields"]["f-legal"] == ["Jane Doe"]
              and sent["fields"]["f-degree"] == ["Bachelor's"] and sent["fields"]["f-auth"] == ["yes"]
              and sent["fields"]["f-family"] == ["no"] and sent["fields"]["f-why"] == [""], sent["fields"])
        check("resume went to the resume slot, not the autofill shortcut", sent["files"] == {"resume": RESUME.name}, sent["files"])
        remember_answers(PROFILE, site.learned)

        section("6. PAGES IT MUST LEAVE ALONE, AND ODD ONES")
        site = EmployerSite(page, answers_now(), ask=None, say=said.append)
        check("a closed posting", site.open(f"{EMPLOYER}/closed")[0] == "closed")
        check("a sign-in page", site.open(f"{EMPLOYER}/signin")[0] == "needs_account")
        state, note = site.open("https://acme.wd5.myworkdayjobs.com/careers/job/1")
        check("an account site isn't even opened", state == "needs_account" and "Workday" in note, note)
        check("a page with no form", site.open(f"{EMPLOYER}/nothing-here")[0] == "no_form")
        check("a careers page with only search and job-alert boxes isn't a form", site.open(f"{EMPLOYER}/careers")[0] == "no_form")
        state, note = site.open(f"{EMPLOYER}/portal/job")
        check("an Apply button that leads to a sign-in page", state == "needs_account" and page.url.endswith("/portal/login"), (state, note, page.url))
        state, _ = site.open(f"{EMPLOYER}/embedded")
        check("a form inside a frame on the company's page", state == "form" and site.flavor == "greenhouse", (state, site.flavor))
        check("and it can be read there", len(site.scan()) > 10)

        state, _ = site.open(f"{EMPLOYER}/plain/apply")
        check("a small company's own form is found", state == "form" and site.flavor == "", (state, site.flavor))
        labels = [f["label"] for f in site.scan()]
        check("the site's search box isn't a question", labels == ["First name", "Last name", "Email address", "Resume"], labels)
        report = site.fill(documents)
        check("it fills from the same answers", report.missing == [] and "resume" in report.attached, report.missing)
        check("and sends", site.submit()[0] == "applied")

        site.open(f"{EMPLOYER}/essay/apply")
        report = site.fill(documents)
        check("with essay writing off, a required essay is left empty", report.missing == ["Why do you want to work here?"], report.missing)

        wrote: list[str] = []

        def essays(question: str, max_chars: int, single_line: bool) -> str | None:
            wrote.append(question)
            return essay_answers.answer("essay-test", question, "Robotics Intern", "Tiny Robotics", "We build robots.",
                                        max_chars=max_chars, single_line=single_line).text or None

        site.open(f"{EMPLOYER}/essay/apply")
        report = site.fill(documents, essays)
        check("with it on, the essay is written into the form", report.missing == [] and wrote == ["Why do you want to work here?"], (report.missing, wrote))
        check("and reported so the student can read it", len(report.written) == 1 and "weather station" in report.written[0][1], report.written)
        typed_in = page.locator("#why").input_value()
        check("the form holds only the checked sentences", "weather station" in typed_in and "hackathon" not in typed_in and "Kubernetes" not in typed_in, typed_in)
        wrote.clear()
        site.open(f"{EMPLOYER}/gh/acme/jobs/1")
        site.fill(documents, essays)
        check("questions that aren't essays never reach the writer", wrote == [], wrote)

        sent_to_claude: list[str] = []
        turned_down: list[str] = []

        def settle(questions: list[dict]) -> dict:
            sent_to_claude.extend(q["question"] for q in questions)
            found, notes = form_answers.work_out(questions, json.loads(PROFILE.read_text(encoding="utf-8")),
                                                 "Robotics Intern", "Tiny Robotics", "Handshake")
            turned_down.extend(notes)
            return found

        left = ["Have you ever been convicted of a felony?", "GPA in your major courses only, if you know it",
                "Are you related to a current employee?", "Do you have a disability?"]
        site.open(f"{EMPLOYER}/quiz/apply")
        report = site.fill(documents, None, settle)
        print("  worked out:", report.worked)
        check("a profile fact needs no working out",
              "Expected graduation year" not in sent_to_claude and page.locator("#gy").input_value() == "2028", sent_to_claude)
        check("leftover questions the profile settles are filled in",
              {q for q, _, _ in report.worked} == {"How did you hear about us?", "Master's GPA",
                                                   "Graduation year (four digits), as it will appear on your transcript",
                                                   "Would you need an export license under the circumstances described below?"},
              report.worked)
        check("a question with choices is never taken for an essay, however it is worded",
              page.locator("#exp").input_value() == "No", page.locator("#exp").input_value())
        check("with the right values",
              (page.locator("#hear").input_value(), page.locator("#mgpa").input_value(), page.locator("#gy2").input_value())
              == ("Handshake", "N/A", "2028"))
        check("each with where it came from", all(because for _, _, because in report.worked))
        check("a guess on a legal question, an invented figure and a non-choice are all left empty", report.missing == left, report.missing)
        check("and the student is told why",
              any("figure" in n for n in turned_down) and any("choices" in n for n in turned_down), turned_down)
        asked.clear()
        site = EmployerSite(page, answers_now(), say=said.append,
                            ask=make_asker({"felony": "No", "major courses": "3.5", "related": "No", "disability": "No"}))
        site.open(f"{EMPLOYER}/quiz/apply")
        report = site.fill(documents, None, settle)
        check("only what couldn't be worked out is asked", sorted(q for q, _ in asked) == sorted(left), asked)
        check("and then nothing is left", report.missing == [], report.missing)
        check("what was worked out is not saved as the student's own words",
              not any("hear" in a["match"][0] for a in site.learned), site.learned)
        why_empty: list[str] = []

        def judge(questions: list[dict]) -> dict:
            found, notes = form_answers.work_out(questions, json.loads(PROFILE.read_text(encoding="utf-8")), "Robotics Intern",
                                                 "Tiny Robotics", "Handshake", best_guess=True, job_text="We build robots.")
            why_empty.extend(notes)
            return found

        asked.clear()
        site = EmployerSite(page, answers_now(), ask=make_asker({}), say=said.append)  # asked, and never answers
        site.open(f"{EMPLOYER}/quiz/apply")
        report = site.fill(documents, None, settle, judge)
        print("  best judgement:", report.guessed)
        check("the student is asked first", len(asked) == 4, asked)
        check("a question they leave gets its best judgement",
              ("Are you related to a current employee?", "No") in [(q, v) for q, v, _ in report.guessed], report.guessed)
        check("a personal question they've never answered gets the form's own 'prefer not to say'",
              ("Do you have a disability?", "I prefer not to say") in [(q, v) for q, v, _ in report.guessed], report.guessed)
        check("a legal question with no such choice is still not guessed, nor is a figure made up",
              report.missing == left[:2], report.missing)
        check("and the student is told", any("isn't guessed" in n for n in why_empty), why_empty)
        check("best judgement is not saved as the student's own words", site.learned == [], site.learned)
        site = EmployerSite(page, answers_now(), ask=None, say=said.append)

        site.open(f"{EMPLOYER}/wiping/apply")
        report = site.fill(documents)
        check("an answer the site wiped is given again",
              report.missing == [] and page.locator("#fn").input_value() == "Jane", (report.missing, page.locator("#fn").input_value()))

        site.open(f"{EMPLOYER}/rejecting/apply")
        site.fill(documents)
        status, note = site.submit()
        check("a form turned down over an answer the site emptied is repaired and sent again",
              status == "applied" and last("/rejecting")["fields"]["first"] == ["Jane"], (status, note))
        site.open(f"{EMPLOYER}/refusing/apply")
        site.fill(documents)
        sent_before = len(fake_employer_sites.SUBMISSIONS)
        status, note = site.submit()
        check("a form the site keeps turning down is reported as not sent, with what the site said",
              status == "needs_manual" and "Reference code" in note and len(fake_employer_sites.SUBMISSIONS) == sent_before, (status, note))

        other = browser.new_page()
        gone = EmployerSite(other, answers_now(), ask=None, say=said.append)
        gone.open(f"{EMPLOYER}/plain/apply")
        other.close()
        check("a closed tab is never mistaken for a finished form", gone.missing_required() != [] and not gone.alive())
        check("and nothing more is tried in it", gone.open(f"{EMPLOYER}/plain/apply")[0] == "stopped")

        sent_before = len(fake_employer_sites.SUBMISSIONS)
        site.open(f"{EMPLOYER}/guarded/apply")
        site.fill(documents)
        status, note = site.submit()
        check("a security check with nobody there stops it", status == "needs_manual" and "security check" in note, (status, note))
        check("and nothing was sent", len(fake_employer_sites.SUBMISSIONS) == sent_before)
        patient = EmployerSite(page, answers_now(), ask=None, person_present=True, patience=20, say=said.append)
        patient.open(f"{EMPLOYER}/guarded/apply")
        patient.fill(documents)
        said.clear()
        status, note = patient.submit()
        check("with the student there, it waits for them to pass it", status == "applied", (status, note))
        check("and says what the site wants", any("security check" in line for line in said), said)
        browser.close()

    section("7. WHERE A HANDSHAKE POSTING'S 'APPLY EXTERNALLY' LEADS")
    fake_handshake.EXTERNAL_APPLY_URL = f"{EMPLOYER}/gh/acme/jobs/1"
    handshake_server, _ = fake_handshake.serve(HANDSHAKE_PORT)
    selectors = {k: v for k, v in json.loads((ROOT / "selectors.json").read_text(encoding="utf-8")).items() if isinstance(v, list)}
    with HandshakeSession({"handshake_base_url": HANDSHAKE, "headless": True}, selectors, WORK / "probe_profile") as session:
        session.ensure_logged_in(timeout_seconds=20)
        address, why = session.external_apply_url("1002")
        check("the employer's address is read from the posting's own data", address == f"{EMPLOYER}/gh/acme/jobs/1" and "data" in why, (address, why))
        check("not another posting's address from the same data", "someone-else" not in address)
        check("without pressing Apply externally", not session.page.evaluate("() => !!window.pressedApply"))
        check("or opening a tab", len(session._context.pages) == 1, len(session._context.pages))
        fake_handshake.SERVE_JOB_DATA = False
        address, why = session.external_apply_url("1002")
        check("with no data on the page, pressing the button finds it", address == f"{EMPLOYER}/gh/acme/jobs/1", (address, why))
        check("and the tab it opened was closed again", len(session._context.pages) == 1, len(session._context.pages))
        fake_handshake.SERVE_JOB_DATA = True
        check("a posting the employer took down is reported closed", session.external_apply_url("4040") == ("", "closed"))
        address, why = session.external_apply_url("1001")
        check("a posting that applies on Handshake has none", address == "" and "Handshake" in why, (address, why))
        check("and its Apply button wasn't pressed", session._first_visible("dialog", timeout=500) is None)

    section("8. THE WHOLE RUN FROM THE COMMAND LINE")
    data, listing = WORK / "data", WORK / "list"
    data.mkdir(parents=True, exist_ok=True)
    listing.mkdir(parents=True, exist_ok=True)
    (data / "elsewhere_ranked.json").write_text(json.dumps([
        {"id": "ab-acme-1", "title": "Embedded Intern", "employer": "Acme", "location": "", "url": f"{EMPLOYER}/ashby/acme/{JOB}/application", "description": "We build embedded systems.", "score": 95},
        {"id": "gh-acme-1", "title": "Hardware Intern", "employer": "Acme", "location": "Remote", "url": f"{EMPLOYER}/gh/acme/jobs/1", "description": "Embedded work.", "score": 90},
        {"id": "wd-big-1", "title": "Intern", "employer": "BigCo", "location": "", "url": "https://big.wd1.myworkdayjobs.com/x/job/1", "description": "", "score": 80},
        {"id": "gone-1", "title": "Old Intern", "employer": "Gone", "location": "", "url": f"{EMPLOYER}/closed", "description": "", "score": 70},
        {"id": "essay-1", "title": "Essay Intern", "employer": "Wordy", "location": "", "url": f"{EMPLOYER}/essay/apply", "description": "", "score": 60},
        {"id": "plain-1", "title": "Robotics Intern", "employer": "Tiny Robotics", "location": "", "url": f"{EMPLOYER}/plain/apply", "description": "", "score": 50},
        {"id": f"lv-acme-{JOB}", "title": "Firmware Intern", "employer": "Acme", "location": "", "url": f"{EMPLOYER}/lever/acme/{JOB}", "description": "Firmware for embedded devices.", "score": 40},
    ]), encoding="utf-8")
    with (listing / "all_ranked.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "score", "title", "employer", "location", "pay", "deadline", "kind of job", "matches", "notes", "more at this company", "url"])
        writer.writerow([1, 88.0, "Machine Learning Intern", "Initech", "Remote", "", "", "", "", "", 0, f"{HANDSHAKE}/job-search/1002"])
    common = ["--resume", str(RESUME), "--headless", "--base-url", HANDSHAKE]
    sys.stdin = io.StringIO()  # nobody at the keyboard (the null device counts as a keyboard on Windows)
    typed = iter(["yes"])
    real_input = builtins.input
    builtins.input = lambda prompt="": next(typed)
    config = employer_apply.load_config
    employer_apply.load_config = lambda args: dict(config(args), delay_between_employer_applications_seconds=[0, 0])
    try:
        sent_before = len(fake_employer_sites.SUBMISSIONS)
        tailored = WORK / "tailored"
        code = employer_apply.main(["--list", "elsewhere", "--dry-run", "--usual-resume", "--no-cover-letters"] + common)
        check("with --usual-resume and --no-cover-letters, no resume or letter is made",
              code == 0 and not list(tailored.glob("*/*_Resume.pdf")) and not list(tailored.glob("*/*_Cover_Letter.pdf")),
              [str(p) for p in tailored.glob("*/*.pdf")])
        code = employer_apply.main(["--list", "elsewhere", "--dry-run"] + common)
        check("practice run finishes", code == 0)
        check("a practice run sends nothing", len(fake_employer_sites.SUBMISSIONS) == sent_before)
        check("and marks nothing applied", applied_myself.ids() == set())
        check("left to itself, it tailors a resume to each posting",
              all((tailored / folder / "Jane_Doe_Resume.pdf").exists() for folder in ("ab-acme-1-acme", "gh-acme-1-acme", f"lv-acme-{JOB}-acme")),
              [str(p) for p in tailored.glob("*/*.pdf")])
        check("and writes a cover letter wherever the form takes one, required or not",
              all((tailored / folder / "Jane_Doe_Cover_Letter.pdf").exists() for folder in ("gh-acme-1-acme", f"lv-acme-{JOB}-acme")))
        check("but none for a form with nowhere to put it", not (tailored / "ab-acme-1-acme" / "Jane_Doe_Cover_Letter.pdf").exists())
        check("a posting with no description saved gets neither", not list(tailored.glob("plain-1-*/*.pdf")))

        # The Ashby form is first on the list and has an optional "Why do you want to work at Acme?".
        code = employer_apply.main(["--list", "elsewhere", "--auto-submit", "--hold-essays", "--top", "1"] + common)
        log = json.loads((data / "employer_applications.json").read_text(encoding="utf-8"))
        check("with --hold-essays, automatic mode keeps back a form with a written answer nobody has read",
              code == 0 and log["ab-acme-1"]["status"] == "needs_manual" and "written answer" in log["ab-acme-1"]["note"], log.get("ab-acme-1"))
        check("and sends nothing", len(fake_employer_sites.SUBMISSIONS) == sent_before)
        typed = iter(["yes"])
        code = employer_apply.main(["--list", "elsewhere", "--auto-submit", "--top", "1"] + common)
        log = json.loads((data / "employer_applications.json").read_text(encoding="utf-8"))
        check("without it, automatic mode sends a form with a written answer, and a held one is picked up again",
              code == 0 and log["ab-acme-1"]["status"] == "applied", log.get("ab-acme-1"))
        why = last("/ashby")["fields"]["f-why"][0]
        check("the employer received the checked answer, without the invented parts",
              "weather station" in why and "hackathon" not in why and "Kubernetes" not in why, why)
        essays_page = (listing / "Essays.html").read_text(encoding="utf-8") if (listing / "Essays.html").exists() else ""
        check("every essay is gathered on one page in the results folder, under its job and what happened to it",
              "Why do you want to work at Acme?" in essays_page and "weather station" in essays_page
              and "Embedded Intern" in essays_page and ">applied<" not in essays_page and "applied" in essays_page, essays_page[-600:])
        check("the answer is kept with that job's documents",
              "weather station" in (WORK / "tailored" / "ab-acme-1-acme" / "written_answers.txt").read_text(encoding="utf-8"))

        typed = iter(["yes"])
        code = employer_apply.main(["--list", "both", "--auto-submit", "--no-essays"] + common)
        check("automatic run finishes", code == 0)
        log = json.loads((data / "employer_applications.json").read_text(encoding="utf-8"))
        statuses = {key: entry["status"] for key, entry in log.items()}
        print("  ", statuses)
        check("the Greenhouse form was sent", statuses.get("gh-acme-1") == "applied")
        check("the Lever form was sent", statuses.get(f"lv-acme-{JOB}") == "applied")
        check("the Handshake posting was followed to its employer and sent", statuses.get("1002") == "applied")
        check("its employer link was kept", json.loads((data / "employer_links.json").read_text(encoding="utf-8")) == {"1002": f"{EMPLOYER}/gh/acme/jobs/1"})
        by_list, from_handshake = [s for s in fake_employer_sites.SUBMISSIONS if s["path"].startswith("/gh/acme")][-2:]
        check("the employer received the resume tailored to that job, with its cover letter",
              by_list["files"] == {"resume": "Jane_Doe_Resume.pdf", "cover_letter": "Jane_Doe_Cover_Letter.pdf"}, by_list["files"])
        check("a posting with no description saved went out with the usual resume and no letter",
              from_handshake["files"] == {"resume": RESUME.name}, from_handshake["files"])
        typed_in = last("/lever/acme")["fields"]["comments"][0]
        check("on a form with only a box for it, the letter written for that job was typed in",
              typed_in.startswith("Dear Hiring Team,") and typed_in.rstrip().endswith("Jane Doe") and "weather station" in typed_in, typed_in[:200])
        check("without the parts Claude invented", "hackathon" not in typed_in and "Kubernetes" not in typed_in, typed_in)
        check("Workday left for the student", statuses.get("wd-big-1") == "needs_account")
        check("the closed posting noted", statuses.get("gone-1") == "closed")
        check("with --no-essays the essay form is left for the student", statuses.get("essay-1") == "needs_manual" and "Why do you want" in log["essay-1"]["note"], log.get("essay-1"))
        check("an unfamiliar form isn't sent unchecked", statuses.get("plain-1") == "needs_manual" and "hasn't seen" in log["plain-1"]["note"], log.get("plain-1"))
        check("applied ones are marked like the Applied button", applied_myself.ids() == {"ab-acme-1", "gh-acme-1", f"lv-acme-{JOB}", "1002"}, applied_myself.ids())
        check("the record keeps questions, not answers", "jane.doe@example.edu" not in json.dumps(log) and "Email" in log["gh-acme-1"]["answered"])
        rows = list(csv.DictReader((listing / "employer_site_results.csv").open(encoding="utf-8")))
        check("results spreadsheet lists every posting with a link", len(rows) == 8 and all(r["where to apply"].startswith("http") for r in rows), len(rows))

        open_questions = unanswered.ranked(answers_now())
        check("the question a form was left over is on the list of unanswered ones",
              any(r["question"] == "Why do you want to work here?" and r["employers"] == ["Wordy"] for r in open_questions), open_questions)
        check("answered questions from the forms that were sent are not", not any("authorized" in r["question"].lower() for r in open_questions))
        check("the list can be shown on its own", employer_apply.main(["--questions"]) == 0
              and (listing / "Questions it couldn't answer.html").exists())

        sent_before = len(fake_employer_sites.SUBMISSIONS)
        typed = iter(["yes"])
        code = employer_apply.main(["--list", "both", "--auto-submit", "--no-essays"] + common)
        check("a second run finishes", code == 0)
        check("and never applies to the same posting twice", len(fake_employer_sites.SUBMISSIONS) == sent_before)
        check("a form left for want of an answer isn't reopened until there are new answers",
              json.loads((data / "employer_applications.json").read_text(encoding="utf-8"))["essay-1"]["when"] == log["essay-1"]["when"])
    finally:
        builtins.input = real_input
        employer_apply.load_config = config
        handshake_server.shutdown()
finally:
    server.shutdown()
    shutil.rmtree(WORK, ignore_errors=True)

print("\n" + "=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED:")
    for name in failures:
        print(f"  - {name}")
    sys.exit(1)
print("ALL EMPLOYER-SITE CHECKS PASSED")
print("=" * 70)
