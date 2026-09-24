"""Ranks every saved posting by how well it fits the student.

Each posting gets seven sub-scores between 0 and 1, read from its full text:

  skills     (30%)  the student's own skills and topics found in the posting,
                    weighted by rarity: a term most postings mention (Python)
                    counts less than one few do (Verilog). Title hits count
                    double. Normalized against the whole pool, so the scale
                    adapts to what's out there.
  role       (22%)  the kind of job, from the title: chip design, embedded,
                    hardware, robotics, security, software, test, data, IT,
                    mechanical, non-engineering. Ordered by the student's
                    stated preferences in the career profile.
  eligibility(18%)  undergraduate wording and class year help; PhD or
                    graduate-only, years of experience, senior titles, an
                    active clearance, or a GPA above the student's hurt.
  major match(10%)  the preset score the auto-applier uses.
  timing     (8%)   summer 2027 dates, undated, or another season.
  pay        (6%)   the listed hourly rate.
  location   (6%)   near home counts a little; the student is open to anywhere.

The weighted sum is out of 100. A posting that is clearly the wrong kind of
job (non-engineering) or that the student can't get (graduate-only) is then
scaled down, so strong keywords can't rescue it. Postings whose deadline has
passed are left out, and those closing within 10 days are flagged.

Finally the list is spread across employers: at most `per_company` postings
from any one company (TikTok, for example, lists dozens), and never two with
the same job under different team names.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import ranking
from resume_parser import normalize

WEIGHTS = {
    "skills": 0.30,
    "role": 0.22,
    "eligibility": 0.18,
    "major": 0.10,
    "timing": 0.08,
    "pay": 0.06,
    "location": 0.06,
}

# (family, how the title shows it). First match wins, so specific beats general.
FAMILIES = [
    ("chip design", r"\b(asic|fpga|rtl|vlsi|silicon|soc|ic design|digital design|design verification|"
                    r"verification engineer|physical design|analog ic|mixed[- ]signal|layout|semiconductor|chip)\b"),
    ("embedded", r"\b(embedded|firmware|microcontroller|bsp|device driver|real[- ]time)\b"),
    ("hardware", r"\b(hardware|electrical|electronics?|pcb|power electronics|rf|circuit|ee)\b"),
    ("robotics", r"\b(robotics?|autonomy|autonomous|controls?|mechatronics|automation)\b"),
    ("security", r"\b(security|cyber|vulnerability|reverse engineering|penetration)\b"),
    ("software", r"\b(software|backend|back[- ]end|frontend|front[- ]end|full[- ]?stack|developer|swe|devops|"
                 r"site reliability|platform|systems? engineer|programmer|application)\b"),
    ("test", r"\b(test|validation|quality assurance|qa|reliability)\b"),
    ("data", r"\b(data|machine learning|ml|ai|artificial intelligence|analytics|computer vision)\b"),
    ("it", r"\b(information technology|it|network|systems? admin|help ?desk|technical support|infrastructure)\b"),
    ("mechanical", r"\b(mechanical|manufacturing|quality|process|industrial|civil|structural|product design)\b"),
    ("non-engineering", r"\b(marketing|sales|social media|product manager|business|finance|accounting|"
                        r"human resources|recruit|graphic|content|communications|operations specialist)\b"),
]
DEFAULT_FAMILY_WEIGHT = 0.45

UNDERGRAD = re.compile(
    r"\b(undergrad(uate)?s?|sophomores?|juniors?|rising (junior|senior)s?|bachelor'?s|currently (enrolled|pursuing)|"
    r"entry[- ]level|no (prior )?experience (is )?(required|necessary|needed))\b", re.I)
CLASS_YEAR = re.compile(r"\b(class of 2028|graduat\w* (in |by )?(spring |may |december |fall )?(2027|2028)|"
                        r"expected graduation (date )?(of |in |between )?.{0,20}(2027|2028))\b", re.I)
GRAD_ONLY = re.compile(r"\b(ph\.?\s?d|doctoral|postdoc|mba|(master'?s|graduate) (students?|candidates?) only|"
                       r"pursuing an? (master'?s|ph\.?d|graduate degree)|enrolled in an? (master'?s|ph\.?d|graduate) program)\b", re.I)
EXPERIENCE = re.compile(r"\b([3-9]|1\d)\+?\s*(or more\s*)?years?\s+(of\s+)?(professional |relevant |industry |work )?experience\b", re.I)
SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of)\b", re.I)
STUDENT_TITLE = re.compile(r"\b(intern|internship|co-?op|student|trainee|apprentice)\b", re.I)
ACTIVE_CLEARANCE = re.compile(r"\b(active|current)\b[^.]{0,40}\b(secret|ts/sci|top secret|clearance)\b", re.I)
GPA = re.compile(r"\b(?:gpa|grade point average)[^0-9]{0,30}([2-4]\.\d{1,2})|\b([2-4]\.\d{1,2})\s*/\s*4\.0", re.I)

SUMMER = re.compile(r"\b(summer|may|june|july|august)\b[^.\n]{0,40}\b2027\b|\b2027\b[^.\n]{0,20}\bsummer\b", re.I)
OTHER_SEASON = re.compile(r"\b(spring|fall|autumn|winter)\s+(20\d\d|semester|co-?op)\b|\bco-?op\b", re.I)

PAY = re.compile(r"\$\s?(\d{2,3}(?:\.\d+)?)(?:\s*[–-]\s*\$?\s?(\d{2,3}(?:\.\d+)?))?\s*/\s*hr", re.I)
PAY_YEAR = re.compile(r"\$\s?(\d{2,3}),?(\d{3})(?:\s*[–-]\s*\$?\s?(\d{2,3}),?(\d{3}))?\s*/\s*yr", re.I)
DEADLINE = re.compile(r"Apply by (\w+ \d{1,2}, \d{4})", re.I)

HOME = re.compile(r"\b(MD|Maryland|Baltimore|Columbia|Towson|Annapolis|DC|Washington, DC|VA|Virginia|Arlington|"
                  r"Reston|Herndon|McLean|Fairfax|Chantilly)\b")
REMOTE = re.compile(r"\bremote\b", re.I)

COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|plc|lp|llp|group|holdings|"
    r"usa|us|u\.s|america|north america|the)\b\.?", re.I)
TEAM_PART = re.compile(r"\s*[\(\[].*?[\)\]]|\s+-\s+.*$|\s*–\s*.*$|\(#?[\w-]+\)|#\w[\w-]*")


@dataclass
class Graded:
    job_id: str
    title: str
    employer: str
    location: str
    url: str
    score: float
    parts: dict[str, float]
    family: str
    matched: list[str]
    pay: str = ""
    deadline: str = ""
    closing_soon: bool = False
    flags: list[str] = field(default_factory=list)
    more_at_company: int = 0


def body_of(description: str) -> str:
    """The posting itself, without Handshake's 'About the employer' and similar-job extras."""
    for marker in ("\nAbout the employer", "\nSimilar Jobs", "\nAlumni in similar roles"):
        cut = description.find(marker)
        if cut != -1:
            description = description[:cut]
    return description


