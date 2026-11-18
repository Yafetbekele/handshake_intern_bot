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

Job keywords (add_keywords=True), for resumes the student sends himself: the
posting's own tools and skills are added to the skills line, and bullets may
use the posting's terms, even where the profile doesn't back them. Numbers,
employers, titles, dates and the never-claim list stay checked as always.
Every keyword the profile doesn't back is listed in "CHECK BEFORE SENDING.txt"
beside the resume. These resumes go to their own folder, which the automatic
appliers never read, so nothing unchecked is ever sent for him.
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
# Resumes with the job's keywords added. Kept apart: the appliers only read OUT_DIR.
KEYWORD_DIR = Path(os.environ.get("HSBOT_KEYWORD_DIR") or HERE / "tailored_resumes_with_job_keywords")
CHECK_FILE = "CHECK BEFORE SENDING.txt"
MAX_ADDED_KEYWORDS = 8

MAX_BULLETS = {"default": 3, "low": 2}
MAX_SKILLS = 12
MAX_COURSES = 6
MAX_BULLET_CHARS = 200  # two full lines of the page
COURSE_ROWS = 2  # lines the coursework may take under Education


@dataclass
class Plan:
    coursework: list[str]
    honors: list[str]
    include_high_school: bool
    sections: list[dict[str, Any]]
    skills: list[str]
    method: str
    notes: list[str] = field(default_factory=list)
    added_keywords: list[str] = field(default_factory=list)  # on the page, not backed by the profile


@dataclass
class TailorResult:
    path: Path
    method: str
    notes: list[str]
    added: list[str] = field(default_factory=list)  # keywords to check before sending


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


def on_resume(entry: dict[str, Any]) -> bool:
    """False for what the student keeps off his resume ("on_resume": false). It stays in the profile for forms."""
    return entry.get("on_resume", True) is not False


def earlier_education(profile: dict[str, Any]) -> dict[str, Any]:
    """An earlier school worth a line for its honors, if any."""
    return next((e for e in profile.get("education", []) if e.get("honors") and on_resume(e)), {})


def profile_entries(profile: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["id"]: e for s in profile.get("sections", []) for e in s.get("entries", []) if on_resume(e)}


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


# -------------------------------------------------------------- job keywords

