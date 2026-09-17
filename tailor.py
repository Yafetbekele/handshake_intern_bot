"""Tailor the resume to one job and render it as a one-page PDF.

Everything comes from profile/career_profile.json, the student's own record of
true facts. A tailored resume may reword, reorder, select and emphasize those
facts. It may never add facts, numbers, tools or results.

Two ways to build a plan:

* AI: Claude Code, running on the student's existing Claude plan (no API key,
  no extra cost), picks and rewords bullets for the job. Every AI-written
  bullet is checked against the profile; a bullet that introduces a number or
  a technical term the profile doesn't contain is thrown out and replaced.
* Rules: ranks the profile's pre-written bullets, coursework and skills by how
  well their topic tags match the job. Free, instant, and used whenever the AI
  is unavailable.

Either plan is rendered in the format of the student's original resume: Times,
bold section headings, dash bullets, one page.
"""

from __future__ import annotations

import glob
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from resume_parser import TECH_TOKENS, normalize

HERE = Path(__file__).resolve().parent
# Environment overrides exist so tests never touch the real profile or resumes.
PROFILE_PATH = Path(os.environ.get("HSBOT_PROFILE") or HERE / "profile" / "career_profile.json")
OUT_DIR = Path(os.environ.get("HSBOT_TAILORED_DIR") or HERE / "tailored_resumes")

MAX_BULLETS = {"default": 3, "low": 2}
MAX_SKILLS = 12
MAX_COURSES = 6
MAX_BULLET_CHARS = 170


@dataclass
class Plan:
    coursework: list[str]
    honors: list[str]
    include_high_school: bool
    sections: list[dict[str, Any]]
    skills: list[str]
    method: str
    notes: list[str] = field(default_factory=list)


@dataclass
class TailorResult:
    path: Path
    method: str
    notes: list[str]


# --------------------------------------------------------------------- profile


def load_profile(path: str | Path = PROFILE_PATH) -> dict[str, Any]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"No career profile at {path}. Resume tailoring needs it; see the README."
        )
    return json.loads(path.read_text(encoding="utf-8"))


def required_entries(profile: dict[str, Any]) -> list[str]:
    """Entries marked "always_include": kept on every tailored resume."""
    return [e["id"] for e in profile_entries(profile).values() if e.get("always_include")]


def trim_first_entries(profile: dict[str, Any]) -> list[str]:
    """Entries marked "trim_first": the first thing cut when a page overflows."""
    return [e["id"] for e in profile_entries(profile).values() if e.get("trim_first")]


def primary_education(profile: dict[str, Any]) -> dict[str, Any]:
    """The degree in progress: the education entry that lists coursework."""
    return next((e for e in profile.get("education", []) if "coursework" in e), {})


def earlier_education(profile: dict[str, Any]) -> dict[str, Any]:
    """An earlier school worth a line for its honors, if any."""
    return next((e for e in profile.get("education", []) if e.get("honors")), {})


def profile_entries(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for s in profile.get("sections", []) for e in s.get("entries", [])}


def _profile_corpus(profile: dict[str, Any]) -> str:
    return normalize(json.dumps(profile, ensure_ascii=False))


# ----------------------------------------------------------------- job terms


def _job_text(job_text: str, title: str) -> tuple[str, set[str]]:
    text = normalize(f"{title} {title} {job_text}")
    return text, set(text.split())


def _tag_score(tags: list[str], job_norm: str, job_tokens: set[str]) -> float:
    score = 0.0
    for tag in tags:
        tag_norm = normalize(tag)
        if not tag_norm:
            continue
        if " " in tag_norm:
            score += 1.5 if tag_norm in job_norm else 0.0
        elif tag_norm in job_tokens:
            score += 1.0
    return score


# ------------------------------------------------------------------ rule plan