def family_of(title: str) -> str:
    for name, pattern in FAMILIES:
        if re.search(pattern, title, re.I):
            return name
    return "other"


def family_weights(profile: dict[str, Any]) -> dict[str, float]:
    """Preference per job family, led by the student's own target roles."""
    weights = {
        "chip design": 0.85, "embedded": 0.85, "hardware": 0.85, "robotics": 0.8, "security": 0.72,
        "software": 0.7, "test": 0.64, "data": 0.6, "it": 0.5, "mechanical": 0.35, "non-engineering": 0.08,
        "other": DEFAULT_FAMILY_WEIGHT,
    }
    targets = " ".join(str(t) for t in (profile.get("preferences", {}) or {}).get("target_roles", [])).lower()
    for family, cue in (("embedded", "embedded"), ("hardware", "hardware"), ("chip design", "chip"),
                        ("robotics", "robot"), ("software", "software"), ("data", "data")):
        if cue in targets:
            weights[family] = 1.0
    return weights


def company_key(employer: str) -> str:
    name = normalize(COMPANY_SUFFIXES.sub(" ", employer or ""))
    return " ".join(name.split()) or (employer or "").lower()


def title_key(title: str) -> str:
    """The job, without team names, requisition numbers or 'Summer 2027'."""
    core = TEAM_PART.sub("", title or "")
    core = re.sub(r"\b(20\d\d|summer|fall|spring|winter|intern(ship)?s?|co-?op)\b", " ", core, flags=re.I)
    return " ".join(normalize(core).split())


