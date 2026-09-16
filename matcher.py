"""Score a job posting against the student's major and resume.

The score is a transparent weighted-overlap number, not a black box: every
match is reported so the student can see why a posting ranked where it did.

Weights
    major keyword present in resume too : 3.0
    major keyword                       : 2.0
    resume skill or topic               : 1.5
    frequent resume word                : 1.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from resume_parser import ResumeProfile, normalize

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


@dataclass
class MatchResult:
    score: float
    matched: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def percent(self) -> int:
        return round(self.score * 100)


def build_weights(
    profile: ResumeProfile | None,
    major_keywords: list[str],
    extra_keywords: list[str] | None = None,
) -> dict[str, float]:
    """Combine major keywords, resume signal and user extras into one vocabulary."""
    weights: dict[str, float] = {}
    resume_terms: set[str] = set()

    if profile is not None:
        resume_terms |= set(profile.token_counts)
        resume_terms |= set(profile.skills)
        resume_terms |= set(profile.phrases)

    for keyword in major_keywords:
        term = keyword.lower().strip()
        if not term:
            continue
        # A major keyword the resume also backs up is the strongest signal.
        weights[term] = 3.0 if term in resume_terms else 2.0

    for keyword in extra_keywords or []:
        term = keyword.lower().strip()
        if term:
            weights[term] = max(weights.get(term, 0.0), 3.0)

    if profile is not None:
        for skill in profile.skills:
            weights[skill] = max(weights.get(skill, 0.0), 1.5)
        for phrase in profile.phrases:
            weights[phrase] = max(weights.get(phrase, 0.0), 1.5)
        # Frequent resume words fill in domain vocabulary the lists missed.
        for token, count in profile.token_counts.most_common(60):
            if count < 2:
                continue
            weights.setdefault(token, 1.0)

    return weights


TITLE_BONUS = 0.25

# Words too generic to say which field a job title belongs to.
GENERIC_TITLE_WORDS = {
    "intern", "interns", "internship", "internships", "summer", "co", "op", "coop",
    "engineering", "engineer", "science", "sciences", "studies", "management",
    "administration", "systems", "analyst", "associate", "program", "and", "of",
    "the", "for", "in", "assistant", "research", "student", "services", "general",
}


def title_terms_for(major_name: str, queries: list[str]) -> list[str]:
    """Field words to look for in job titles, from the major and its searches.

    Electrical Engineering with searches like "firmware intern" gives
    ["electrical", "hardware", "embedded", "firmware"].
    """
    terms: list[str] = []
    for phrase in [major_name, *queries]:
        for word in re.findall(r"[a-z][a-z&+#]*", phrase.lower()):
            if len(word) > 2 and word not in GENERIC_TITLE_WORDS and word not in terms:
                terms.append(word)
    return terms


def score_job(
    job_text: str,
    weights: dict[str, float],
    title: str = "",
    title_terms: list[str] | tuple[str, ...] = (),
) -> MatchResult:
    """Fraction of the weighted vocabulary that the posting mentions, plus a
    fixed bonus when the job title itself names the student's field."""
    result = _vocabulary_score(job_text, weights)
    if title and title_terms:
        title_words = set(re.findall(r"[a-z][a-z&+#]*", title.lower()))
        hits = [t for t in title_terms if t in title_words]
        if hits:
            result.score = min(result.score + TITLE_BONUS, 1.0)
            result.reasons.append(f"title mentions {', '.join(hits)}")
    return result


def _vocabulary_score(job_text: str, weights: dict[str, float]) -> MatchResult:
    if not weights:
        return MatchResult(score=0.0)

    haystack = normalize(job_text)
    haystack_tokens = set(haystack.split())

    total = sum(weights.values())
    earned = 0.0
    matched: list[str] = []

    for term, weight in weights.items():
        if " " in term:
            hit = term in haystack
        else:
            hit = term in haystack_tokens
        if hit:
            earned += weight
            matched.append(term)

    matched.sort(key=lambda t: -weights[t])
    score = earned / total if total else 0.0
    return MatchResult(score=min(score, 1.0), matched=matched)


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


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
