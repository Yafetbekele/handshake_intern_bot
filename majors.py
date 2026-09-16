"""Map a student's major to Handshake search queries and scoring keywords.

`queries` are typed into Handshake's job search, one search per query.
`keywords` add weight when scoring a posting against the student's resume.
Unknown majors fall back to a generic profile built from the major's own words,
so typing anything at all still produces a usable search.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class MajorProfile:
    name: str
    queries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)


MAJORS: list[MajorProfile] = [
    MajorProfile(
        name="Computer Science",
        aliases=["cs", "comp sci", "computing", "computer engineering", "cse"],
        queries=[
            "software engineering intern",
            "software developer intern",
            "backend intern",
            "frontend intern",
            "machine learning intern",
        ],
        keywords=[
            "software", "engineer", "developer", "programming", "python", "java",
            "javascript", "cpp", "algorithms", "data structures", "api", "backend",
            "frontend", "full stack", "git", "debugging", "testing", "cloud",
            "distributed systems", "code review", "agile",
        ],
    ),
    MajorProfile(
        name="Data Science",
        aliases=["data analytics", "analytics", "statistics", "stats", "data"],
        queries=[
            "data science intern",
            "data analyst intern",
            "business intelligence intern",
            "machine learning intern",
        ],
        keywords=[
            "data", "analytics", "sql", "python", "pandas", "statistics",
            "modeling", "machine learning", "visualization", "tableau",
            "dashboard", "regression", "experimentation", "a b testing",
            "etl", "warehouse", "forecasting", "insights",
        ],
    ),
    MajorProfile(
        name="Electrical Engineering",
        aliases=["ee", "electrical", "electrical and computer engineering", "ece"],
        queries=[
            "electrical engineering intern",
            "hardware engineering intern",
            "embedded systems intern",
            "firmware intern",
        ],
        keywords=[
            "electrical", "circuit", "pcb", "embedded", "firmware", "signal",
            "analog", "digital", "fpga", "verilog", "vhdl", "power", "rf",
            "oscilloscope", "altium", "matlab", "simulink", "microcontroller",
            "schematic", "hardware",
        ],
    ),
    MajorProfile(
        name="Mechanical Engineering",
        aliases=["me", "mechanical", "mech e", "aerospace", "aeronautical"],
        queries=[
            "mechanical engineering intern",
            "design engineering intern",
            "manufacturing engineering intern",
            "product engineering intern",
        ],
        keywords=[
            "mechanical", "cad", "solidworks", "catia", "ansys", "tolerance",
            "gd&t", "thermal", "heat transfer", "fluid mechanics", "stress",
            "finite element", "prototype", "machining", "manufacturing",
            "assembly", "dfm", "testing", "hvac", "robotics",
        ],
    ),
    MajorProfile(
        name="Civil Engineering",
        aliases=["civil", "structural engineering", "environmental engineering"],
        queries=[
            "civil engineering intern",
            "structural engineering intern",
            "construction management intern",
            "transportation engineering intern",
        ],
        keywords=[
            "civil", "structural", "construction", "autocad", "revit", "site",
            "concrete", "steel", "geotechnical", "surveying", "hydrology",
            "transportation", "permitting", "inspection", "estimating",
            "drawings", "specifications", "osha",
        ],
    ),
    MajorProfile(
        name="Chemical Engineering",
        aliases=["chem e", "cheme", "chemical"],
        queries=[
            "chemical engineering intern",
            "process engineering intern",
            "manufacturing engineering intern",
        ],
        keywords=[
            "chemical", "process", "reactor", "distillation", "aspen",
            "heat transfer", "mass transfer", "pilot plant", "gmp", "yield",
            "polymer", "catalyst", "safety", "hazop", "scale up", "unit operations",
        ],
    ),
    MajorProfile(
        name="Industrial Engineering",
        aliases=["ie", "industrial", "operations research", "systems engineering"],
        queries=[
            "industrial engineering intern",
            "supply chain intern",
            "operations intern",
            "process improvement intern",
        ],
        keywords=[
            "industrial", "supply chain", "logistics", "lean", "six sigma",
            "throughput", "scheduling", "optimization", "simulation",
            "inventory", "warehouse", "kaizen", "process improvement",
            "operations", "forecasting", "capacity",
        ],
    ),
    MajorProfile(
        name="Biomedical Engineering",
        aliases=["bme", "biomedical", "bioengineering"],
        queries=[
            "biomedical engineering intern",
            "medical device intern",
            "r&d engineering intern",
        ],
        keywords=[
            "biomedical", "medical device", "fda", "iso 13485", "clinical",
            "biomaterials", "imaging", "prototype", "verification", "validation",
            "regulatory", "tissue", "instrumentation", "matlab", "design control",
        ],
    ),
    MajorProfile(
        name="Biology",
        aliases=["bio", "biological sciences", "biochemistry", "microbiology", "neuroscience"],
        queries=[
            "biology intern",
            "research intern",
            "laboratory intern",
            "biotech intern",
        ],
        keywords=[
            "laboratory", "research", "assay", "pcr", "cell culture",
            "molecular biology", "microscopy", "pipette", "protocol",
            "specimen", "sequencing", "data collection", "wet lab",
            "chromatography", "elisa", "sterile",
        ],
    ),
    MajorProfile(
        name="Chemistry",
        aliases=["chem", "chemical sciences"],
        queries=[
            "chemistry intern",
            "analytical chemistry intern",
            "laboratory intern",
            "quality control intern",
        ],
        keywords=[
            "chemistry", "analytical", "hplc", "gc ms", "spectroscopy",
            "titration", "synthesis", "organic chemistry", "laboratory",
            "quality control", "reagent", "calibration", "sop", "purity",
        ],
    ),
    MajorProfile(
        name="Physics",
        aliases=["applied physics", "astronomy", "astrophysics"],
        queries=[
            "physics intern",
            "research intern",
            "optics intern",
            "engineering intern",
        ],
        keywords=[
            "physics", "optics", "laser", "vacuum", "cryogenic", "simulation",
            "modeling", "matlab", "python", "instrumentation", "measurement",
            "data analysis", "experiment", "semiconductor",
        ],
    ),
    MajorProfile(
        name="Mathematics",
        aliases=["math", "applied math", "actuarial science"],
        queries=[
            "quantitative intern",
            "data analyst intern",
            "actuarial intern",
            "research intern",
        ],
        keywords=[
            "quantitative", "mathematics", "statistics", "probability",
            "linear algebra", "modeling", "optimization", "python", "r",
            "sql", "actuarial", "risk", "stochastic", "numerical",
        ],
    ),
    MajorProfile(
        name="Finance",
        aliases=["fin", "banking", "investment", "financial economics"],
        queries=[
            "finance intern",
            "investment banking summer analyst",
            "financial analyst intern",
            "corporate finance intern",
        ],
        keywords=[
            "finance", "financial modeling", "valuation", "dcf", "excel",
            "forecasting", "budget", "variance", "equity research", "portfolio",
            "capital markets", "due diligence", "bloomberg", "accounting",
            "reporting", "investment",
        ],
    ),
    MajorProfile(
        name="Accounting",
        aliases=["acct", "audit", "tax"],
        queries=[
            "accounting intern",
            "audit intern",
            "tax intern",
        ],
        keywords=[
            "accounting", "audit", "tax", "gaap", "reconciliation", "ledger",
            "journal entries", "excel", "internal controls", "financial statements",
            "quickbooks", "cpa", "close process", "compliance",
        ],
    ),
    MajorProfile(
        name="Economics",
        aliases=["econ"],
        queries=[
            "economics intern",
            "research analyst intern",
            "data analyst intern",
            "policy intern",
        ],
        keywords=[
            "economics", "econometrics", "regression", "stata", "r", "policy",
            "research", "data analysis", "forecasting", "market research",
            "microeconomics", "statistics", "report writing",
        ],
    ),
    MajorProfile(
        name="Business Administration",
        aliases=["business", "management", "ba", "bba", "entrepreneurship"],
        queries=[
            "business intern",
            "business analyst intern",
            "operations intern",
            "strategy intern",
        ],
        keywords=[
            "business", "strategy", "analysis", "excel", "powerpoint",
            "stakeholder", "process", "operations", "reporting", "kpi",
            "project management", "client", "market research", "presentation",
        ],
    ),
    MajorProfile(
        name="Marketing",
        aliases=["advertising", "digital marketing", "brand management"],
        queries=[
            "marketing intern",
            "digital marketing intern",
            "social media intern",
            "brand intern",
        ],
        keywords=[
            "marketing", "social media", "content creation", "campaign", "seo",
            "search engine optimization", "analytics", "copywriting", "brand",
            "email marketing", "google analytics", "audience", "engagement",
            "canva", "crm",
        ],
    ),
    MajorProfile(
        name="Information Systems",
        aliases=["mis", "it", "information technology", "cybersecurity", "cyber security"],
        queries=[
            "information technology intern",
            "it intern",
            "cybersecurity intern",
            "systems analyst intern",
        ],
        keywords=[
            "information systems", "it support", "network", "security",
            "cyber security", "active directory", "sql", "helpdesk",
            "troubleshooting", "cloud", "azure", "compliance", "siem",
            "vulnerability", "documentation",
        ],
    ),
    MajorProfile(
        name="Psychology",
        aliases=["psych", "cognitive science", "behavioral science"],
        queries=[
            "psychology intern",
            "research assistant intern",
            "human resources intern",
            "behavioral health intern",
        ],
        keywords=[
            "psychology", "research", "survey", "participants", "irb",
            "qualitative", "quantitative", "spss", "counseling", "behavioral",
            "data collection", "literature review", "case notes",
        ],
    ),
    MajorProfile(
        name="Communications",
        aliases=["comm", "public relations", "pr", "journalism", "media studies"],
        queries=[
            "communications intern",
            "public relations intern",
            "media intern",
            "content intern",
        ],
        keywords=[
            "communications", "public relations", "press release", "media",
            "content creation", "writing", "editing", "social media",
            "storytelling", "newsletter", "ap style", "outreach", "events",
        ],
    ),
    MajorProfile(
        name="Graphic Design",
        aliases=["design", "visual arts", "studio art", "ux", "ui", "ux design"],
        queries=[
            "design intern",
            "graphic design intern",
            "ux design intern",
            "product design intern",
        ],
        keywords=[
            "design", "figma", "adobe", "photoshop", "illustrator", "indesign",
            "typography", "layout", "branding", "user experience",
            "user interface", "wireframe", "prototype", "portfolio", "mockup",
        ],
    ),
    MajorProfile(
        name="Political Science",
        aliases=["poli sci", "government", "public policy", "international relations"],
        queries=[
            "policy intern",
            "government affairs intern",
            "legislative intern",
            "public affairs intern",
        ],
        keywords=[
            "policy", "government", "legislative", "advocacy", "research",
            "constituent", "regulatory", "public affairs", "briefing",
            "stakeholder", "nonprofit", "campaign", "writing",
        ],
    ),
    MajorProfile(
        name="Nursing",
        aliases=["nurse", "bsn", "health sciences", "public health"],
        queries=[
            "nursing intern",
            "healthcare intern",
            "public health intern",
            "clinical intern",
        ],
        keywords=[
            "nursing", "clinical", "patient", "healthcare", "hipaa", "vitals",
            "charting", "epic", "care plan", "public health", "community health",
            "bls", "triage", "rounds",
        ],
    ),
    MajorProfile(
        name="Education",
        aliases=["teaching", "elementary education", "secondary education"],
        queries=[
            "education intern",
            "teaching intern",
            "youth program intern",
            "curriculum intern",
        ],
        keywords=[
            "education", "teaching", "curriculum", "lesson plan", "classroom",
            "students", "tutoring", "assessment", "youth", "mentoring",
            "differentiated instruction", "literacy",
        ],
    ),
    MajorProfile(
        name="Environmental Science",
        aliases=["environmental studies", "sustainability", "geology", "earth science"],
        queries=[
            "environmental intern",
            "sustainability intern",
            "environmental science intern",
            "conservation intern",
        ],
        keywords=[
            "environmental", "sustainability", "gis", "arcgis", "sampling",
            "water quality", "air quality", "compliance", "epa", "permitting",
            "field work", "conservation", "remediation", "emissions",
        ],
    ),
    MajorProfile(
        name="Supply Chain Management",
        aliases=["logistics", "operations management", "procurement"],
        queries=[
            "supply chain intern",
            "logistics intern",
            "procurement intern",
            "operations intern",
        ],
        keywords=[
            "supply chain", "logistics", "procurement", "sourcing", "inventory",
            "sap", "erp", "freight", "warehouse", "demand planning",
            "supplier", "purchase order", "lean", "excel",
        ],
    ),
    MajorProfile(
        name="Human Resources",
        aliases=["hr", "human resource management", "organizational behavior"],
        queries=[
            "human resources intern",
            "hr intern",
            "talent acquisition intern",
            "recruiting intern",
        ],
        keywords=[
            "human resources", "recruiting", "talent acquisition", "onboarding",
            "hris", "workday", "employee relations", "benefits", "compliance",
            "interview", "sourcing", "engagement", "training",
        ],
    ),
]

GENERIC_INTERN_KEYWORDS = [
    "intern", "internship", "summer", "student", "undergraduate",
    "collaborate", "communication", "analysis", "learn", "training",
]


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

    # Whole-word containment in either direction, longest candidate first so
    # "electrical engineering" beats a bare "engineering". Very short aliases
    # such as "cs" or "ba" are skipped here; only the exact match above may
    # claim them, otherwise they fire inside unrelated words.
    candidates: list[tuple[str, MajorProfile]] = []
    for profile in MAJORS:
        for candidate in [profile.name, *profile.aliases]:
            norm = _normalize(candidate)
            if len(norm) >= 4:
                candidates.append((norm, profile))
    candidates.sort(key=lambda pair: -len(pair[0]))

    for norm, profile in candidates:
        if _contains_phrase(query, norm) or _contains_phrase(norm, query):
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

    # Unknown major: search on the major itself.
    label = major_input.strip()
    return MajorProfile(
        name=label,
        queries=[f"{label} intern", f"{label} internship", f"{label} summer intern"],
        keywords=[t for t in query.split() if len(t) > 2],
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