def _student_gpa(profile: dict[str, Any]) -> float | None:
    for school in profile.get("education", []):
        try:
            return float(str(school.get("gpa", "")).strip())
        except ValueError:
            continue
    return None


def eligibility(title: str, text: str, gpa: float | None) -> tuple[float, list[str]]:
    score, flags = 0.7, []
    if UNDERGRAD.search(text) or STUDENT_TITLE.search(title):
        score += 0.2
    if CLASS_YEAR.search(text):
        score += 0.1
        flags.append("your class year")
    if GRAD_ONLY.search(f"{title}\n{text}"):
        score -= 0.5
        flags.append("graduate or PhD")
    if EXPERIENCE.search(text):
        score -= 0.3
        flags.append("wants years of experience")
    if SENIOR.search(title) and not STUDENT_TITLE.search(title):
        score -= 0.4
        flags.append("senior title")
    if ACTIVE_CLEARANCE.search(text):
        score -= 0.35
        flags.append("needs an active clearance")
    if gpa is not None:
        wanted = [float(a or b) for a, b in GPA.findall(text) if (a or b)]
        if wanted and max(wanted) > gpa:
            score -= 0.5
            flags.append(f"asks for a {max(wanted):.1f}+ GPA")
    return max(0.0, min(score, 1.0)), flags


def timing(title: str, text: str) -> float:
    head = f"{title}\n{text[:3000]}"
    if SUMMER.search(head):
        return 1.0
    if OTHER_SEASON.search(head):
        return 0.3
    return 0.75


def pay(text: str) -> tuple[float, str]:
    match = PAY.search(text)
    if match:
        low = float(match.group(1))
        high = float(match.group(2) or low)
        rate = (low + high) / 2
        label = f"${match.group(1)}–{match.group(2)}/hr" if match.group(2) else f"${match.group(1)}/hr"
    else:
        year = PAY_YEAR.search(text)
        if not year:
            return 0.5, ""
        rate = float(year.group(1) + year.group(2)) / 2080
        label = f"${year.group(1)},{year.group(2)}/yr"
    # 15/hr scores 0.2, 25 about 0.55, 40 or more 1.0, on a smooth curve.
    return max(0.1, min(1.0, 0.2 + 0.8 * (rate - 15) / 25)), label


def location(where: str) -> float:
    if HOME.search(where or ""):
        return 1.0
    if REMOTE.search(where or ""):
        return 0.85
    return 0.65


def deadline(text: str, today: date) -> tuple[date | None, str]:
    match = DEADLINE.search(text)
    if not match:
        return None, ""
    try:
        when = datetime.strptime(match.group(1), "%B %d, %Y").date()
    except ValueError:
        return None, ""
    return when, when.strftime("%b %d").replace(" 0", " ")