# Tools and skills computer engineering postings ask for, written the way a
# resume lists them, each with the pattern that finds it in a posting.
JOB_KEYWORDS: list[tuple[str, str]] = [
    ("SystemVerilog", r"system\s?verilog"), ("UVM", r"\buvm\b"), ("Verilog", r"\bverilog\b"),
    ("VHDL", r"\bvhdl\b"), ("RTL Design", r"\brtl\b"), ("FPGA", r"\bfpgas?\b"), ("ASIC", r"\basics?\b"),
    ("SoC", r"system[- ]on[- ](a[- ])?chip|\bsocs?\b(?! analyst)"), ("Static Timing Analysis", r"static timing"),
    ("Design Verification", r"design verification"), ("Formal Verification", r"formal verification"),
    ("DFT", r"\bdft\b|design for test"), ("Physical Design", r"physical design"),
    ("Cadence", r"\bcadence\b"), ("Synopsys", r"\bsynopsys\b"), ("Vivado", r"\bvivado\b"),
    ("Quartus", r"\bquartus\b"), ("SPICE", r"\b(lt|h|ng)?spice\b"), ("PCB Design", r"\bpcbs?\b|printed circuit"),
    ("Altium", r"\baltium\b"), ("KiCad", r"\bkicad\b"), ("Oscilloscopes", r"oscilloscope"),
    ("Logic Analyzers", r"logic analy[sz]er"), ("Signal Integrity", r"signal integrity"),
    ("Power Electronics", r"power electronics"), ("Analog Circuit Design", r"\banalog (circuit|design)"),
    ("Mixed-Signal Design", r"mixed[- ]signal"), ("RF", r"\brf\b|radio frequency"),
    ("DSP", r"\bdsp\b|digital signal processing"), ("Embedded C", r"embedded c\b|\bc/c\+\+|\bc and c\+\+|\bc programming"),
    ("C++", r"c\+\+"), ("Python", r"\bpython\b"), ("Rust", r"\brust\b"), ("Assembly", r"\bassembly language|\bassembly\b(?! line)"),
    ("Perl", r"\bperl\b"), ("Tcl", r"\btcl\b"), ("Bash Scripting", r"\bbash\b|shell script"),
    ("RTOS", r"\brtos\b|real[- ]time operating"), ("FreeRTOS", r"freertos"), ("Zephyr RTOS", r"\bzephyr\b"),
    ("Embedded Linux", r"embedded linux"), ("Linux Kernel", r"linux kernel|kernel (development|modules?|drivers?)"),
    ("Device Drivers", r"device drivers?|driver development"), ("Yocto", r"\byocto\b"),
    ("Firmware Development", r"\bfirmware\b"), ("Bootloaders", r"bootloader"),
    ("ARM", r"\barm (cortex|processors?|architecture|cores?|based)|cortex-[amr]\d*|\barmv\d"),
    ("RISC-V", r"risc-?v"), ("x86", r"\bx86\b"),
    ("Computer Architecture", r"computer architecture|cpu architecture|micro-?architecture"),
    ("GPU", r"\bgpus?\b"), ("CUDA", r"\bcuda\b"), ("OpenCL", r"\bopencl\b"),
    ("I2C", r"\bi2c\b"), ("SPI", r"\bspi\b"), ("UART", r"\buart\b"), ("USB", r"\busb\b"),
    ("PCIe", r"\bpci-?e\b|pci express"), ("Ethernet", r"\bethernet\b"), ("TCP/IP", r"tcp/ip"),
    ("Bluetooth Low Energy", r"bluetooth|\bble\b"), ("Wi-Fi", r"\bwi-?fi\b|802\.11"),
    ("CAN Bus", r"\bcan[- ]?bus\b|\bcan (protocol|network)|\bj1939\b"), ("Modbus", r"\bmodbus\b"),
    ("PLC Programming", r"\bplcs?\b"), ("LabVIEW", r"\blabview\b"), ("MATLAB", r"\bmatlab\b"),
    ("Simulink", r"\bsimulink\b"), ("Git", r"\bgit\b|github|gitlab"), ("CI/CD", r"\bci/cd\b|continuous integration"),
    ("Jenkins", r"\bjenkins\b"), ("Docker", r"\bdocker\b"), ("Kubernetes", r"\bkubernetes\b"),
    ("AWS", r"\baws\b|amazon web services"), ("Azure", r"\bazure\b"), ("SQL", r"\bsql\b"),
    ("Java", r"\bjava\b(?!script)"), ("JavaScript", r"\bjavascript\b"), ("TypeScript", r"\btypescript\b"),
    ("React", r"\breact(\.js|js)?\b"), ("REST APIs", r"\brest(ful)? apis?\b|\brestful\b"),
    ("Machine Learning", r"machine learning"), ("PyTorch", r"\bpytorch\b"), ("TensorFlow", r"\btensorflow\b"),
    ("Computer Vision", r"computer vision"), ("OpenCV", r"\bopencv\b"), ("ROS", r"\bros ?2?\b"),
    ("Control Systems", r"control systems|controls engineering|\bpid control"),
    ("Sensor Integration", r"\bsensors?\b"), ("Unit Testing", r"unit test"),
    ("Test Automation", r"test automation|automated test"), ("Agile", r"\bagile\b|\bscrum\b"),
    ("JIRA", r"\bjira\b"), ("Data Structures and Algorithms", r"data structures"),
    ("Object-Oriented Programming", r"object[- ]oriented"), ("Linux", r"\blinux\b"),
    ("Networking", r"\bnetworking\b"), ("Cybersecurity", r"cyber ?security"),
]


def _flat(text: str) -> str:
    """Words only, for comparing terms: normalize() keeps dots, so 'Linux.' would not match 'Linux'."""
    return " ".join(re.findall(r"[a-z0-9+#]+", normalize(text)))


def job_keywords(job_text: str, title: str = "") -> list[str]:
    """The posting's keywords from JOB_KEYWORDS, most mentioned first (the title counts three times)."""
    found = []
    for order, (name, pattern) in enumerate(JOB_KEYWORDS):
        hits = len(re.findall(pattern, job_text, re.I)) + 3 * len(re.findall(pattern, title, re.I))
        if hits:
            first = min((m.start() for m in re.finditer(pattern, f"{title}\n{job_text}", re.I)), default=0)
            found.append((-hits, first, order, name))
    return [name for *_, name in sorted(found)]


def _profile_facts(profile: dict[str, Any]) -> str:
    """The profile's own wording (skills, courses, entries), never its tags, as ' word word '."""
    parts = [s.get("name", "") for s in profile.get("skills", [])]
    parts += [c.get("name", "") for c in primary_education(profile).get("coursework", [])]
    parts += [_entry_fact_text(e) for e in profile_entries(profile).values()]
    return f" {_flat(' '.join(str(p) for p in parts))} "


def backed_by_profile(term: str, facts: str) -> bool:
    words = _flat(term)
    return bool(words) and f" {words} " in facts


