"""Written answers to an application's open questions.

"Why do you want to work here?" or "Tell us about a project you're proud of."
Claude Code on the student's plan writes each answer for one question on one
job, from the student's career profile and their own baseline cover letter.
Every sentence is then checked the way cover letters are (cover_letter.py): a
sentence that brings in a number, tool, name or claim the student never gave
is thrown out. If too much is thrown out, Claude isn't available, or the
profile has nothing to answer with, no answer is given and the question is
left for the student.

Answers are kept per job in tailored_resumes/<job>/written_answers.json (and
.txt, for reading), and reused if that job's form is opened again. Edit the
.json to change what a later run fills in.

Not written here, whatever the wording: personal and legal questions
(sponsorship, pay, demographics and so on), plain facts such as a start date
or how the student heard of the job, and "anything else?" boxes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cover_letter
import tailor
from handshake import NEVER_FILL, SENSITIVE_QUESTION

MAX_DROPPED_SENTENCES = 2
DEFAULT_WORDS = (70, 120)  # a paragraph
LINE_WORDS = (12, 35)  # a one-line box

SYSTEM_PROMPT = """You answer one written question on a student's internship application, as the student.

Absolute rules:
- Facts about the student come ONLY from the student's profile and their own letter. Never add a number, tool, language, technology, employer, course, result, responsibility, award or claim they do not state.
- Never say the student has a skill or experience that only the job posting or the question mentions.
- You may name the company and the role, and refer to what the posting says the team does, in the posting's own words.
- Answer the question that was asked, directly, in the first person. Plain and specific: no exclamation marks, no clichés, no flattery about the company beyond what the posting says.
- If the profile holds nothing true to answer the question with, reply {"answer": ""}.
- Reply with one JSON object only. No commentary, no code fences."""

# Open questions about the student, worth a few written sentences.
ESSAY = re.compile(
    r"\bwhy\b|tell (us|me)|describe|explain|briefly|in a few sentences|\b\d+\s+words\b|"
    r"what (interests|excites|motivates|draws|attracts|makes you|do you (hope|want|bring|enjoy)|"
    r"are you (most )?(proud|passionate|excited|looking)|would you|about)|"
    r"how (would|have|do|did) you|share (an?|your|something)|an example|examples (of|highlighting|that|showing)|"
    r"a time (when|you)|"
    r"proud of|passionate about|interest(ed)? in|about yourself|your (goals|interests|background)",
    re.IGNORECASE,
)
# Plain facts only the student knows; these are asked, not written.
FACTUAL = re.compile(
    r"how did you (hear|find|learn)|where did you (hear|find|learn)|when (are|can|will|would|do) you|"
    r"what (is|are) your (full |legal |preferred )?(name|gpa|major|school|pronouns|address|phone|email|"
    r"salary|expected|desired|availability|start|graduation)|how many|which (location|office|school|degree)|"
    r"referr|salary|compensation|hourly rate|start date|end date|availability|available to (start|work)|"
    r"relocat|commut|willing to|years of|\b(name|address|signature|date)\s*$",
    re.IGNORECASE,
)
# "Are you...", "Do you...": a yes or no, unless it goes on to ask for more.
YES_NO = re.compile(r"^\s*(are|do|does|did|have|has|will|would|can|could|is|were|was)\s+(you|your)\b", re.IGNORECASE)
ASKS_FOR_MORE = re.compile(r"describe|explain|tell (us|me)|\bwhy\b|elaborate", re.IGNORECASE)
FOLLOW_UP = re.compile(
    r"^(if (yes|no|so|not|other|applicable|you|selected|your)\b|other\b|please (specify|list|provide))",
    re.IGNORECASE,
)
EXTRA = re.compile(r"anything else|additional (information|comments|details|notes)|cover letter|\bcomments?\s*$",
                   re.IGNORECASE)


def is_essay(question: str, kind: str, required: bool) -> bool:
    """Whether this free-text question is one to write an answer for."""
    text = " ".join(question.split())
    if len(text.split()) < 2:
        return False
    if NEVER_FILL.search(text) or SENSITIVE_QUESTION.search(text):
        return False
    if FOLLOW_UP.search(text) or FACTUAL.search(text) or EXTRA.search(text):
        return False
    if YES_NO.search(text) and not ASKS_FOR_MORE.search(text):
        return False
    if not ESSAY.search(text):
        return False
    return kind == "textarea" or required  # a one-line box only when the form insists on it


def limits(question: str, max_chars: int = 0, single_line: bool = False) -> tuple[int, int, int]:
    """(fewest words, most words, most characters or 0) for an answer."""
    low, high = LINE_WORDS if single_line else DEFAULT_WORDS
    asked = re.search(r"(\d{2,4})\s*(?:-|to)\s*(\d{2,4})\s+words", question, re.IGNORECASE)
    if asked:
        low, high = int(asked.group(1)), int(asked.group(2))
    else:
        asked = re.search(r"(\d{2,4})\s+words", question, re.IGNORECASE)
        if asked:
            high = int(asked.group(1))
            low = max(15, round(high * 0.6))
    asked = re.search(r"(\d{2,5})\s+characters", question, re.IGNORECASE)
    if asked:
        max_chars = min(max_chars, int(asked.group(1))) if max_chars else int(asked.group(1))
    if max_chars:
        high = min(high, max(8, max_chars // 7))
        low = min(low, max(5, round(high * 0.6)))
    return low, max(high, low), max_chars


def _prompt(question: str, profile: dict[str, Any], base: str, title: str, employer: str, job_text: str,
            low: int, high: int, max_chars: int) -> str:
    letter = (f"STUDENT'S OWN LETTER (their voice and their facts):\n{base}\n\n" if base
              else "The student has no letter of their own; write in a plain, first-person student voice.\n\n")
    cap = f", and no more than {max_chars} characters" if max_chars else ""
    return (
        f"JOB: {title} at {employer}\n\n"
        f"JOB POSTING:\n{job_text[:5000] or '(not available)'}\n\n"
        f"{letter}"
        f"STUDENT PROFILE (the only other allowed source of facts):\n{json.dumps(profile, ensure_ascii=False, indent=1)}\n\n"
        f"QUESTION ON THE APPLICATION: {question}\n\n"
        "TASK: Write the student's answer.\n"
        f"- {low} to {high} words{cap}. One short paragraph; two only if the question has two parts.\n"
        "- Lead with the one or two things the student has actually done that best answer this question for this job.\n"
        "- No greeting and no sign-off.\n\n"
        'Reply with JSON shaped exactly like:\n{"answer": "..."}'
    )


def _fit(paragraphs: list[str], max_chars: int) -> list[str]:
    """Drop whole sentences from the end until the answer fits the box."""
    if not max_chars:
        return paragraphs
    groups = [cover_letter.split_sentences(p) for p in paragraphs]
    while any(groups) and len("\n\n".join(" ".join(g) for g in groups if g)) > max_chars:
        next(g for g in reversed(groups) if g).pop()
    return [" ".join(g) for g in groups if g]


def write(
    question: str,
    profile: dict[str, Any],
    title: str,
    employer: str,
    job_text: str,
    *,
    base: str = "",
    max_chars: int = 0,
    single_line: bool = False,
    exe: str | None = None,
) -> tuple[str | None, list[str]]:
    """Returns (the answer or None, notes on what was cut or why there is none)."""
    low, high, max_chars = limits(question, max_chars, single_line)
    data, reason = tailor.ask_claude_json(
        _prompt(question, profile, base, title, employer, job_text, low, high, max_chars), SYSTEM_PROMPT, exe
    )
    if data is None:
        return None, [reason]
    raw = [" ".join(p.split()) for p in re.split(r"\n\s*\n", str(data.get("answer") or "")) if p.strip()]
    if not raw:
        return None, ["nothing in your profile answers this question"]
    # Words in the question itself (a product, a team) may be named, like the posting's.
    kept, notes = cover_letter.check_letter(raw, profile, base, title, employer, f"{job_text}\n{question}")
    kept = _fit(kept, max_chars)
    enough = 6 if single_line or high < 30 else 25
    if len(notes) > MAX_DROPPED_SENTENCES or cover_letter.word_count(kept) < enough:
        return None, notes + ["too much of the answer had to be cut, so it's left for you"]
    return "\n\n".join(kept), notes


@dataclass
class EssayResult:
    text: str
    method: str  # ai | saved | none (text is empty; the notes say why)
    notes: list[str] = field(default_factory=list)


def _key(question: str) -> str:
    return " ".join(question.lower().split())[:300]


def answer(
    job_id: str,
    question: str,
    title: str,
    employer: str,
    job_text: str,
    *,
    max_chars: int = 0,
    single_line: bool = False,
    profile_path: str | Path = tailor.PROFILE_PATH,
    base_path: str | Path = cover_letter.BASE_PATH,
    out_dir: str | Path = tailor.OUT_DIR,
    reuse: bool = True,
    exe: str | None = None,
) -> EssayResult:
    """Write (or reuse) the answer to one question on one job's application."""
    folder = Path(out_dir) / f"{job_id}-{tailor._slug(employer)}"
    record_path = folder / "written_answers.json"
    try:
        saved = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    known = saved.get(_key(question))
    if reuse and isinstance(known, dict) and str(known.get("answer") or "").strip():
        text = str(known["answer"]).strip()
        if not max_chars or len(text) <= max_chars:
            return EssayResult(text, "saved", ["reused the answer written earlier for this job"])

    profile = tailor.load_profile(profile_path)
    text, notes = write(question, profile, title, employer, job_text, base=cover_letter.load_base(base_path),
                        max_chars=max_chars, single_line=single_line, exe=exe)
    if text is None:
        return EssayResult("", "none", notes)
    saved[_key(question)] = {"question": question, "answer": text, "notes": notes,
                             "job": {"id": job_id, "title": title, "employer": employer}}
    folder.mkdir(parents=True, exist_ok=True)
    record_path.write_text(json.dumps(saved, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "written_answers.txt").write_text(
        "\n\n".join(f"{entry['question']}\n\n{entry['answer']}" for entry in saved.values()) + "\n", encoding="utf-8"
    )
    return EssayResult(text, "ai", notes)


# ------------------------------------------------------------------ the page


def _job_of(folder: Path, entries: dict[str, Any], about: dict[str, dict[str, str]]) -> dict[str, str]:
    """What is known about the job a folder of answers belongs to."""
    known = max((key for key in about if folder.name.startswith(key + "-") or folder.name == key), key=len, default="")
    job = dict(about.get(known, {}))
    for entry in entries.values():
        for field_name in ("title", "employer"):
            job.setdefault(field_name, str((entry.get("job") or {}).get(field_name) or ""))
    for other in ("cover_letter.json", "plan.json"):  # written for the same job, and they name it
        if job.get("title") and job.get("employer"):
            break
        try:
            named = json.loads((folder / other).read_text(encoding="utf-8")).get("job") or {}
        except (OSError, ValueError):
            continue
        job["title"] = job.get("title") or str(named.get("title") or "")
        job["employer"] = job.get("employer") or str(named.get("employer") or "")
    job["title"] = job.get("title") or folder.name
    return job


def write_page(folder: str | Path, about: dict[str, dict[str, str]] | None = None,
               out_dir: str | Path = tailor.OUT_DIR) -> Path:
    """One page holding every answer written so far, the newest job first.

    `about` maps a job's folder id to what the run knows of it: title,
    employer, link and what happened to the application.
    """
    from datetime import date, datetime
    from html import escape

    about = about or {}
    jobs = []
    for record_path in Path(out_dir).glob("*/written_answers.json"):
        try:
            entries = json.loads(record_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = {k: v for k, v in entries.items() if isinstance(v, dict) and str(v.get("answer") or "").strip()}
        if entries:
            jobs.append((record_path.stat().st_mtime, _job_of(record_path.parent, entries, about), entries))
    jobs.sort(key=lambda job: -job[0])

    blocks = []
    for written, job, entries in jobs:
        heading = escape(job["title"]) + (f" <span class='at'>@ {escape(job['employer'])}</span>" if job.get("employer") else "")
        if job.get("link"):
            heading = f"<a href='{escape(job['link'], quote=True)}' target='_blank' rel='noopener'>{heading}</a>"
        facts = [f"written {datetime.fromtimestamp(written).strftime('%B %d').replace(' 0', ' ')}"]
        if job.get("status"):
            facts.insert(0, escape(job["status"]))
        answers = []
        for entry in entries.values():
            paragraphs = "".join(f"<p>{escape(p)}</p>" for p in str(entry["answer"]).split("\n\n"))
            cut = [n for n in entry.get("notes") or [] if "dropped" in n]
            note = f"<div class='why'>{escape('; '.join(cut))}</div>" if cut else ""
            answers.append(f"<h3>{escape(str(entry.get('question') or ''))}</h3>{paragraphs}{note}")
        blocks.append(f"<section><h2>{heading}</h2><div class='why'>{' · '.join(facts)}</div>{''.join(answers)}</section>")

    count = sum(len(entries) for _, _, entries in jobs)
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Essays</title>
<style>
  :root {{ color-scheme: light dark; --fg:#1b1b1f; --muted:#5f6068; --line:#e3e3e8; --bg:#fff; --accent:#1f5fd6; }}
  @media (prefers-color-scheme: dark) {{ :root {{ --fg:#ececf1; --muted:#a4a5ad; --line:#34343b; --bg:#17171b; --accent:#8ab4ff; }} }}
  body {{ margin:0; padding:24px 16px; background:var(--bg); color:var(--fg); font:15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:820px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  h2 {{ font-size:17px; margin:0 0 2px; }}
  h3 {{ font-size:15px; margin:16px 0 4px; }}
  p {{ margin:0 0 10px; }}
  .lead {{ color:var(--muted); margin:0 0 20px; }}
  section {{ border-top:1px solid var(--line); padding:18px 0; }}
  a {{ color:var(--accent); text-decoration:none; }}
  a:hover {{ text-decoration:underline; }}
  .at {{ font-weight:400; }}
  .why {{ color:var(--muted); font-size:13px; }}
</style></head>
<body><main>
<h1>Essays</h1>
<p class="lead">{count} answers written for {len(jobs)} applications, from your profile, newest first.
Updated {date.today().strftime('%B %d, %Y').replace(' 0', ' ')}. Each one is kept with its job in
<code>tailored_resumes</code>; edit <code>written_answers.json</code> there to change what a later run fills in.</p>
{chr(10).join(blocks) or "<section>No essays written yet.</section>"}
</main></body></html>
"""
    out = Path(folder) / "Essays.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page, encoding="utf-8")
    return out