def order_sections(sections: list[dict[str, Any]], scores: dict[str, float]) -> list[dict[str, Any]]:
    """Most relevant section first; leadership and involvement always last."""
    def is_leadership(s: dict[str, Any]) -> bool:
        return "LEADERSHIP" in s["heading"].upper()

    main = sorted(
        [s for s in sections if not is_leadership(s)],
        key=lambda s: -scores.get(s["heading"], 0.0),
    )
    return main + [s for s in sections if is_leadership(s)]


def rule_plan(profile: dict[str, Any], job_text: str, title: str = "") -> Plan:
    job_norm, job_tokens = _job_text(job_text, title)

    umbc = primary_education(profile)
    courses = umbc.get("coursework", [])
    ranked_courses = sorted(
        enumerate(courses), key=lambda p: (-_tag_score(p[1].get("tags", []), job_norm, job_tokens), p[0])
    )
    coursework = [c["name"] for _, c in ranked_courses][:MAX_COURSES]

    hs = earlier_education(profile)
    honors_ranked = sorted(
        enumerate(hs.get("honors", [])),
        key=lambda p: (-_tag_score(p[1].get("tags", []), job_norm, job_tokens), p[0]),
    )
    honors = [h["name"] for _, h in honors_ranked[:2]]

    sections = []
    section_scores: dict[str, float] = {}
    for section in profile.get("sections", []):
        scored_entries = []
        for entry in section.get("entries", []):
            bullets = entry.get("bullets", [])
            ranked = sorted(
                enumerate(bullets),
                key=lambda p: (-_tag_score(p[1].get("tags", []), job_norm, job_tokens), p[0]),
            )
            limit = MAX_BULLETS["low"] if entry.get("priority", 5) <= 5 else MAX_BULLETS["default"]
            chosen = [b["text"] for _, b in ranked[:limit]]
            relevance = sum(_tag_score(b.get("tags", []), job_norm, job_tokens) for _, b in ranked[:limit])
            scored_entries.append((relevance + entry.get("priority", 5) * 0.4, entry["id"], chosen))
        scored_entries.sort(key=lambda t: -t[0])
        section_scores[section["heading"]] = max((t[0] for t in scored_entries), default=0.0)
        sections.append(
            {
                "heading": section["heading"],
                "entries": [{"id": entry_id, "bullets": chosen} for _, entry_id, chosen in scored_entries],
            }
        )

    sections = order_sections(sections, section_scores)

    skills = profile.get("skills", [])
    ranked_skills = sorted(
        enumerate(skills), key=lambda p: (-_tag_score(p[1].get("tags", []), job_norm, job_tokens), p[0])
    )
    skill_names = [s["name"] for _, s in ranked_skills[:MAX_SKILLS]]

    return Plan(
        coursework=coursework,
        honors=honors,
        include_high_school=True,
        sections=sections,
        skills=skill_names,
        method="rules",
    )


# -------------------------------------------------------------------- AI plan


def find_claude() -> str | None:
    """Locate the Claude Code program, including the copy bundled with the desktop app."""
    override = os.environ.get("HSBOT_CLAUDE")
    if override:
        return override
    on_path = shutil.which("claude")
    if on_path:
        return on_path
    appdata = os.environ.get("APPDATA", "")
    candidates = glob.glob(os.path.join(appdata, "Claude", "claude-code", "*", "claude.exe"))

    def version_key(p: str) -> tuple[int, ...]:
        parts = re.findall(r"\d+", Path(p).parent.name)
        return tuple(int(x) for x in parts)

    return max(candidates, key=version_key) if candidates else None