def with_job_keywords(plan: Plan, profile: dict[str, Any], job_text: str, title: str,
                      suggested: list[str] | None = None) -> Plan:
    """Put the posting's keywords on the skills line, and note the ones the profile doesn't back.

    Skills the profile has that the posting names go first, then the added
    keywords, then the rest. plan.added_keywords lists everything on the page,
    skills or bullets, that needs the student's check.
    """
    have = {s["name"].lower() for s in profile.get("skills", [])}
    named = job_keywords(job_text, title)
    job_norm = f" {_flat(f'{title} {job_text}')} "
    wanted: list[str] = []
    for name in list(suggested or []) + named:
        name = " ".join(str(name).split()).strip(" ,.;:")
        if not name or len(name) > 40 or name.lower() in have or name.lower() in {w.lower() for w in wanted}:
            continue
        # A suggestion has to be the posting's own word; a known keyword was found by its pattern.
        if name not in named and f" {_flat(name)} " not in job_norm:
            continue
        wanted.append(name)
    wanted = wanted[:MAX_ADDED_KEYWORDS]
    mentioned = {n.lower() for n in named}
    first = [s for s in plan.skills if s.lower() in mentioned]
    rest = [s for s in plan.skills if s.lower() not in mentioned]
    plan.skills = (first + wanted + rest)[: MAX_SKILLS + 4]
    facts = _profile_facts(profile)
    unbacked = [w for w in wanted if w in plan.skills and not backed_by_profile(w, facts)]
    plan.added_keywords = list(dict.fromkeys(unbacked + list(plan.added_keywords)))
    if wanted:
        plan.notes.append(f"added the posting's keywords: {', '.join(wanted)}")
    return plan


