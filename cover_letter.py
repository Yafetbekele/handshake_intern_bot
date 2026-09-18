"""Cover letters for postings whose Handshake application requires one.

The letter starts from the student's own baseline letter
(profile/cover_letter_base.txt) and is adapted to one job: the company, the
role, and which of the student's experiences it leads with. Claude Code on the
student's plan does the adapting. Every sentence is then checked, and a
sentence that brings in a number, tool, name or claim the student never gave
is thrown out. If too much is thrown out, or Claude isn't available, the
baseline letter is used with only the company and role filled in.

With no baseline letter yet, the letter is built from the career profile alone.

Letters are written per job and never reused for another employer.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import tailor
from resume_parser import TECH_TOKENS, normalize

BASE_PATH = Path(
    os.environ.get("HSBOT_COVER_BASE") or tailor.HERE / "profile" / "cover_letter_base.txt"
)
TARGET_WORDS = (200, 250)
MIN_WORDS = 120
MAX_DROPPED_SENTENCES = 2

# Easy to invent, never in the student's facts. Profile entries add their own.
LETTER_NEVER_CLAIM = [
    "award", "awarded", " won ", "winner", "first place", "patent", "publish", "publication",
    "promoted", "supervised", "managed a team", "trained new", "led a team",
]
FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|i'll|my|me|mine)\b", re.IGNORECASE)
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")

SYSTEM_PROMPT = """You adapt a student's own cover letter to one job posting.

