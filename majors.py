"""Majors: what to search for on Handshake, and the fixed terms postings are scored on.

Everything lives in majors.json so it can be read and edited without touching
code. Each major has:

* queries      typed into Handshake's job search, one search each
* anchors      the major's own name ("computer engineering"); a posting that says
               it almost always passes
* core         strong signs of the field, worth the most
* related      supporting terms, worth less
* title_words  words that earn a boost when they appear in a job title

Unknown majors fall back to a profile built from the major's own words, so
typing anything at all still produces a usable search.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

MAJORS_FILE = Path(__file__).resolve().parent / "majors.json"


@dataclass
class MajorProfile:
    name: str
    queries: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    anchors: list[str] = field(default_factory=list)
    core: list[str] = field(default_factory=list)
    related: list[str] = field(default_factory=list)
    title_words: list[str] = field(default_factory=list)
    job_queries: list[str] = field(default_factory=list)

    @property
    def keywords(self) -> list[str]:
        """Every scoring term, strongest first."""
        return list(dict.fromkeys(self.anchors + self.core + self.related))


def load_majors(path: Path = MAJORS_FILE) -> list[MajorProfile]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read {path.name}: {exc}")
    majors = []
    for raw in data.get("majors", []):
        majors.append(
            MajorProfile(
                name=str(raw["name"]),
                queries=[str(q) for q in raw.get("queries", [])],
                aliases=[str(a) for a in raw.get("aliases", [])],
                anchors=[str(a) for a in raw.get("anchors", [])] or [str(raw["name"]).lower()],
                core=[str(t) for t in raw.get("core", [])],
                related=[str(t) for t in raw.get("related", [])],
                title_words=[str(t) for t in raw.get("title_words", [])],
                job_queries=[str(q) for q in raw.get("job_queries", [])],
            )
        )
    return majors


INTERN_WORDS = re.compile(r"\b(summer\s+)?(intern|interns|internship|internships|co-?op)\b", re.IGNORECASE)


def queries_for(major: MajorProfile, looking_for: str) -> list[str]:
    """Searches to run: the internship ones, or job ones for job searches.

    A major without its own job searches uses its internship searches with
    "intern" taken out, so "psychology intern" becomes "psychology".
    """
    if looking_for != "jobs":
        return list(major.queries) or [f"{major.name} intern"]
    if major.job_queries:
        return list(major.job_queries)
    stripped = [" ".join(INTERN_WORDS.sub(" ", q).split()) for q in major.queries]
    return list(dict.fromkeys(q for q in stripped if q)) or [major.name.lower()]


MAJORS: list[MajorProfile] = load_majors()


def list_majors() -> list[str]:
    return [m.name for m in MAJORS]


def _normalize(value: str) -> str:
    return " ".join(value.lower().replace("&", " and ").split())


def _contains_phrase(haystack: str, needle: str) -> bool:
    """Whole-word containment, so 'ba' never matches inside 'basket'."""
    if not needle:
        return False
    return re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", haystack) is not None


def resolve(major_input: str) -> MajorProfile:
    """Find the closest known major, or build a generic profile from the input."""
    query = _normalize(major_input)
    if not query:
        raise ValueError("No major provided")

    # Exact name or alias hit.
    for profile in MAJORS:
        if query == _normalize(profile.name):
            return profile
        if any(query == _normalize(a) for a in profile.aliases):
            return profile

    # Very short aliases such as "cs" or "ba" only count as exact matches above;
    # otherwise they fire inside unrelated words.
    candidates: list[tuple[str, MajorProfile]] = []
    for profile in MAJORS:
        for candidate in [profile.name, *profile.aliases]:
            norm = _normalize(candidate)
            if len(norm) >= 4:
                candidates.append((norm, profile))

    # A longer name inside what was typed wins: "electrical engineering"
    # beats a bare "engineering" for "electrical engineering technology".
    for norm, profile in sorted(candidates, key=lambda pair: -len(pair[0])):
        if _contains_phrase(query, norm):
            return profile

    # Typed something shorter, like "computer": take the closest name that
    # contains it, not the longest one that happens to mention it.
    for norm, profile in sorted(candidates, key=lambda pair: len(pair[0])):
        if _contains_phrase(norm, query):
            return profile

    # Token overlap hit.
    query_tokens = set(query.split())
    best: tuple[int, MajorProfile | None] = (0, None)
    for profile in MAJORS:
        tokens = set(_normalize(profile.name).split())
        for alias in profile.aliases:
            tokens |= set(_normalize(alias).split())
        tokens -= {"and", "of", "science", "engineering", "studies", "management"}
        overlap = len(query_tokens & tokens)
        if overlap > best[0]:
            best = (overlap, profile)
    if best[1] is not None:
        return best[1]

    # Unknown major: search and score on the major's own words.
    label = major_input.strip()
    words = [t for t in query.split() if len(t) > 2 and t not in {"and", "the"}]
    return MajorProfile(
        name=label,
        queries=[f"{label} intern", f"{label} internship", f"{label} summer intern"],
        anchors=[query],
        core=words,
        title_words=words,
    )


def prompt_for_major(preset: str = "") -> MajorProfile:
    """Ask the student for their major, showing the known list as shortcuts."""
    if preset.strip():
        profile = resolve(preset)
        print(f"Major: {profile.name}")
        return profile

    print("\nWhich major should I target internships for?")
    print("Pick a number, or type any major name.\n")
    names = list_majors()
    columns = 2
    rows = (len(names) + columns - 1) // columns
    for row in range(rows):
        line = ""
        for col in range(columns):
            index = row + col * rows
            if index < len(names):
                line += f"  {index + 1:2d}. {names[index]:<28}"
        print(line.rstrip())

    while True:
        answer = input("\nMajor: ").strip()
        if not answer:
            print("Please enter a number or a major name.")
            continue
        if answer.isdigit():
            index = int(answer)
            if 1 <= index <= len(names):
                profile = resolve(names[index - 1])
                print(f"Targeting: {profile.name}")
                return profile
            print(f"Enter a number between 1 and {len(names)}.")
            continue
        profile = resolve(answer)
        if _normalize(profile.name) != _normalize(answer):
            print(f"Closest match: {profile.name}")
        else:
            print(f"Targeting: {profile.name}")
        return profile