def check_list(plan: Plan, title: str, employer: str) -> str:
    """What CHECK BEFORE SENDING.txt says."""
    lines = [f"Resume for: {title} at {employer}", ""]
    if not plan.added_keywords:
        lines.append("Nothing was added beyond what your profile backs up. It's ready to send.")
        return "\n".join(lines) + "\n"
    lines += [
        "These keywords come from the posting, and your profile doesn't back them.",
        "Check each one before you send this. Take out anything you can't talk about",
        "in an interview (edit the PDF, or make the resume again after updating your",
        "profile in profile/career_profile.json).",
        "",
    ]
    skills = [k for k in plan.added_keywords if k in plan.skills]
    if skills:
        lines.append("Added to your skills line:")
        lines += [f"  - {k}" for k in skills]
        lines.append("")
    bullets = []
    for section in plan.sections:
        for entry in section["entries"]:
            for bullet in entry["bullets"]:
                terms = [k for k in plan.added_keywords if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", bullet, re.I)]
                if terms:
                    bullets.append(f'  - "{bullet}"\n      adds: {", ".join(terms)}')
    if bullets:
        lines.append("Bullets that use them:")
        lines += bullets
    return "\n".join(lines).rstrip() + "\n"


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
            if not on_resume(entry):
                continue
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
    local = os.environ.get("LOCALAPPDATA", "")
    # The Microsoft Store version of the Claude app keeps its files in a
    # redirected folder that programs started outside the app can't see at
    # the usual path.
    homes = [os.path.join(appdata, "Claude", "claude-code")]
    homes += glob.glob(os.path.join(local, "Packages", "Claude_*", "LocalCache", "Roaming", "Claude", "claude-code"))
    # Since the app's late-October 2026 update its files are in Local\Claude-Data\claude_<id>.
    homes += glob.glob(os.path.join(local, "Claude-Data", "*", "claude-code"))
    candidates = []
    for home in homes:
        # <version>\claude.exe, or since late 2026 <version>\<build>\claude.exe.
        candidates += glob.glob(os.path.join(home, "*", "claude.exe"))
        candidates += glob.glob(os.path.join(home, "*", "*", "claude.exe"))
    candidates += glob.glob(os.path.join(os.path.expanduser("~"), ".local", "bin", "claude.exe"))

    def version_key(p: str) -> tuple[int, ...]:
        folders = Path(p).parts
        version = folders[folders.index("claude-code") + 1] if "claude-code" in folders else Path(p).parent.name
        return tuple(int(x) for x in re.findall(r"\d+", version))

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
        # Quotes around the path stop PowerShell running it, so they're only used when a space needs them.
        command = f"{exe} auth login" if " " not in str(exe) else f'& "{exe}" auth login'
        return False, f"Claude Code is not signed in. Run once, in PowerShell:  {command}"
    return True, "Claude Code is signed in."


SYSTEM_PROMPT = """You tailor a student's resume to one job posting.

Absolute rules:
- Use ONLY facts in the student's profile. Never add a number, tool, language, technology, employer, result, responsibility or claim that is not stated there.
- You may reword, shorten, combine two facts from the same entry, reorder, and choose what to include.
- You may use the job's vocabulary only when a profile fact genuinely supports it. Example: the profile says "C++ and Verilog on FPGA", so "FPGA development" is fine; "SystemVerilog" or "ASIC tape-out" is not.
- Keep every date, title, name and figure exactly as the profile states it.
- Reply with one JSON object only. No commentary, no code fences."""

# For resumes the student checks and sends himself: the posting's keywords may go in.
KEYWORD_SYSTEM_PROMPT = """You tailor a student's resume to one job posting, working the posting's keywords in so it gets past keyword screening. The student checks every added keyword before sending.

Rules:
- You may add tools, languages and skills the posting asks for, even ones the profile doesn't mention: on the skills line ("added_skills"), and in bullets where they fit what the student actually did.
- Use only keywords that appear in the job posting. Never invent other tools.
- What the student did, where, and when stays as the profile states it: never add a number, employer, job title, date, award, result or responsibility that isn't in the profile, and never change one.
- Each bullet must still be mostly about its own entry's facts, with the posting's keywords worked into it.
- Reply with one JSON object only. No commentary, no code fences."""


def _ai_prompt(profile: dict[str, Any], job_text: str, title: str, employer: str, keywords: bool = False) -> str:
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
    extra = ""
    if keywords:
        schema["added_skills"] = [f"up to {MAX_ADDED_KEYWORDS} tools or skills named in the posting that the "
                                  "profile's skills lack, written as a resume lists them, most important first"]
        extra = ("- Work the posting's most important keywords into the bullets where they fit the work described. "
                 "Every number, name, title and date stays exactly as the profile has it.\n")
    # Saved form answers are over half the profile and never go on a resume,
    # so they stay out of the request.
    resume_facts = {k: v for k, v in profile.items() if k not in ("application_answers", "_answers_note")}
    return (
        f"JOB: {title} at {employer}\n\n"
        f"JOB POSTING:\n{job_text[:7000]}\n\n"
        f"STUDENT PROFILE (the only allowed source of facts):\n{json.dumps(resume_facts, ensure_ascii=False, separators=(',', ':'))}\n\n"
        "TASK: Choose and word the resume content that best fits this job.\n"
        + extra +
        f"- Always include entries {', '.join(required_entries(profile)) or '(none required)'}. Include the others only if they help.\n"
        f"- At most 3 bullets per entry and about 13 bullets in total, each under {MAX_BULLET_CHARS} characters, starting with a strong verb.\n"
        "- A line of the page holds about 100 characters. Size each bullet to end on a full line: one line "
        "(75 to 98 characters) or two (165 to 195). Never leave one to four words alone on a second line.\n"
        "- No two bullets of an entry may make the same point.\n"
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


def _without_job_terms(bullet: str, job_norm: str) -> str:
    """The bullet with the posting's own terms that hold digits (I2C, x86, Cortex-M4) blanked out,
    so their digits aren't mistaken for invented numbers."""
    def blank(match: re.Match[str]) -> str:
        word = match.group(0)
        return " " if re.search(r"\d", word) and f" {_flat(word)} " in job_norm else word
    return re.sub(r"[A-Za-z0-9+#./-]*[A-Za-z][A-Za-z0-9+#./-]*", blank, bullet)


def keyword_bullet_ok(bullet: str, entry: dict[str, Any], job_text: str) -> bool:
    """With job keywords allowed: a bullet still has to be about its own entry.

    At least 40% of its meaningful words come from the entry's facts, and at
    most a quarter come from neither the entry nor the posting.
    """
    def stems(text: str) -> set[str]:
        return {w[:5] for w in re.findall(r"[a-z][a-z0-9+#]*", text.lower()) if len(w) > 3}

    entry_stems, job_stems = stems(_entry_fact_text(entry)), stems(job_text)
    words = [w for w in re.findall(r"[a-z][a-z0-9+#]*", bullet.lower()) if len(w) > 3 and w not in GENERIC_WORDS]
    if not words:
        return True
    from_entry = sum(1 for w in words if w[:5] in entry_stems)
    from_nowhere = sum(1 for w in words if w[:5] not in entry_stems and w[:5] not in job_stems)
    return from_entry >= 0.4 * len(words) and from_nowhere <= 0.25 * len(words)


def _sanitize_ai_plan(
    data: dict[str, Any], profile: dict[str, Any], fallback: Plan, job_text: str | None = None, title: str = ""
) -> Plan:
    """Keep only what the profile backs. With job_text (job keywords on), the posting's
    own terms may go in too, and each one the profile doesn't back is noted."""
    keywords = job_text is not None
    job_norm = f" {_flat(job_text or '')} "
    corpus = _profile_corpus(profile)
    entries = profile_entries(profile)
    notes: list[str] = []
    added_terms: list[str] = []

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
                numbers = [p for p in problems if NUMBER_RE.fullmatch(p)]
                terms = [p for p in problems if p not in numbers]
                if keywords and numbers:
                    left = NUMBER_RE.findall(_without_job_terms(bullet, job_norm))
                    numbers = [n for n in numbers if n in left]
                # With job keywords on, a term is fine if the posting uses it; numbers never are.
                outside = [t for t in terms if not keywords or f" {_flat(t)} " not in job_norm]
                if numbers or outside:
                    notes.append(f"rejected a bullet for {entry_id} mentioning {', '.join(numbers + outside)}")
                    continue
                claims = false_claims(bullet, entry)
                if claims:
                    notes.append(f"rejected a bullet for {entry_id} claiming {', '.join(claims)}")
                    continue
                if keywords:
                    if not keyword_bullet_ok(bullet, entry, job_text or ""):
                        notes.append(f"rejected a bullet for {entry_id} straying from its facts: {bullet[:60]}")
                        continue
                elif unsupported_share(bullet, entry) > MAX_UNSUPPORTED_SHARE:
                    notes.append(f"rejected a bullet for {entry_id} not clearly backed by its facts: {bullet[:60]}")
                    continue
                if any(_same_point(bullet, earlier) for earlier in kept):
                    notes.append(f"dropped a bullet for {entry_id} repeating another: {bullet[:60]}")
                    continue
                kept.append(bullet)
                added_terms += terms
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
    plan = Plan(
        coursework=coursework,
        honors=honors,
        include_high_school=bool(honors),
        sections=sections,
        skills=skills,
        method="ai",
        notes=notes,
        added_keywords=list(dict.fromkeys(added_terms)),
    )
    if keywords:
        suggested = data.get("added_skills") if isinstance(data.get("added_skills"), list) else []
        plan = with_job_keywords(plan, profile, job_text or "", title, [str(s) for s in suggested])
    return plan


def ai_plan(
    profile: dict[str, Any],
    job_text: str,
    title: str,
    employer: str,
    fallback: Plan,
    exe: str | None = None,
    timeout: int = 300,
    keywords: bool = False,
) -> tuple[Plan | None, str]:
    """Ask Claude Code for a plan. Returns (plan or None, explanation)."""
    data, reason = ask_claude_json(_ai_prompt(profile, job_text, title, employer, keywords),
                                   KEYWORD_SYSTEM_PROMPT if keywords else SYSTEM_PROMPT, exe, timeout)
    if data is None:
        return None, reason
    return (_sanitize_ai_plan(data, profile, fallback, job_text if keywords else None, title),
            "tailored with Claude" + (", with the posting's keywords" if keywords else ""))


def ask_claude_json(
    prompt: str, system_prompt: str, exe: str | None = None, timeout: int = 300, model: str | None = None
) -> tuple[dict[str, Any] | None, str]:
    """One tool-less Claude Code call on the student's plan that answers with JSON."""
    exe = exe or find_claude()
    if not exe:
        return None, "Claude Code was not found."
    command = [
        exe, "-p",
        "--output-format", "json",
        "--tools", "",
        "--no-session-persistence",
        "--strict-mcp-config",
        "--system-prompt", system_prompt,
    ] + (["--model", model] if model else [])
    try:
        done = subprocess.run(
            command,
            input=prompt,
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
    return data, "ok"


# --------------------------------------------------------------------- render


def _slug(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:limit].strip("-") or "employer"


def _same_point(a: str, b: str) -> bool:
    """Whether two bullets say the same thing, so a reworded one isn't repeated.

    Either most of their words are shared, or they share a run of three words that carry
    the point ("built an electric longboard", "expanding into computer repairs").
    """
    wa, wb = _point_words(a), _point_words(b)
    if not wa or not wb:
        return a.strip().lower() == b.strip().lower()
    if len(wa & wb) / min(len(wa), len(wb)) >= 0.5:
        return True
    return bool(_point_runs(a) & _point_runs(b))


def _point_runs(text: str) -> set[tuple[str, ...]]:
    words = [w for w in (_point_word(t) for t in re.findall(r"[a-z0-9+#]+", text.lower())) if w]
    return {tuple(words[i:i + 3]) for i in range(len(words) - 2)}


_LINKING_WORDS = {"with", "from", "that", "this", "into", "around", "using", "their", "each", "also", "through",
                  "while", "over", "about", "them", "were", "have", "been", "which"}


def _point_words(text: str) -> set[str]:
    """The words that carry a bullet's point: no linking words, and "motors" the same as "motor"."""
    return {w for w in (_point_word(t) for t in re.findall(r"[a-z0-9+#]+", text.lower())) if w}


def _point_word(word: str) -> str:
    """"applying" the same as "apply", "systems" as "system"."""
    if len(word) <= 3 or word in _LINKING_WORDS:
        return ""
    for ending in ("ing", "ed", "es", "s"):
        if word.endswith(ending) and not word.endswith("ss") and len(word) - len(ending) >= 4:
            return word[: -len(ending)]
    return word


def course_title(name: str) -> tuple[str, bool]:
    """A course as the resume shows it, and whether it's still being taken.

    "CMPE 310 (Systems Design and Programming, in progress)" is
    ("Systems Design and Programming", True): the title says what was learned,
    the catalog number doesn't, and the line has room for more of them.
    """
    text = str(name).strip()
    current = bool(re.search(r"\bin progress\b", text, re.IGNORECASE))
    inner = re.match(r"^[A-Z]{2,5}\s*[\d/]+[A-Z]?\s*\((.+)\)\s*$", text)
    title = inner.group(1) if inner else re.sub(r"\s*\([^)]*\)\s*$", "", text)
    title = re.sub(r",?\s*in progress\b", "", title, flags=re.IGNORECASE).strip(" ,")
    return title or text, current


def coursework_line(courses: list[str]) -> str:
    """The coursework as one run of text; courses still being taken are starred, and the star explained."""
    shown = [course_title(name) for name in courses]
    line = ", ".join(title + ("*" if current else "") for title, current in shown)
    return line + (" (*in progress)" if any(current for _, current in shown) else "")


def render_pdf(
    profile: dict[str, Any], plan: Plan, path: str | Path, job_text: str = "", title: str = "", fill: bool = True
) -> Path:
    """Draw the plan in the original resume's format on exactly one page.

    With `fill`, empty space at the bottom is filled with more of the profile's
    own content, most relevant to the job first: further bullets, experiences
    the plan left out, high school honors, coursework and skills.
    """
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas as pdf_canvas

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries = profile_entries(profile)
    width, height = letter

    # Working copy we can grow to fill the page, or trim if it overflows.
    sections = [
        {"heading": s["heading"], "entries": [dict(e, bullets=list(e["bullets"])) for e in s["entries"]]}
        for s in plan.sections
    ]
    honors = list(plan.honors)
    include_hs = plan.include_high_school and bool(honors)
    coursework = list(plan.coursework)
    skills = list(plan.skills)

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
            room = usable - (x - margin_x) - prefix_w
            lines = simpleSplit(body, font, fsize, room)
            squeeze = 100
            if len(lines) > 1 and stringWidth(lines[-1], font, fsize) < room * 0.15:
                # A word or two alone on the last line: the letters are set a hair narrower
                # (at most 3%, not visible) to bring them back up.
                for percent in (99, 98, 97):
                    tighter = simpleSplit(body, font, fsize, room * 100 / percent)
                    if len(tighter) < len(lines):
                        lines, squeeze = tighter, percent
                        break
            for i, piece in enumerate(lines):
                if c is not None:
                    c.setFont(font, fsize)
                    if i == 0 and prefix:
                        c.drawString(x, y, prefix)
                    if squeeze == 100:
                        c.drawString(x + prefix_w, y, piece)
                    else:
                        run = c.beginText(x + prefix_w, y)
                        run.setFont(font, fsize)
                        run.setHorizScale(squeeze)
                        run.textOut(piece)
                        c.drawText(run)
                y -= line

        contact = profile["contact"]
        if c is not None:
            c.setFont("Times-Bold", size + 5)
            c.drawString(margin_x, y, contact["name"])
        y -= (size + 5) * 1.35
        linkedin = contact.get("linkedin", "").replace("https://www.", "").replace("https://", "")
        github = contact.get("github", "").replace("https://www.", "").replace("https://", "")
        contact_line = " | ".join(v for v in [contact.get("location"), contact.get("phone"), contact.get("email"), linkedin,
                                              github] if v)
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

        for i, piece in enumerate(simpleSplit(coursework_line(coursework), "Times-Roman", size, usable - 22 - label_w)):
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

        # Skills, near the top where a recruiter sees them first
        if skills:
            heading("SKILLS")
            for piece in simpleSplit(", ".join(skills), "Times-Roman", size, usable):
                if c is not None:
                    c.setFont("Times-Roman", size)
                    c.drawString(margin_x, y, piece)
                y -= line
            y -= size * 0.2

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
        return y - bottom

    def additions() -> list[tuple[float, str, Any]]:
        """Everything in the profile not on the page yet, most useful first."""
        job_norm, job_tokens = _job_text(job_text, title)
        shown = {e["id"]: e for s in sections for e in s["entries"]}
        found: list[tuple[float, str, Any]] = []
        for section in profile.get("sections", []):
            for entry in section.get("entries", []):
                if not on_resume(entry):
                    continue
                for order, bullet in enumerate(entry.get("bullets", [])):
                    text = bullet["text"] if isinstance(bullet, dict) else str(bullet)
                    tags = bullet.get("tags", []) if isinstance(bullet, dict) else []
                    on_page = shown.get(entry["id"], {}).get("bullets", [])
                    if any(_same_point(text, existing) for existing in on_page):
                        continue
                    score = _tag_score(tags, job_norm, job_tokens) + entry.get("priority", 5) * 0.3 - order * 0.05
                    if entry.get("trim_first"):
                        score -= 5
                    found.append((10 + score, "bullet", (section["heading"], entry["id"], text)))
        if not include_hs or len(honors) < len(earlier_education(profile).get("honors", [])):
            for honor in earlier_education(profile).get("honors", []):
                if honor["name"] not in honors:
                    found.append((5, "honor", honor["name"]))
        for course in primary_education(profile).get("coursework", []):
            if course["name"] not in coursework:
                found.append((3 + _tag_score(course.get("tags", []), job_norm, job_tokens) * 0.1, "course", course["name"]))
        for skill in profile.get("skills", []):
            if skill["name"] not in skills:
                found.append((2 + _tag_score(skill.get("tags", []), job_norm, job_tokens) * 0.1, "skill", skill["name"]))
        found.sort(key=lambda t: -t[0])
        return found

    def add(kind: str, value: Any) -> Any:
        """Put one addition on the page; returns what undo() needs."""
        nonlocal include_hs
        if kind == "bullet":
            heading_name, entry_id, text = value
            for section in sections:
                for item in section["entries"]:
                    if item["id"] == entry_id:
                        item["bullets"].append(text)
                        return ("bullet", item)
            target = next((s for s in sections if s["heading"] == heading_name), None)
            if target is None:  # a section the plan left out goes last
                target = {"heading": heading_name, "entries": []}
                sections.append(target)
            item = {"id": entry_id, "bullets": [text]}
            target["entries"].append(item)
            return ("entry", target, item)
        if kind == "honor":
            was = include_hs
            include_hs = True
            honors.append(value)
            return ("honor", was)
        (coursework if kind == "course" else skills).append(value)
        return (kind,)

    def undo(record: Any) -> None:
        nonlocal include_hs
        if record[0] == "bullet":
            record[1]["bullets"].pop()
        elif record[0] == "entry":
            record[1]["entries"].remove(record[2])
            if not record[1]["entries"]:
                sections.remove(record[1])
        elif record[0] == "honor":
            honors.pop()
            include_hs = record[1]
        elif record[0] == "course":
            coursework.pop()
        else:
            skills.pop()

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

    def course_rows() -> int:
        from reportlab.lib.utils import simpleSplit

        room = width - 108 - 22 - stringWidth("Relevant Coursework: ", "Times-Italic", size)
        return len(simpleSplit(coursework_line(coursework), "Times-Roman", size, room))

    def lone_skill() -> bool:
        """True when the skills line ends with one skill alone on its last line."""
        from reportlab.lib.utils import simpleSplit

        rows = simpleSplit(", ".join(skills), "Times-Roman", size, width - 108)
        return len(rows) > 1 and "," not in rows[-1]

    planned_skills = len(skills)  # the plan's own skills; the rest are page-filling extras
    while len(coursework) > 3 and course_rows() > COURSE_ROWS:
        coursework.pop()  # the least relevant; the space is worth more to the experience below
    if fill and layout(None, size) >= 0:
        # Fill the rest of the page: most relevant first, skipping anything
        # that would spill onto a second page.
        for _score, kind, value in additions():
            record = add(kind, value)
            if (
                layout(None, size) < 0
                or (kind == "course" and course_rows() > COURSE_ROWS)
                or (kind == "skill" and lone_skill())
            ):
                undo(record)
    for _ in range(40):
        if layout(None, size) >= 0:
            break
        if size > 10.0:
            size -= 0.5
            continue
        if not trim_once():
            break
    # A smaller size can still leave one skill alone: drop the lowest-ranked
    # extras (never one the plan chose) until the line reflows.
    while lone_skill() and len(skills) > planned_skills:
        skills.pop()

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
    out_dir: str | Path | None = None,
    reuse: bool = True,
    add_keywords: bool = False,
) -> TailorResult:
    """Build (or reuse) the tailored resume PDF for one job.

    add_keywords: work the posting's keywords in even where the profile doesn't
    back them (only for resumes the student checks and sends himself). These go
    to KEYWORD_DIR with a CHECK BEFORE SENDING.txt beside them.
    """
    profile = load_profile(profile_path)
    folder = Path(out_dir or (KEYWORD_DIR if add_keywords else OUT_DIR)) / f"{job_id}-{_slug(employer)}"
    pdf_path = folder / resume_filename(profile)
    plan_path = folder / "plan.json"

    if reuse and pdf_path.exists() and plan_path.exists():
        saved = json.loads(plan_path.read_text(encoding="utf-8"))
        # One the rules made while Claude couldn't be used is made again properly now that it can,
        # and one made with the keyword setting the other way round is made again too.
        if (use_ai and saved.get("method") in ("rules", "ai-earlier")) or bool(saved.get("job_keywords")) != add_keywords:
            saved = {}
        elif saved.get("layout") == LAYOUT_VERSION:
            return TailorResult(pdf_path, saved.get("method", "saved"), ["reused the resume made earlier for this job"],
                                list(saved.get("added_keywords", [])))
        # Made with an older layout: keep its choices, draw it again the current way.
        known = {f for f in Plan.__dataclass_fields__}
        try:
            plan = Plan(**{k: v for k, v in saved.items() if k in known}) if saved else None
        except TypeError:
            plan = None
        if plan is not None:
            _save_resume(profile, plan, folder, pdf_path, plan_path, job_id, title, employer, job_text, add_keywords)
            return TailorResult(pdf_path, "saved", ["redrew the resume made earlier for this job to fill the page"],
                                plan.added_keywords)

    fallback = rule_plan(profile, job_text, title)
    plan, reason = (None, "AI tailoring is turned off")
    if use_ai:
        plan, reason = ai_plan(profile, job_text, title, employer, fallback, keywords=add_keywords)
    if plan is None:
        plan = fallback
        plan.notes.append(reason)
        if add_keywords:
            earlier = _earlier_ai_plan(Path(OUT_DIR) / folder.name, profile, profile_path)
            if earlier is not None:  # Claude's checked version from before beats the rules' one
                plan = earlier
                plan.notes.append("started from the resume Claude tailored for this job earlier")
            plan = with_job_keywords(plan, profile, job_text, title)

    _save_resume(profile, plan, folder, pdf_path, plan_path, job_id, title, employer, job_text, add_keywords)
    return TailorResult(pdf_path, plan.method, plan.notes, plan.added_keywords)


def _earlier_ai_plan(folder: Path, profile: dict[str, Any], profile_path: str | Path) -> Plan | None:
    """The plan Claude made for this job before, without added keywords, if there is one
    made since the profile last changed.

    Marked "ai-earlier" so a later run with Claude makes a proper keyword version."""
    plan_file = folder / "plan.json"
    try:
        if plan_file.stat().st_mtime < Path(profile_path).stat().st_mtime:
            return None  # made from an older profile
        saved = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if saved.get("method") != "ai" or saved.get("job_keywords"):
        return None
    entries = profile_entries(profile)
    if any(e.get("id") not in entries for s in saved.get("sections", []) for e in s.get("entries", [])):
        return None
    known = {f for f in Plan.__dataclass_fields__}
    try:
        plan = Plan(**{k: v for k, v in saved.items() if k in known})
    except TypeError:
        return None
    plan.method, plan.notes, plan.added_keywords = "ai-earlier", [], []
    return plan


# Bump when the resume layout changes, so resumes made earlier are redrawn.
LAYOUT_VERSION = 3  # 2: skills near the top, page filled. 3: coursework by title, no stray last words


def _save_resume(
    profile: dict[str, Any], plan: Plan, folder: Path, pdf_path: Path, plan_path: Path,
    job_id: str, title: str, employer: str, job_text: str, keywords: bool = False,
) -> None:
    render_pdf(profile, plan, pdf_path, job_text=job_text, title=title)
    folder.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(dict(asdict(plan), layout=LAYOUT_VERSION, job_keywords=keywords,
                        job={"id": job_id, "title": title, "employer": employer}), indent=2),
        encoding="utf-8",
    )
    if keywords:
        (folder / CHECK_FILE).write_text(check_list(plan, title, employer), encoding="utf-8")
    (folder / "job_posting.txt").write_text(f"{title}\n{employer}\n\n{job_text}", encoding="utf-8")