def grade_all(
    postings: list[dict[str, Any]],
    profile: dict[str, Any],
    terms: list[str],
    today: date | None = None,
) -> list[Graded]:
    """Score every posting. Expired ones are dropped; nothing is capped yet."""
    today = today or date.today()
    fam_weight = family_weights(profile)
    gpa = _student_gpa(profile)

    bodies = [normalize(body_of(p.get("description", ""))) for p in postings]
    titles = [normalize(p.get("title", "")) for p in postings]
    count = len(postings)

    # Rarity of each of the student's terms across the whole pool.
    doc_freq = {t: sum(1 for b, ti in zip(bodies, titles) if f" {t} " in f" {ti} {b} ") for t in terms}
    idf = {t: math.log((count + 1) / (df + 1)) + 1 for t, df in doc_freq.items()}

    raw_skill: list[tuple[float, list[str]]] = []
    for body, title in zip(bodies, titles):
        padded_title, padded_all = f" {title} ", f" {title} {body} "
        hits = [t for t in terms if f" {t} " in padded_all]
        weight = sum(idf[t] * (2 if f" {t} " in padded_title else 1) for t in hits)
        raw_skill.append((weight, sorted(hits, key=lambda t: -idf[t])))
    positive = sorted(w for w, _ in raw_skill if w > 0)
    # The 75th percentile of the pool maps to about 0.78: saturating, not linear.
    scale = positive[int(len(positive) * 0.75)] if positive else 1.0

    graded: list[Graded] = []
    for posting, (weight, hits) in zip(postings, raw_skill):
        text = body_of(posting.get("description", ""))
        title = posting.get("title", "")
        due, due_label = deadline(posting.get("description", ""), today)
        if due is not None and due < today:
            continue
        if posting.get("apply_kind") in ("closed", "already_applied"):
            continue

        family = family_of(title)
        elig, flags = eligibility(title, text, gpa)
        pay_score, pay_label = pay(posting.get("description", ""))
        parts = {
            "skills": 1 - math.exp(-1.5 * weight / scale) if scale else 0.0,
            "role": fam_weight.get(family, DEFAULT_FAMILY_WEIGHT),
            "eligibility": elig,
            "major": max(0.0, min(float(posting.get("score", 0.5) or 0.0), 1.0)),
            "timing": timing(title, text),
            "pay": pay_score,
            "location": location(posting.get("location", "")),
        }
        score = 100 * sum(WEIGHTS[k] * v for k, v in parts.items())
        if family == "non-engineering":
            score *= 0.6
        if elig < 0.3:
            score *= 0.7
        if parts["timing"] <= 0.3:  # a spring or fall co-op doesn't fit a summer search
            score *= 0.8
            flags.append("not a summer internship")
        closing = due is not None and (due - today).days <= 10
        if closing:
            flags.append("closes soon")
        graded.append(Graded(
            job_id=posting["job_id"], title=title, employer=posting.get("employer", ""),
            location=posting.get("location", ""), url=posting.get("url", ""), score=round(score, 1),
            parts={k: round(v, 2) for k, v in parts.items()}, family=family, matched=hits[:6],
            pay=pay_label, deadline=due_label, closing_soon=closing, flags=flags,
        ))
    graded.sort(key=lambda g: -g.score)
    return graded


def spread(graded: list[Graded], per_company: int = 2) -> list[Graded]:
    """Keep the best `per_company` distinct jobs from each employer."""
    kept: list[Graded] = []
    by_company: dict[str, list[Graded]] = {}
    skipped: dict[str, int] = {}
    for item in graded:
        key = company_key(item.employer)
        chosen = by_company.setdefault(key, [])
        if len(chosen) >= per_company or any(title_key(c.title) == title_key(item.title) for c in chosen):
            skipped[key] = skipped.get(key, 0) + 1
            continue
        chosen.append(item)
        kept.append(item)
    for item in kept:
        item.more_at_company = skipped.get(company_key(item.employer), 0)
    return kept


def student_terms(resume: Any, profile_path: Any) -> list[str]:
    return ranking.load_resume_terms(resume, profile_path)