Absolute rules:
- Facts about the student come ONLY from the student's letter and profile. Never add a number, tool, language, technology, employer, course, result, responsibility, award or claim they do not state.
- Never say the student has a skill or experience that only the job posting mentions.
- You may name the company and the role, and refer to what the posting says the team does, in the posting's own words.
- Keep the student's voice. Plain and direct: no exclamation marks, no clichés such as "I am writing to express my interest", no flattery about the company beyond what the posting says.
- Reply with one JSON object only. No commentary, no code fences."""


@dataclass
class LetterResult:
    path: Path
    method: str  # ai | baseline | profile | saved
    notes: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ baseline


def load_base(path: str | Path = BASE_PATH) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


GREETING = re.compile(r"^\s*(dear|to whom|hello|hi)\b", re.IGNORECASE)
SIGN_OFF = re.compile(r"^\s*(sincerely|best|regards|kind regards|thank you|thanks|respectfully|warmly)\b", re.IGNORECASE)


def base_paragraphs(base: str) -> list[str]:
    """The body of the baseline letter: greeting, sign-off and name removed."""
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", base) if b.strip()]
    body: list[str] = []
    for block in blocks:
        short = len(block.split()) <= 8
        if GREETING.match(block) and short:
            continue
        if SIGN_OFF.match(block) and short:
            break  # "Sincerely, Name" and everything after it
        body.append(block)
    return body


def fill_placeholders(text: str, title: str, employer: str) -> str:
    # What the company works on can't be known without reading the posting, so
    # without Claude that clause is left out rather than guessed.
    text = re.sub(r",?\s*(because of|for)\s+(its|their)\s+work\s+(in|on)\s+\{company_work\}", "", text)
    text = text.replace("{company_work}", "this field")
    return (
        text.replace("{company}", employer or "your team")
        .replace("{role}", title or "this role")
        .replace("[Company]", employer or "your team")
        .replace("[Role]", title or "this role")
    )


def has_placeholders(base: str) -> bool:
    return any(p in base for p in ("{company}", "{role}", "[Company]", "[Role]"))


# ---------------------------------------------------------------- the checks


def _profile_never_claim(profile: dict[str, Any]) -> list[str]:
    """Phrases never allowed anywhere in the letter."""
    phrases = list(LETTER_NEVER_CLAIM)
    for school in profile.get("education", []):
        phrases += [str(p).lower() for p in school.get("never_claim", [])]
    return phrases


ENTRY_WORD_SKIP = {"university", "maryland", "baltimore", "county", "student", "students", "project",
                   "personal", "undergraduate", "advisor", "assistant", "president", "captain", "team",
                   "club", "lab", "research", "researcher", "state", "college", "school", "home"}


def _entry_rules(profile: dict[str, Any]) -> list[tuple[set[str], list[str]]]:
    """Each entry's own never-claim list, with the words that show a sentence is about it.

    "team" may be off limits for a solo project yet fine in "join your team",
    so an entry's list only applies to sentences that mention that entry.
    """
    rules = []
    for entry_id, entry in tailor.profile_entries(profile).items():
        phrases = [str(p).lower() for p in entry.get("never_claim", []) if str(p).strip()]
        if not phrases:
            continue
        source = f"{entry_id.replace('-', ' ')} {entry.get('organization', '')} {entry.get('role', '')}"
        words = {w for w in normalize(source).split() if len(w) >= 4 and w not in ENTRY_WORD_SKIP}
        rules.append((words, phrases))
    return rules


EXPERIENCE_WORDS = re.compile(
    r"\b(used|use|using|worked|experience|experienced|built|developed|wrote|designed|know|knowledge|"
    r"familiar|proficient|skilled|expertise|background)\b",
    re.IGNORECASE,
)


HONORIFICS = {"dr", "mr", "mrs", "ms", "prof", "st", "jr", "sr", "inc", "co", "ltd", "u.s", "e.g", "i.e"}


def _numbers(raw: str) -> set[str]:
    return {n.replace(",", "") for n in tailor.NUMBER_RE.findall(raw)}


def _known(term: str, tokens: set[str], text: str) -> bool:
    return term in tokens or term in text


def split_sentences(paragraph: str) -> list[str]:
    """Sentences, without breaking after "Dr." or "Inc."."""
    pieces = [p for p in SENTENCE_SPLIT.split(" ".join(str(paragraph).split())) if p]
    sentences: list[str] = []
    for piece in pieces:
        last = sentences[-1].rsplit(" ", 1)[-1].rstrip(".").lower() if sentences else ""
        if sentences and last in HONORIFICS:
            sentences[-1] += " " + piece
        else:
            sentences.append(piece)
    return sentences


@dataclass
class _Sources:
    student_text: str  # normalized profile + baseline letter
    student_tokens: set[str]
    student_numbers: set[str]
    job_text: str  # normalized posting
    job_tokens: set[str]
    job_numbers: set[str]
    named_text: str  # normalized job title and employer: always fine to name
    never_claim: list[str]
    entry_rules: list[tuple[set[str], list[str]]] = field(default_factory=list)


def _sources(profile: dict[str, Any], base: str, title: str, employer: str, job_text: str) -> _Sources:
    student_raw = json.dumps(profile, ensure_ascii=False) + " " + base
    student = normalize(student_raw)
    job = normalize(f"{title} {employer} {job_text}")
    return _Sources(
        student, set(student.split()), _numbers(student_raw),
        job, set(job.split()), _numbers(f"{title} {employer} {job_text}"),
        normalize(f"{title} {employer}"), _profile_never_claim(profile), _entry_rules(profile),
    )


def sentence_problems(sentence: str, src: _Sources) -> list[str]:
    """What in this sentence the student never said about themselves.

    About the student ("I", "my"), every number, tool and name must come from
    the student's profile or letter; only the job's title and employer may be
    named as well. About the job, the posting's own words are fine too.
    """
    problems = [n for n in tailor.NUMBER_RE.findall(sentence)
                if n.replace(",", "") not in src.student_numbers | src.job_numbers]

    about_student = bool(FIRST_PERSON.search(sentence))
    # "I'd like to work on your Pod platform" is fine; "I've used Pod" is a claim.
    claims_experience = about_student and bool(EXPERIENCE_WORDS.search(sentence))
    words = tailor.TERM_RE.findall(sentence)
    for index, word in enumerate(words):
        lowered = normalize(word).strip(".-/")
        if not lowered or lowered in HONORIFICS or word in ("I", "I'm", "I've", "I'd", "I'll"):
            continue
        is_tech = lowered in TECH_TOKENS
        is_name = index > 0 and (word[0].isupper() or any(c.isupper() for c in word[1:]))
        if not (is_tech or is_name):
            continue
        for part in lowered.split():
            if _known(part, src.student_tokens, src.student_text):
                continue
            if not is_tech and _known(part, set(src.named_text.split()), src.named_text):
                continue  # the company or the role by name
            if _known(part, src.job_tokens, src.job_text) and (
                not about_student or (not is_tech and not claims_experience)
            ):
                continue  # describing the job in the posting's words
            problems.append(word)
            break

    padded = f" {sentence.lower()} "
    problems += [p.strip() for p in src.never_claim if p and p in padded]
    sentence_words = set(normalize(sentence).split())
    for entry_words, phrases in src.entry_rules:
        if entry_words & sentence_words:
            problems += [p.strip() for p in phrases if p in padded]
    return problems


def check_letter(
    paragraphs: list[str], profile: dict[str, Any], base: str, title: str, employer: str, job_text: str
) -> tuple[list[str], list[str]]:
    """Drop sentences with made-up facts. Returns (paragraphs kept, notes)."""
    src = _sources(profile, base, title, employer, job_text)
    kept: list[str] = []
    notes: list[str] = []
    for paragraph in paragraphs:
        sentences = split_sentences(paragraph)
        good = []
        for sentence in sentences:
            problems = sentence_problems(sentence, src)
            if problems:
                notes.append(f"dropped a sentence mentioning {', '.join(dict.fromkeys(problems))}")
            else:
                good.append(sentence)
        if good:
            kept.append(" ".join(good))
    return kept, notes


def word_count(paragraphs: list[str]) -> int:
    return sum(len(p.split()) for p in paragraphs)


# ------------------------------------------------------------ writing a letter


def _ai_prompt(profile: dict[str, Any], base: str, title: str, employer: str, job_text: str) -> str:
    source = (
        f"STUDENT'S OWN LETTER (their voice and their facts):\n{base}\n\n"
        if base
        else "The student has no letter of their own yet; write in a plain, first-person student voice.\n\n"
    )
    return (
        f"JOB: {title} at {employer}\n\n"
        f"JOB POSTING:\n{job_text[:6000]}\n\n"
        f"{source}"
        f"STUDENT PROFILE (the only other allowed source of facts):\n{json.dumps(profile, ensure_ascii=False, indent=1)}\n\n"
        "TASK: Write the body of a cover letter for this job.\n"
        f"- {TARGET_WORDS[0]} to {TARGET_WORDS[1]} words in three paragraphs: why this role at this company, "
        "the two or three most relevant things the student has done, and a short close.\n"
        "- Start from the student's own letter when there is one: keep its voice and its best lines, "
        "change what doesn't fit this job, and lead with the experiences this posting cares about. "
        "Its blanks are {company} (the employer), {role} (the job title) and {company_work} (what the "
        "posting says the company or team works on, in the posting's own words).\n"
        "- No greeting, no sign-off, no name, no date: body paragraphs only.\n\n"
        'Reply with JSON shaped exactly like:\n{"paragraphs": ["...", "...", "..."], '
        '"changes": "one sentence on what you emphasized for this job"}'
    )


def profile_letter(profile: dict[str, Any], title: str, employer: str, job_text: str) -> list[str]:
    """A plain letter from the profile's own wording, for when nothing else is available."""
    school = tailor.primary_education(profile)
    plan = tailor.rule_plan(profile, job_text, title)
    entries = tailor.profile_entries(profile)

    standing = str(school.get("standing", "")).strip()
    opening = (
        f"I'm applying for the {title} position at {employer}. I'm a "
        f"{standing.lower() + ' ' if standing else ''}studying for a {school.get('degree', 'degree')} "
        f"at {school.get('school', 'my university')}"
        + (f", graduating {school['expected_graduation']}" if school.get("expected_graduation") else "")
        + "."
    )

    lines = []
    for section in plan.sections:
        for chosen in section["entries"]:
            entry = entries.get(chosen["id"], {})
            if not chosen.get("bullets"):
                continue
            bullet = chosen["bullets"][0].rstrip(".")
            action = bullet[0].lower() + bullet[1:]
            role = re.sub(r"\s*\(.*?\)", "", str(entry.get("role") or "")).strip()
            org = str(entry.get("organization") or "").strip()
            if "project" in role.lower() or "project" in section["heading"].lower() and not role:
                lines.append(f"For my {org or 'own'} project, I {action}." if org else f"In a project of my own, I {action}.")
            elif role and org:
                lines.append(f"As {_article(role)} {role} at {org}, I {action}.")
            else:
                lines.append(f"At {org or 'work'}, I {action}.")
            if len(lines) == 2:
                break
        if len(lines) == 2:
            break
    middle = " ".join(lines) or "My coursework and projects have prepared me for this kind of work."

    skills = ", ".join(plan.skills[:4])
    close = (
        (f"I work with {skills}. " if skills else "")
        + f"I would welcome the chance to talk about how I can contribute to {employer}. "
        "Thank you for your time and consideration."
    )
    return [opening, middle, close]


