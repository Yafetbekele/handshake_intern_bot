"""Working out a form's leftover questions from the student's profile.

After the saved answers have been used, an application usually still has
questions the profile plainly settles, only in other words: "Degree" with the
form's own list of degrees, "Will you require sponsorship from Acme?" when
that was answered for another company, "How did you hear about us?". Rather
than ask the student each of these, one Claude Code request on their plan
reads the profile and answers what it settles.

What comes back is not trusted as is. An answer is dropped unless
    * it is one of the form's own choices, where the form gives choices;
    * every figure in a typed answer is a figure in the profile;
    * for a personal or legal question (sponsorship, citizenship, clearance,
      pay, criminal record, gender and so on), the profile actually says
      something on that subject. Claude is told not to guess, and this makes
      sure a guess on one of these can't get through.
A question that isn't settled is left for the student, as before. Birth
dates, Social Security numbers and the like are never put to Claude at all.

Answers worked out here are used for the run and shown to the student; they
are not saved to the profile, because the student didn't say them.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

import tailor
from employer_sites import pick_option
from handshake import NEVER_FILL

SYSTEM_PROMPT = """You fill in a student's internship application from their own profile.

Rules:
- Answer a question only when the student's profile, the answers they have given before ("application_answers"), or the facts given about this application settle it. Settled includes: a question one of their earlier answers already covers, asked by a different company or in different words; whether they have worked for this employer, when the profile lists their jobs; a degree, major, GPA or graduation date the profile states, put in the form's own terms; "not applicable" for a degree they aren't pursuing; their country, from their address.
- When the profile doesn't settle it, the answer is null. Never guess, and never choose an answer because it would look better to the employer.
- Read each question for what it actually asks. "Would anything prevent you from working on site?" is the opposite of "Are you willing to work on site?".
- For a question with choices, the answer must be one of the choices, copied exactly. For "pick": "any", give a list of choices.
- Where the student has said that any choice is fine for a question ("any location", "select anything"), take the choice that says so ("No preference", "Any", "Other") if there is one; otherwise the choice nearest what they asked for, or failing that the first one listed.
- Keep a typed answer as short as the box needs: a name, a date, a number, a city, "N/A". No sentences.
- Never give a Social Security number, a bank or card number, a password, a date of birth or a licence number.
- Reply with one JSON object only. No commentary, no code fences."""

# For questions the student was asked and didn't answer, when they've said to use
# best judgement rather than leave a blank.
GUESS_PROMPT = """You fill in a student's internship application from their own profile, using your best judgement on questions they left unanswered.

The student was asked the questions below and gave no answer. They have told this tool to use its best judgement rather than leave them blank. So:
- Give the answer most likely to be TRUE of this student, judged from their profile, the answers they have given before ("application_answers") and the job posting. For "do you meet the qualifications", compare what the posting asks for with what the profile shows, honestly.
- Where nothing bears on a question, take the most neutral choice there is ("No preference", "None", "Other", "N/A"), or type "N/A".
- Never choose an answer because it would look better to the employer: a wrong answer on an application can cost the student the offer later.
- Read each question for what it actually asks. "Would anything prevent you from working on site?" is the opposite of "Are you willing to work on site?".
- For a question with choices, the answer must be one of the choices, copied exactly. For "pick": "any", give a list of choices.
- Keep a typed answer as short as the box needs. No invented figures: a number must come from the profile.
- Never give a Social Security number, a bank or card number, a password, a date of birth or a licence number.
- Answer every question you can; null only when no answer is defensible.
- Reply with one JSON object only. No commentary, no code fences."""

# A form's own way of not answering a personal question.
DECLINE = re.compile(
    r"prefer not|decline|rather not|do not wish|don.t wish|not wish to|choose not to|"
    r"not to (say|disclose|answer|specify|identify|self)",
    re.IGNORECASE,
)

# Never put to Claude, whatever the profile holds.
NOT_FOR_CLAUDE = re.compile(r"date of birth|birth ?date|birthday|\bdob\b|\bage\b|signature", re.IGNORECASE)

# Personal and legal subjects. A question on one of these is only answered when
# the profile itself mentions that subject (any word of the same group).
SUBJECTS = [
    ("sponsor", "visa", "work permit", "h1b", "h-1b"),
    ("citizen", "green card", "permanent resident", "u.s. person", "us person", "export control", "itar"),
    ("authorized to work", "work authorization", "legally authorized", "eligible to work", "right to work"),
    ("clearance", "top secret"),
    ("salary", "compensation", "wage", "pay rate", "hourly rate", "desired pay"),
    ("felon", "criminal", "convict", "background check", "drug test", "misdemeanor"),
    ("gender", "pronoun", "transgender"),
    ("race", "ethnic", "hispanic", "latino"),
    ("veteran", "military", "service member", "armed forces"),
    ("disab",),
    ("marital", "married"),
    ("religio",),
    ("pregnan",),
    ("sexual orientation", "lgbt"),
    # Signing something in the student's name: only once they've said it's fine to.
    ("certify", "acknowledg", "consent", "attest", "i agree", "privacy policy", "privacy notice", "terms and conditions",
     "terms & conditions"),
]


def _mentions(text: str, term: str) -> bool:
    return re.search(rf"(?<![a-z]){re.escape(term)}", text) is not None


def grounded(question: str, profile_text: str) -> bool:
    """False when a question is on a personal or legal subject the profile says nothing about."""
    asked = question.lower()
    for group in SUBJECTS:
        if any(_mentions(asked, term) for term in group) and not any(_mentions(profile_text, term) for term in group):
            return False
    return True


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def figures_known(answer: str, profile_raw: str, question: str) -> bool:
    """Every figure in a typed answer has to be the student's: a GPA, a year, a phone number."""
    known = {n.replace(",", "") for n in tailor.NUMBER_RE.findall(f"{profile_raw} {question}")}
    for number in tailor.NUMBER_RE.findall(answer):
        plain = number.replace(",", "")
        if plain in known:
            continue
        if plain.isdigit() and int(plain) <= 31:
            continue  # a month or a day, from writing a date another way
        return False
    run = _digits(answer)
    if len(run) >= 7 and run not in _digits(profile_raw):
        return False  # a long number (phone, ID) has to appear in the profile whole
    return True


