"""Decide whether a job posting fits the student's major.

Postings are scored against a fixed preset per major (see majors.json), never
against the resume, so the same major always means the same thing. Every
match is reported so the student can see why a posting ranked where it did.

How the score is built, from 0 to 100%:

* Anchor: the major's own name, such as "computer engineering".
  In the job title it scores 100%. Anywhere in the posting, at least 90%.
* Content: core terms are worth 3 points, related terms 1 point.
  12 points earn the full 75% for content, so four core terms are enough.
* Title: a field word in the job title, such as "FPGA", adds 25%.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from resume_parser import normalize

if TYPE_CHECKING:
    from majors import MajorProfile

SUMMER_PATTERNS = [
    r"\bsummer\b",
    r"\bsummer\s*20\d\d\b",
    r"\bjune\b.*\baugust\b",
    r"\bmay\b.*\baugust\b",
]

INTERN_PATTERNS = [
    r"\bintern\b",
    r"\binternship\b",
    r"\bco[\s\-]?op\b",
    r"\bsummer analyst\b",
    r"\bsummer associate\b",
]

# Postings that say "intern" but are not what a student wants.
NEGATIVE_TITLE_PATTERNS = [
    r"\binternal\b",
    r"\bintern\s*supervisor\b",
    r"\bmanager of interns\b",
]

CORE_POINTS = 3.0
RELATED_POINTS = 1.0
FULL_CONTENT_POINTS = 12.0
CONTENT_SHARE = 0.75
TITLE_BONUS = 0.25
ANCHOR_IN_TITLE = 1.0
ANCHOR_IN_POSTING = 0.90

# Pickiness levels offered in the launcher, as minimum scores.
STRICTNESS = {
    "broad": 0.15,
    "balanced": 0.25,
    "strict": 0.40,
}


@dataclass
class MatchResult:
    score: float
    matched: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        return round(self.score * 100)


def _padded(text: str) -> str:
    return f" {normalize(text)} "


def _has_term(padded_text: str, term: str) -> bool:
    """Whole-word, case-insensitive match on normalized text."""
    norm = normalize(term)
    return bool(norm) and f" {norm} " in padded_text


def passes(result: MatchResult, min_score: float) -> bool:
    """Compare on the same rounded percentage the student sees."""
    return result.percent >= round(min_score * 100)


def score_posting(title: str, text: str, major: "MajorProfile") -> MatchResult:
    """Score one posting against a major's fixed preset."""
    padded_title = _padded(title)
    padded_all = _padded(f"{title} {text}")

    core_hits = [t for t in dict.fromkeys(major.core) if _has_term(padded_all, t)]
    related_hits = [t for t in dict.fromkeys(major.related) if _has_term(padded_all, t) and t not in core_hits]
    title_hits = [t for t in dict.fromkeys(major.title_words) if _has_term(padded_title, t)]
    anchors_in_title = [a for a in major.anchors if _has_term(padded_title, a)]
    anchors_anywhere = [a for a in major.anchors if _has_term(padded_all, a)]

    points = CORE_POINTS * len(core_hits) + RELATED_POINTS * len(related_hits)
    score = min(points / FULL_CONTENT_POINTS, 1.0) * CONTENT_SHARE
    reasons: list[str] = []
    if title_hits:
        score += TITLE_BONUS
        reasons.append(f"title mentions {', '.join(title_hits[:3])}")
    if anchors_in_title:
        score = max(score, ANCHOR_IN_TITLE)
        reasons.insert(0, f"title names the major: {anchors_in_title[0]}")
    elif anchors_anywhere:
        score = max(score, ANCHOR_IN_POSTING)
        reasons.insert(0, f"posting names the major: {anchors_anywhere[0]}")

    matched = list(dict.fromkeys(anchors_anywhere + core_hits + related_hits))
    return MatchResult(score=min(score, 1.0), matched=matched, reasons=reasons)


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


def is_internship_posting(title: str, body: str = "") -> bool:
    """For job searches: is this posting an internship rather than a job?

    Only the title and Handshake's own job type line ("Internship" under At a
    glance) count, since job descriptions often mention internships in passing.
    """
    if _matches_any(title, NEGATIVE_TITLE_PATTERNS):
        return False
    if _matches_any(title, INTERN_PATTERNS):
        return True
    return re.search(r"(?:^|\n)\s*internship\s*(?:\n|$)", body[:2500], re.IGNORECASE) is not None


def is_internship(title: str, body: str = "") -> bool:
    if _matches_any(title, NEGATIVE_TITLE_PATTERNS):
        return False
    if _matches_any(title, INTERN_PATTERNS):
        return True
    # Some employers only say it in the body or the job-type field.
    return _matches_any(body[:4000], INTERN_PATTERNS)


MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9, "oct": 10,
    "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"

# "From May 30, 2027 to August 7, 2027" (job panel) and "May 29—Aug 6" (result card).
DATE_RANGE_PATTERNS = [
    re.compile(rf"\bfrom\s+{_MONTH}\.?\s+\d{{1,2}}(?:,?\s*\d{{4}})?\s+to\s+{_MONTH}\b", re.IGNORECASE),
    re.compile(rf"\b{_MONTH}\.?\s+\d{{1,2}}\s*[—–-]+\s*{_MONTH}\.?\s+\d{{1,2}}\b", re.IGNORECASE),
]

OTHER_SEASON_TITLE = re.compile(r"\b(fall|autumn|spring|winter)\b", re.IGNORECASE)
OTHER_SEASON_BODY = re.compile(
    r"\b(fall|autumn|spring|winter)\s+(20\d\d|semester|term|co-?op|internship|session|quarter)\b",
    re.IGNORECASE,
)


def summer_status(title: str, body: str = "") -> str:
    """Classify a posting's timing as 'summer', 'other' or 'unknown'.

    Explicit dates win. Then the title, then the description. Postings that
    never say when they run come back 'unknown' rather than being guessed.
    """
    text = body[:8000] if body else ""
    for pattern in DATE_RANGE_PATTERNS:
        match = pattern.search(title) or pattern.search(text)
        if match:
            start = MONTHS.get(match.group(1).lower().rstrip("."), 0)
            end = MONTHS.get(match.group(2).lower().rstrip("."), 0)
            return "summer" if 5 <= start <= 7 and 6 <= end <= 9 else "other"

    if _matches_any(title, SUMMER_PATTERNS):
        return "summer"
    if OTHER_SEASON_TITLE.search(title):
        return "other"
    if _matches_any(text, SUMMER_PATTERNS):
        return "summer"
    if OTHER_SEASON_BODY.search(text):
        return "other"
    return "unknown"


def is_summer(title: str, body: str = "", include_undated: bool = True) -> bool:
    """True for summer postings, and for undated ones unless told otherwise."""
    status = summer_status(title, body)
    return status == "summer" or (status == "unknown" and include_undated)


def location_ok(job_location: str, wanted: list[str]) -> bool:
    """True when no preference is set, the job is remote, or a preference matches."""
    if not wanted:
        return True
    haystack = job_location.lower()
    if "remote" in haystack:
        return True
    return any(w.lower().strip() in haystack for w in wanted if w.strip())