def _article(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def write_letter(
    profile: dict[str, Any],
    title: str,
    employer: str,
    job_text: str,
    *,
    base: str = "",
    use_ai: bool = True,
    exe: str | None = None,
) -> tuple[list[str], str, list[str]]:
    """Returns (body paragraphs, method, notes)."""
    notes: list[str] = []
    if use_ai:
        data, reason = tailor.ask_claude_json(
            _ai_prompt(profile, base, title, employer, job_text), SYSTEM_PROMPT, exe
        )
        raw = [str(p) for p in (data or {}).get("paragraphs", []) if str(p).strip()] if data else []
        if raw:
            kept, dropped = check_letter(raw, profile, base, title, employer, job_text)
            notes += dropped
            if len(dropped) <= MAX_DROPPED_SENTENCES and word_count(kept) >= MIN_WORDS:
                if data.get("changes"):
                    notes.insert(0, str(data["changes"])[:200])
                return kept, "ai", notes
            notes.append("Claude's letter needed too many cuts, so a plainer letter was used instead")
        else:
            notes.append(reason if data is None else "Claude returned no letter")

    if base and has_placeholders(base):
        return [fill_placeholders(p, title, employer) for p in base_paragraphs(base)], "baseline", notes
    if base:
        notes.append("the baseline letter has no {company} or {role} placeholders, so it wasn't reused as is")
    return profile_letter(profile, title, employer, job_text), "profile", notes


# --------------------------------------------------------------------- render


def letter_filename(profile: dict[str, Any]) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", profile["contact"]["name"]).strip("_") + "_Cover_Letter.pdf"


def render_pdf(profile: dict[str, Any], paragraphs: list[str], employer: str, path: str | Path) -> Path:
    """One page in Times, with the same header as the student's resume."""
    from xml.sax.saxutils import escape

    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    contact = profile["contact"]
    body = ParagraphStyle("body", fontName="Times-Roman", fontSize=11.5, leading=15, spaceAfter=10)
    name_style = ParagraphStyle("name", fontName="Times-Bold", fontSize=16, leading=19)
    small = ParagraphStyle("small", fontName="Times-Roman", fontSize=10.5, leading=13)

    linkedin = contact.get("linkedin", "").replace("https://www.", "").replace("https://", "")
    contact_line = " | ".join(v for v in [contact.get("location"), contact.get("phone"), contact.get("email"), linkedin] if v)

    story = [
        Paragraph(escape(contact["name"]), name_style),
        Paragraph(escape(contact_line), small),
        Spacer(1, 0.3 * inch),
        Paragraph(escape(date.today().strftime("%B %d, %Y").replace(" 0", " ")), body),
        Paragraph(f"Hiring Team<br/>{escape(employer)}", body),
        Paragraph("Dear Hiring Team,", body),
    ]
    story += [Paragraph(escape(p), body) for p in paragraphs]
    story += [Paragraph(f"Sincerely,<br/>{escape(contact['name'])}", body)]
    SimpleDocTemplate(
        str(path), pagesize=letter,
        leftMargin=inch, rightMargin=inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch,
        title=f"Cover letter for {employer}", author=contact["name"],
    ).build(story)
    return path


def cover_letter(
    job_id: str,
    title: str,
    employer: str,
    job_text: str,
    *,
    use_ai: bool = True,
    profile_path: str | Path = tailor.PROFILE_PATH,
    base_path: str | Path = BASE_PATH,
    out_dir: str | Path = tailor.OUT_DIR,
    reuse: bool = True,
) -> LetterResult:
    """Write (or reuse) the cover letter PDF for one job."""
    profile = tailor.load_profile(profile_path)
    folder = Path(out_dir) / f"{job_id}-{tailor._slug(employer)}"
    pdf_path = folder / letter_filename(profile)
    record_path = folder / "cover_letter.json"

    if reuse and pdf_path.exists() and record_path.exists():
        saved = json.loads(record_path.read_text(encoding="utf-8"))
        return LetterResult(pdf_path, "saved", [f"reused the letter written earlier ({saved.get('method')})"])

    paragraphs, method, notes = write_letter(
        profile, title, employer, job_text, base=load_base(base_path), use_ai=use_ai
    )
    render_pdf(profile, paragraphs, employer, pdf_path)
    (folder / "cover_letter.txt").write_text("\n\n".join(paragraphs) + "\n", encoding="utf-8")
    record_path.write_text(
        json.dumps({"method": method, "notes": notes, "job": {"id": job_id, "title": title, "employer": employer}}, indent=2),
        encoding="utf-8",
    )
    return LetterResult(pdf_path, method, notes)