def _prompt(questions: list[dict[str, Any]], profile: dict[str, Any], title: str, employer: str, found_on: str,
            job_text: str = "") -> str:
    posting = f"JOB POSTING:\n{job_text[:4000]}\n\n" if job_text.strip() else ""
    return (
        f"APPLICATION: {title} at {employer}. The student found this posting on {found_on}. "
        f"Today is {date.today().strftime('%B %d, %Y')}.\n\n"
        f"{posting}"
        "STUDENT PROFILE (the only source of facts about the student):\n"
        f"{json.dumps(profile, ensure_ascii=False, indent=1)}\n\n"
        "QUESTIONS STILL EMPTY ON THE FORM:\n"
        f"{json.dumps(questions, ensure_ascii=False, indent=1)}\n\n"
        'Reply with JSON shaped exactly like:\n{"answers": [{"id": 1, "answer": "..." or ["...", "..."] or null, '
        '"because": "the profile fact or earlier answer this comes from, in a few words"}]}'
    )


def work_out(
    questions: list[dict[str, Any]],
    profile: dict[str, Any],
    title: str,
    employer: str,
    found_on: str,
    *,
    best_guess: bool = False,
    job_text: str = "",
    exe: str | None = None,
) -> tuple[dict[int, tuple[Any, str]], list[str]]:
    """Returns ({question id: (answer, where it comes from)}, notes on what was turned down).

    Normally only what the profile settles is answered. With `best_guess`,
    for questions the student was asked and left, the likeliest true answer
    is given instead of a blank. Even then a personal or legal question the
    profile says nothing about is not guessed: the form's own "prefer not to
    say" is taken if it has one, and otherwise the question stays empty.
    """
    profile_raw = json.dumps(profile, ensure_ascii=False)
    profile_text = profile_raw.lower()
    notes: list[str] = []
    asked: dict[int, dict[str, Any]] = {}
    settled: dict[int, tuple[Any, str]] = {}
    for question in questions:
        text = str(question.get("question") or "")
        if NEVER_FILL.search(text) or NOT_FOR_CLAUDE.search(text):
            continue
        if not grounded(text, profile_text):
            # Nothing in the profile to answer it from, so don't invite a guess.
            decline = next((c for c in question.get("choices") or [] if DECLINE.search(str(c))), None)
            if best_guess and decline is not None and question.get("pick") != "any":
                settled[int(question["id"])] = (decline, "a personal question you haven't answered, so the form's own way of not saying")
            elif best_guess:
                notes.append(f"\"{text[:50]}\": a personal or legal question your profile doesn't answer, so it isn't guessed")
            continue
        asked[int(question["id"])] = question
    if not asked:
        return settled, notes

    data, reason = tailor.ask_claude_json(
        _prompt(list(asked.values()), profile, title, employer, found_on, job_text if best_guess else ""),
        GUESS_PROMPT if best_guess else SYSTEM_PROMPT, exe)
    if data is None:
        return settled, [reason]
    for reply in data.get("answers") or []:
        try:
            number = int(reply.get("id"))
        except (AttributeError, TypeError, ValueError):
            continue
        question = asked.get(number)
        answer, because = reply.get("answer"), " ".join(str(reply.get("because") or "").split())[:140]
        if question is None or answer in (None, "", []) or not because:
            continue
        label = str(question["question"])
        choices = question.get("choices") or []
        if choices:
            values = answer if isinstance(answer, list) else [answer]
            picked = [choices[i] for i in (pick_option(choices, str(v)) for v in values) if i is not None]
            if not picked or (question.get("pick") != "any" and len(values) != 1):
                notes.append(f"\"{label[:50]}\": the answer wasn't one of the form's choices")
                continue
            settled[number] = (picked if question.get("pick") == "any" else picked[0], because)
            continue
        text = " ".join(str(answer if not isinstance(answer, list) else "; ".join(map(str, answer))).split())
        if len(text) > 200 or re.search(r"\b\d{3}-\d{2}-\d{4}\b", text):
            continue
        if not figures_known(text, profile_raw, label):
            notes.append(f"\"{label[:50]}\": the answer had a figure that isn't in your profile")
            continue
        settled[number] = (text, because)
    return settled, notes
