"""Orders the "apply to yourself" list so the best openings come first.

The auto-applier keeps using the major's preset score (matcher.score_posting).
Many good postings hit 100% there, so this list adds a finer "fit" score out of
100 built from four parts:

  preset match    up to 30  the same major score the applier uses
  resume overlap  up to 35  skills and topics from the resume and career profile
                            that the posting mentions (title hits count double)
  student level   up to 25  undergraduate-friendly wording scores high; PhD,
                            graduate-only, senior titles or years of experience
                            score low
  title fit       up to 10  the title names the major or one of its title words
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from resume_parser import normalize

MATCH_POINTS = 30.0
RESUME_POINTS = 35.0
LEVEL_POINTS = 25.0
TITLE_POINTS = 10.0
RESUME_HITS_FOR_FULL = 8.0

LIST_LIMIT = 50

# Words from profile tags that say nothing about a posting when they appear.
GENERIC_TERMS = {
    "communication", "teamwork", "leadership", "customer service", "sales", "work",
    "team", "research", "projects", "project", "business", "service", "support",
    "design", "engineering", "technical", "problem solving", "hands on", "school",
    "students", "education", "c", "r", "go", "data", "computer", "test", "testing",
    "analysis", "control", "lab", "network", "collaboration", "logic", "power",
    "marketing", "customer", "signal", "assembly", "programming", "digital",
    "communication skills", "writing", "management", "operations", "science",
    "math", "physics", "teaching", "tutoring", "retail", "restaurant", "server",
    "entrepreneurship", "small business", "inventory", "finance", "accounting",
    "mechanical", "manufacturing", "prototype", "simulation", "scripting", "automation",
    "protocol", "can", "chip", "circuit", "electrical", "repair", "spring",
    "systems", "process", "growth", "initiative", "detail", "responsibility",
    "organization", "events", "event planning", "presentation", "integration",
    "iteration", "materials", "outreach", "diversity", "coordination", "mentoring",
    "stem", "technician", "discrete", "electricity", "magnetism", "controls", "battery",
    "web", "website", "technical communication", "troubleshooting",
}

UNDERGRAD_PATTERNS = [
    r"\bundergrad(uate)?s?\b", r"\bsophomores?\b", r"\bjuniors?\b", r"\bfreshm[ae]n\b",
    r"\brising (sophomore|junior|senior)s?\b", r"\bbachelor'?s (degree )?(student|candidate|program)s?\b",
    r"\bcurrently (enrolled|pursuing)\b", r"\bclass of 20\d\d\b", r"\bno (prior )?experience (is )?(required|necessary|needed)\b",
    r"\bentry[- ]level\b",
]
STUDENT_TITLE = re.compile(r"\b(intern|internship|co-?op|student|trainee|apprentice|entry[- ]level)\b", re.I)
GRAD_ONLY_PATTERNS = [
    r"\bph\.?\s?d\b", r"\bdoctoral\b", r"\bpostdoc", r"\bmba\b",
    r"\b(master'?s|graduate) (students?|candidates?) only\b", r"\bpursuing an? (master'?s|ph\.?d|graduate degree)\b",
    r"\benrolled in an? (master'?s|ph\.?d|graduate) program\b",
]
EXPERIENCE_PATTERN = re.compile(r"\b([3-9]|1\d)\+?\s*(or more\s*)?years?\s+(of\s+)?(professional |relevant |industry |work )?experience\b", re.I)
SENIOR_TITLE = re.compile(r"\b(senior|sr\.?|staff|principal|lead|manager|director|head of)\b", re.I)


@dataclass
class Fit:
    points: float
    why: list[str] = field(default_factory=list)


def resume_terms(resume: Any = None, career_profile: dict[str, Any] | None = None) -> list[str]:
    """Skills and topics the student actually has, from the resume and profile."""
    terms: list[str] = []
    if resume is not None:
        terms += list(getattr(resume, "skills", []) or []) + list(getattr(resume, "phrases", []) or [])
    profile = career_profile or {}
    for skill in profile.get("skills", []) or []:
        if isinstance(skill, dict):
            terms += [skill.get("name", "")] + list(skill.get("tags", []) or [])
        else:
            terms.append(str(skill))
    for school in profile.get("education", []) or []:
        for course in school.get("coursework", []) or []:
            if isinstance(course, dict):
                terms += list(course.get("tags", []) or [])
    for section in profile.get("sections", []) or []:
        for entry in section.get("entries", []) or []:
            terms += list(entry.get("skills", []) or [])
            for bullet in entry.get("bullets", []) or []:
                if isinstance(bullet, dict):
                    terms += list(bullet.get("tags", []) or [])
    cleaned: list[str] = []
    for term in terms:
        norm = normalize(str(term))
        if norm and norm not in GENERIC_TERMS and norm not in cleaned:
            cleaned.append(norm)
    return cleaned


def load_resume_terms(resume: Any, profile_path: str | Path | None) -> list[str]:
    career: dict[str, Any] = {}
    if profile_path:
        try:
            import json

            career = json.loads(Path(profile_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            career = {}
    return resume_terms(resume, career)


def _has(padded: str, term: str) -> bool:
    return f" {term} " in padded


def _any(patterns: Iterable[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def student_level(title: str, text: str) -> tuple[float, list[str]]:
    """Points out of LEVEL_POINTS for how open the posting is to undergraduates."""
    points = LEVEL_POINTS / 2
    why: list[str] = []
    if STUDENT_TITLE.search(title):
        points += 5
    if _any(UNDERGRAD_PATTERNS, f"{title}\n{text}"):
        points += 7.5
        why.append("open to undergraduates")
    if _any(GRAD_ONLY_PATTERNS, f"{title}\n{text}"):
        points -= 10
        why.append("asks for grad or PhD students")
    if EXPERIENCE_PATTERN.search(text):
        points -= 7.5
        why.append("wants years of experience")
    if SENIOR_TITLE.search(title) and not STUDENT_TITLE.search(title):
        points -= 12.5
        why.append("senior-level title")
    return max(0.0, min(points, LEVEL_POINTS)), why


def fit(title: str, text: str, match_score: float, terms: list[str], major: Any = None) -> Fit:
    padded_title = f" {normalize(title)} "
    padded_all = f" {normalize(f'{title} {text}')} "

    points = MATCH_POINTS * max(0.0, min(float(match_score), 1.0))
    why: list[str] = []

    in_title = [t for t in terms if _has(padded_title, t)]
    anywhere = [t for t in terms if t not in in_title and _has(padded_all, t)]
    hits = 2 * len(in_title) + len(anywhere)
    points += RESUME_POINTS * min(hits / RESUME_HITS_FOR_FULL, 1.0)
    if in_title or anywhere:
        why.insert(0, "matches your resume: " + ", ".join((in_title + anywhere)[:5]))

    level, level_why = student_level(title, text)
    points += level
    why += level_why

    title_words = list(getattr(major, "anchors", []) or []) + list(getattr(major, "title_words", []) or [])
    if in_title or any(_has(padded_title, normalize(w)) for w in title_words if normalize(w)):
        points += TITLE_POINTS

    return Fit(points=round(points, 1), why=why)
