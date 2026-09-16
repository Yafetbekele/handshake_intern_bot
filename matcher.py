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


def score_job(job_text: str, weights: dict[str, float]) -> MatchResult:
    """Fraction of the weighted vocabulary that the posting mentions."""
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


def is_summer(title: str, body: str = "") -> bool:
    if _matches_any(title, SUMMER_PATTERNS):
        return True
    return _matches_any(body[:6000], SUMMER_PATTERNS)


def location_ok(job_location: str, wanted: list[str]) -> bool:
    """True when no preference is set, the job is remote, or a preference matches."""
    if not wanted:
        return True
    haystack = job_location.lower()
    if "remote" in haystack:
        return True
    return any(w.lower().strip() in haystack for w in wanted if w.strip())