def claude_ready(exe: str | None = None) -> tuple[bool, str]:
    """(usable, explanation). Checks that Claude Code exists and is signed in."""
    exe = exe or find_claude()
    if not exe:
        return False, "Claude Code was not found on this computer."
    try:
        done = subprocess.run(
            [exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8", timeout=60
        )
        status = json.loads(done.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        return False, f"Could not check Claude Code sign-in ({type(exc).__name__})."
    if not status.get("loggedIn"):
        return False, f'Claude Code is not signed in. Run once:  "{exe}" auth login'
    return True, "Claude Code is signed in."


SYSTEM_PROMPT = """You tailor a student's resume to one job posting.

Absolute rules:
- Use ONLY facts in the student's profile. Never add a number, tool, language, technology, employer, result, responsibility or claim that is not stated there.
- You may reword, shorten, combine two facts from the same entry, reorder, and choose what to include.
- You may use the job's vocabulary only when a profile fact genuinely supports it. Example: the profile says "C++ and Verilog on FPGA", so "FPGA development" is fine; "SystemVerilog" or "ASIC tape-out" is not.
- Keep every date, title, name and figure exactly as the profile states it.
- Reply with one JSON object only. No commentary, no code fences."""


def _ai_prompt(profile: dict[str, Any], job_text: str, title: str, employer: str) -> str:
    entries = profile_entries(profile)
    schema = {
        "coursework": [f"up to {MAX_COURSES} exact course names from the profile, most relevant first"],
        "high_school_honors": ["0 to 2 exact honor names, only if they help"],
        "sections": [
            {
                "heading": "an exact section heading from the profile",
                "entries": [{"id": "an exact entry id", "bullets": ["1 to 3 bullets"]}],
            }
        ],
        "skills": [f"up to {MAX_SKILLS} exact skill names from the profile, most relevant first"],
        "changes": "one sentence on what you emphasized for this job",
    }
    return (
        f"JOB: {title} at {employer}\n\n"
        f"JOB POSTING:\n{job_text[:7000]}\n\n"
        f"STUDENT PROFILE (the only allowed source of facts):\n{json.dumps(profile, ensure_ascii=False, indent=1)}\n\n"
        "TASK: Choose and word the resume content that best fits this job.\n"
        f"- Always include entries {', '.join(required_entries(profile)) or '(none required)'}. Include the others only if they help.\n"
        f"- At most 3 bullets per entry and about 13 bullets in total, each under {MAX_BULLET_CHARS} characters, starting with a strong verb.\n"
        "- Put the most relevant entries and bullets first within each section.\n"
        f"- Valid entry ids: {', '.join(entries)}.\n\n"
        f"Reply with JSON shaped exactly like:\n{json.dumps(schema, indent=1)}"
    )


def _extract_json(text: str) -> dict[str, Any] | None:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
TERM_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+#.\-/]*\b")


def ungrounded_terms(bullet: str, entry: dict[str, Any], corpus: str) -> list[str]:
    """Numbers and technical or proper terms in `bullet` that the profile never mentions.

    Numbers must come from the bullet's own entry. Technical terms and
    capitalized names must appear somewhere in the profile.
    """
    entry_text = _entry_fact_text(entry)
    entry_numbers = {n.replace(",", "") for n in NUMBER_RE.findall(entry_text)}
    problems = [
        n for n in NUMBER_RE.findall(bullet) if n.replace(",", "") not in entry_numbers
    ]

    corpus_tokens = set(corpus.split())
    words = TERM_RE.findall(bullet)
    for index, word in enumerate(words):
        lowered = normalize(word).strip(".-/")
        if not lowered:
            continue
        is_tech = lowered in TECH_TOKENS
        # Capitalized mid-sentence words are names, acronyms or technologies.
        is_name = index > 0 and (word[0].isupper() or any(c.isupper() for c in word[1:]))
        if not (is_tech or is_name):
            continue
        parts = [p for p in lowered.split() if p]
        if parts and not all(p in corpus_tokens or p in corpus for p in parts):
            problems.append(word)
    return problems


def _entry_fact_text(entry: dict[str, Any]) -> str:
    """The entry's factual wording only: never its topic tags or its false-claims list."""
    parts = [entry.get("organization", ""), entry.get("role", ""), entry.get("dates", "")]
    parts += entry.get("facts", [])
    parts += [b["text"] if isinstance(b, dict) else str(b) for b in entry.get("bullets", [])]
    parts += entry.get("skills", [])
    return " ".join(str(p) for p in parts)


# Claims that are easy to invent and are not in the profile. An entry can add its
# own under "never_claim" (for example, research with no findings yet).
NEVER_CLAIM = [
    "award", "awarded", "won ", "winner", "first place", "patent", "publish", "publication",
    "promoted to", "was promoted", "supervised", "team of", "hired", "trained new", "managed a team",
]


def false_claims(bullet: str, entry: dict[str, Any]) -> list[str]:
    lowered = f" {bullet.lower()} "
    forbidden = NEVER_CLAIM + [str(p).lower() for p in entry.get("never_claim", [])]
    return [phrase.strip() for phrase in forbidden if phrase in lowered]


GENERIC_WORDS = {
    # resume verbs and connectors that say nothing factual on their own
    "built", "build", "builds", "building", "developed", "develop", "develops", "designed",
    "design", "designs", "created", "create", "led", "lead", "leads", "helped", "help",
    "supported", "support", "used", "use", "uses", "using", "applied", "apply", "applies",
    "worked", "work", "works", "performed", "perform", "completed", "complete", "delivered",
    "improved", "improve", "operated", "operate", "operates", "managed", "manage", "manages",
    "handled", "handle", "provided", "provide", "gained", "hands", "hands-on", "through",
    "with", "while", "across", "within", "including", "their", "each", "every", "strong",
    "effective", "effectively", "successfully", "independently", "various", "multiple",
    "skills", "experience", "ensuring", "ensure", "about", "around", "over", "into", "from",
    "that", "this", "these", "those", "both", "also", "well", "more", "most", "such",
}


def unsupported_share(bullet: str, entry: dict[str, Any]) -> float:
    """Share of a bullet's meaningful words that its entry's facts never use.

    Words are compared by their first five letters, so 'completion' matches
    'completed' and 'simulation' matches 'simulate'.
    """
    entry_words = set(re.findall(r"[a-z][a-z0-9+#]*", _entry_fact_text(entry).lower()))
    entry_stems = {w[:5] for w in entry_words if len(w) > 3}
    words = [
        w for w in re.findall(r"[a-z][a-z0-9+#]*", bullet.lower())
        if len(w) > 3 and w not in GENERIC_WORDS
    ]
    if not words:
        return 0.0
    missing = [w for w in words if w[:5] not in entry_stems]
    return len(missing) / len(words)


MAX_UNSUPPORTED_SHARE = 0.5


def _sanitize_ai_plan(
    data: dict[str, Any], profile: dict[str, Any], fallback: Plan
) -> Plan:
    corpus = _profile_corpus(profile)
    entries = profile_entries(profile)
    notes: list[str] = []

    def exact(names: Any, allowed: list[str]) -> list[str]:
        lookup = {a.lower(): a for a in allowed}
        out = []
        for name in names if isinstance(names, list) else []:
            match = lookup.get(str(name).strip().lower())
            if match and match not in out:
                out.append(match)
        return out

    umbc = primary_education(profile)
    hs = earlier_education(profile)
    coursework = (exact(data.get("coursework"), [c["name"] for c in umbc.get("coursework", [])]) or fallback.coursework)[:MAX_COURSES]
    honors = exact(data.get("high_school_honors"), [h["name"] for h in hs.get("honors", [])])
    skills = exact(data.get("skills"), [s["name"] for s in profile.get("skills", [])])[:MAX_SKILLS] or fallback.skills

    fallback_bullets = {
        e["id"]: e["bullets"] for s in fallback.sections for e in s["entries"]
    }
    profile_headings = [s["heading"] for s in profile.get("sections", [])]
    by_heading: dict[str, list[dict[str, Any]]] = {h: [] for h in profile_headings}
    seen: set[str] = set()
    # Keep the AI's section order, which reflects what it judged most relevant.
    headings: list[str] = []
    for section in data.get("sections", []) if isinstance(data.get("sections"), list) else []:
        name = str(section.get("heading", "")).strip().upper() if isinstance(section, dict) else ""
        match = next((h for h in profile_headings if h.upper() == name), None)
        if match and match not in headings:
            headings.append(match)
    headings += [h for h in profile_headings if h not in headings]
    headings = [h for h in headings if "LEADERSHIP" not in h.upper()] + [h for h in headings if "LEADERSHIP" in h.upper()]

    for section in data.get("sections", []) if isinstance(data.get("sections"), list) else []:
        for item in section.get("entries", []) if isinstance(section, dict) else []:
            entry_id = str(item.get("id", "")) if isinstance(item, dict) else ""
            entry = entries.get(entry_id)
            if entry is None or entry_id in seen:
                continue
            kept = []
            for bullet in item.get("bullets", [])[:3]:
                bullet = " ".join(str(bullet).split()).lstrip("-• ").strip()
                if not bullet:
                    continue
                if len(bullet) > MAX_BULLET_CHARS:
                    notes.append(f"dropped an over-long bullet for {entry_id}")
                    continue
                problems = ungrounded_terms(bullet, entry, corpus)
                if problems:
                    notes.append(f"rejected a bullet for {entry_id} mentioning {', '.join(problems)}")
                    continue
                claims = false_claims(bullet, entry)
                if claims:
                    notes.append(f"rejected a bullet for {entry_id} claiming {', '.join(claims)}")
                    continue
                if unsupported_share(bullet, entry) > MAX_UNSUPPORTED_SHARE:
                    notes.append(f"rejected a bullet for {entry_id} not clearly backed by its facts: {bullet[:60]}")
                    continue
                kept.append(bullet)
            if not kept:
                kept = fallback_bullets.get(entry_id, [])
                notes.append(f"used pre-written bullets for {entry_id}")
            heading = next(s["heading"] for s in profile["sections"] if entry in s["entries"])
            by_heading[heading].append({"id": entry_id, "bullets": kept})
            seen.add(entry_id)

    for required in required_entries(profile):
        if required not in seen and required in entries:
            heading = next(s["heading"] for s in profile["sections"] if entries[required] in s["entries"])
            by_heading[heading].append({"id": required, "bullets": fallback_bullets.get(required, [])})
            notes.append(f"added required entry {required}")

    sections = [{"heading": h, "entries": by_heading[h]} for h in headings if by_heading[h]]
    changes = str(data.get("changes", "")).strip()
    if changes:
        notes.insert(0, changes)
    return Plan(
        coursework=coursework,
        honors=honors,
        include_high_school=bool(honors),
        sections=sections,
        skills=skills,
        method="ai",
        notes=notes,
    )


def ai_plan(
    profile: dict[str, Any],
    job_text: str,
    title: str,
    employer: str,
    fallback: Plan,
    exe: str | None = None,
    timeout: int = 300,
) -> tuple[Plan | None, str]:
    """Ask Claude Code for a plan. Returns (plan or None, explanation)."""
    exe = exe or find_claude()
    if not exe:
        return None, "Claude Code was not found."
    command = [
        exe, "-p",
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--system-prompt", SYSTEM_PROMPT,
    ]
    try:
        done = subprocess.run(
            command,
            input=_ai_prompt(profile, job_text, title, employer),
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            cwd=str(HERE),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"Claude Code did not answer ({type(exc).__name__})."

    try:
        envelope = json.loads(done.stdout or "{}")
    except json.JSONDecodeError:
        return None, "Claude Code returned something unreadable."
    if envelope.get("is_error"):
        return None, f"Claude Code error: {str(envelope.get('result', ''))[:160]}"
    data = _extract_json(str(envelope.get("result", "")))
    if data is None:
        return None, "Claude Code's answer was not valid JSON."
    return _sanitize_ai_plan(data, profile, fallback), "tailored with Claude"


# --------------------------------------------------------------------- render


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].strip("-") or "employer"


def render_pdf(profile: dict[str, Any], plan: Plan, path: str | Path) -> Path:
    """Draw the plan in the original resume's format, shrinking until it fits one page."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as pdf_canvas

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = profile_entries(profile)
    width, height = letter

    # Working copy we can trim if the page overflows.
    sections = [
        {"heading": s["heading"], "entries": [dict(e, bullets=list(e["bullets"])) for e in s["entries"]]}
        for s in plan.sections
    ]
    honors = list(plan.honors)
    include_hs = plan.include_high_school and bool(honors)

    def layout(c: Any | None, size: float) -> float:
        margin_x, top, bottom = 54.0, 50.0, 46.0
        line = size * 1.22
        y = height - top
        usable = width - 2 * margin_x

        def text(x: float, y_pos: float, runs: list[tuple[str, str]], fsize: float) -> float:
            cursor = x
            for font, value in runs:
                if c is not None:
                    c.setFont(font, fsize)
                    c.drawString(cursor, y_pos, value)
                cursor += stringWidth(value, font, fsize)
            return cursor

        def wrapped(x: float, prefix: str, body: str, font: str, fsize: float) -> None:
            nonlocal y
            from reportlab.lib.utils import simpleSplit

            prefix_w = stringWidth(prefix, font, fsize)
            lines = simpleSplit(body, font, fsize, usable - (x - margin_x) - prefix_w)
            for i, piece in enumerate(lines):
                if c is not None:
                    c.setFont(font, fsize)
                    if i == 0 and prefix:
                        c.drawString(x, y, prefix)
                    c.drawString(x + prefix_w, y, piece)
                y -= line

        contact = profile["contact"]
        if c is not None:
            c.setFont("Times-Bold", size + 5)
            c.drawString(margin_x, y, contact["name"])
        y -= (size + 5) * 1.35
        linkedin = contact.get("linkedin", "").replace("https://www.", "").replace("https://", "")
        contact_line = " | ".join(v for v in [contact.get("location"), contact.get("phone"), contact.get("email"), linkedin] if v)
        text(margin_x, y, [("Times-Roman", contact_line)], size - 1)
        y -= line * 1.35

        def heading(label: str) -> None:
            nonlocal y
            y -= size * 0.25
            text(margin_x, y, [("Times-Bold", label)], size + 1.5)
            y -= line * 1.15

        # Education
        heading("EDUCATION")
        umbc = primary_education(profile)
        text(margin_x, y, [("Times-Bold", umbc["school"]), ("Times-Roman", f" — {umbc['standing']}, {umbc['degree']}")], size)
        y -= line
        indent = margin_x + 22
        text(indent, y, [("Times-Roman", f"Expected Graduation: {umbc['expected_graduation']} | GPA: {umbc['gpa']}")], size)
        y -= line
        if umbc.get("honors_line"):
            text(indent, y, [("Times-Italic", "Honors: "), ("Times-Roman", umbc["honors_line"])], size)
            y -= line
        label = "Relevant Coursework: "
        label_w = stringWidth(label, "Times-Italic", size)
        if c is not None:
            c.setFont("Times-Italic", size)
            c.drawString(indent, y, label)
        from reportlab.lib.utils import simpleSplit

        for i, piece in enumerate(simpleSplit(" / ".join(plan.coursework), "Times-Roman", size, usable - 22 - label_w)):
            if c is not None:
                c.setFont("Times-Roman", size)
                c.drawString(indent + label_w, y, piece)
            y -= line
        if include_hs:
            hs = earlier_education(profile)
            y -= size * 0.3
            text(margin_x, y, [("Times-Bold", hs["school"]), ("Times-Roman", f", {hs['degree']}")], size)
            y -= line
            for honor in honors:
                text(indent, y, [("Times-Roman", honor)], size)
                y -= line

        # Experience-style sections
        for section in sections:
            if not section["entries"]:
                continue
            heading(section["heading"])
            for item in section["entries"]:
                entry = entries[item["id"]]
                org, role, dates = entry["organization"], entry.get("role", ""), entry.get("dates", "")
                date_w = stringWidth(dates, "Times-Italic", size)
                head_w = stringWidth(org, "Times-Bold", size) + stringWidth(f" — {role}", "Times-Italic", size)
                if c is not None and dates:
                    c.setFont("Times-Italic", size)
                    c.drawRightString(width - margin_x, y, dates)
                if head_w + date_w + 12 <= usable:
                    text(margin_x, y, [("Times-Bold", org), ("Times-Italic", f" — {role}" if role else "")], size)
                    y -= line
                else:
                    text(margin_x, y, [("Times-Bold", org)], size)
                    y -= line
                    if role:
                        text(margin_x, y, [("Times-Italic", role)], size)
                        y -= line
                for bullet in item["bullets"]:
                    wrapped(indent, "-", bullet, "Times-Roman", size)
                y -= size * 0.35

        # Skills
        y -= size * 0.2
        skills_label = "Skills: "
        label_w = stringWidth(skills_label, "Times-Bold", size)
        if c is not None:
            c.setFont("Times-Bold", size)
            c.drawString(margin_x, y, skills_label)
        for piece in simpleSplit(", ".join(plan.skills), "Times-Roman", size, usable - label_w):
            if c is not None:
                c.setFont("Times-Roman", size)
                c.drawString(margin_x + label_w, y, piece)
            y -= line
        return y - bottom

    def trim_once() -> bool:
        """Remove the least valuable piece of content. False when nothing is left to cut."""
        nonlocal include_hs, honors
        all_entries = [(s, e) for s in sections for e in s["entries"]]
        for low_id in trim_first_entries(profile):
            for s, e in all_entries:
                if e["id"] == low_id:
                    s["entries"].remove(e)
                    return True
        if include_hs:
            if len(honors) > 1:
                honors.pop()
            else:
                include_hs = False
            return True
        candidates = [
            (len(e["bullets"]), -entries[e["id"]].get("priority", 5), s, e)
            for s, e in all_entries
            if len(e["bullets"]) > 1
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda t: (-t[0], -t[1]))
        candidates[0][3]["bullets"].pop()
        return True

    size = 11.0
    for _ in range(40):
        if layout(None, size) >= 0:
            break
        if size > 10.0:
            size -= 0.5
            continue
        if not trim_once():
            break

    c = pdf_canvas.Canvas(str(path), pagesize=letter)
    c.setTitle(f"{profile['contact']['name']} Resume")
    c.setAuthor(profile["contact"]["name"])
    layout(c, size)
    c.showPage()
    c.save()
    return path


# ------------------------------------------------------------------ top level


def resume_filename(profile: dict[str, Any]) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", profile["contact"]["name"]).strip("_") + "_Resume.pdf"


def tailor_resume(
    job_id: str,
    title: str,
    employer: str,
    job_text: str,
    *,
    use_ai: bool = True,
    profile_path: str | Path = PROFILE_PATH,
    out_dir: str | Path = OUT_DIR,
    reuse: bool = True,
) -> TailorResult:
    """Build (or reuse) the tailored resume PDF for one job."""
    profile = load_profile(profile_path)
    folder = Path(out_dir) / f"{job_id}-{_slug(employer)}"
    pdf_path = folder / resume_filename(profile)
    plan_path = folder / "plan.json"

    if reuse and pdf_path.exists() and plan_path.exists():
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        return TailorResult(pdf_path, saved.get("method", "saved"), ["reused the resume made earlier for this job"])

    fallback = rule_plan(profile, job_text, title)
    plan, reason = (None, "AI tailoring is turned off")
    if use_ai:
        plan, reason = ai_plan(profile, job_text, title, employer, fallback)
    if plan is None:
        plan = fallback
        plan.notes.append(reason)

    render_pdf(profile, plan, pdf_path)
    folder.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(dict(asdict(plan), job={"id": job_id, "title": title, "employer": employer}), indent=2),
        encoding="utf-8",
    )
    (folder / "job_posting.txt").write_text(f"{title}\n{employer}\n\n{job_text}", encoding="utf-8")
    return TailorResult(pdf_path, plan.method, plan.notes)
