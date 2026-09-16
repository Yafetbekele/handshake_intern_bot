"""Turn a resume file into a keyword profile used for job matching.

Supports PDF (pdfplumber), DOCX (python-docx) and plain text. Nothing here
touches the network; it only reads the local file the user points at.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

STOPWORDS = {
    "a", "about", "above", "across", "after", "all", "also", "am", "an", "and",
    "any", "are", "as", "at", "be", "been", "being", "between", "both", "but",
    "by", "can", "did", "do", "does", "during", "each", "for", "from", "had",
    "has", "have", "he", "her", "here", "him", "his", "how", "i", "if", "in",
    "into", "is", "it", "its", "just", "me", "more", "most", "my", "no", "not",
    "of", "on", "one", "only", "or", "other", "our", "out", "over", "own",
    "per", "s", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "those",
    "through", "to", "too", "under", "up", "use", "used", "using", "very",
    "was", "we", "were", "what", "when", "where", "which", "while", "who",
    "will", "with", "within", "would", "you", "your",
    # resume boilerplate that carries no signal
    "experience", "education", "skills", "university", "college", "school",
    "gpa", "present", "current", "expected", "relevant", "coursework",
    "project", "projects", "work", "worked", "team", "teams", "responsible",
    "including", "various", "strong", "excellent", "ability", "resume", "cv",
    "email", "phone", "linkedin", "github", "com", "org", "www", "http",
    "https", "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
}

# Multi-word skills have to be matched as phrases, so they live in their own
# list. Single tokens are picked up by the frequency counter instead.
SKILL_PHRASES = [
    "machine learning", "deep learning", "natural language processing",
    "computer vision", "data analysis", "data science", "data engineering",
    "data visualization", "data structures", "software engineering",
    "web development", "front end", "back end", "full stack", "mobile development",
    "cloud computing", "distributed systems", "operating systems",
    "embedded systems", "signal processing", "control systems", "circuit design",
    "finite element", "heat transfer", "fluid mechanics", "solid mechanics",
    "computer aided design", "technical drawing", "project management",
    "supply chain", "financial modeling", "financial analysis", "equity research",
    "market research", "business development", "product management",
    "public health", "clinical research", "wet lab", "cell culture",
    "molecular biology", "organic chemistry", "analytical chemistry",
    "environmental science", "graphic design", "user experience",
    "user interface", "social media", "content creation", "technical writing",
    "quality assurance", "test automation", "version control",
    "continuous integration", "unit testing", "api design", "rest api",
    "object oriented", "linear algebra", "differential equations",
    "statistical modeling", "time series", "a b testing", "game development",
    "cyber security", "network security", "penetration testing",
    "digital marketing", "search engine optimization", "human resources",
    "civil engineering", "structural analysis", "geotechnical",
    "materials science", "process engineering", "supply planning",
]

TECH_TOKENS = {
    "python", "java", "javascript", "typescript", "c", "cpp", "csharp", "go",
    "rust", "ruby", "php", "swift", "kotlin", "scala", "r", "matlab", "sql",
    "nosql", "html", "css", "react", "angular", "vue", "node", "express",
    "django", "flask", "fastapi", "spring", "rails", "dotnet", "pandas",
    "numpy", "scipy", "sklearn", "scikit", "pytorch", "tensorflow", "keras",
    "jupyter", "tableau", "powerbi", "excel", "vba", "stata", "spss", "sas",
    "git", "github", "gitlab", "docker", "kubernetes", "terraform", "jenkins",
    "aws", "azure", "gcp", "linux", "bash", "postgres", "postgresql", "mysql",
    "mongodb", "redis", "kafka", "spark", "hadoop", "airflow", "dbt",
    "snowflake", "figma", "sketch", "photoshop", "illustrator", "indesign",
    "autocad", "solidworks", "catia", "ansys", "revit", "simulink", "labview",
    "verilog", "vhdl", "fpga", "arduino", "raspberry", "ros", "opencv",
    "salesforce", "hubspot", "quickbooks", "bloomberg", "capiq",
}


@dataclass
class ResumeProfile:
    """Keyword signal pulled out of a resume."""

    path: Path
    raw_text: str
    token_counts: Counter = field(default_factory=Counter)
    skills: list[str] = field(default_factory=list)
    phrases: list[str] = field(default_factory=list)

    @property
    def top_tokens(self) -> list[tuple[str, int]]:
        return self.token_counts.most_common(80)

    def summary(self) -> str:
        skills = ", ".join(self.skills[:12]) or "none detected"
        phrases = ", ".join(self.phrases[:8]) or "none detected"
        return (
            f"Resume: {self.path.name}\n"
            f"  characters read : {len(self.raw_text)}\n"
            f"  tools detected  : {skills}\n"
            f"  topics detected : {phrases}"
        )


def _read_pdf(path: Path) -> str:
    try:
        import pdfplumber
    except ImportError as exc:  # pragma: no cover - dependency hint
        raise SystemExit(
            "Reading PDF resumes needs pdfplumber. Run: pip install -r requirements.txt"
        ) from exc

    chunks: list[str] = []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            chunks.append(page.extract_text() or "")
    return "\n".join(chunks)


def _read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover - dependency hint
        raise SystemExit(
            "Reading DOCX resumes needs python-docx. Run: pip install -r requirements.txt"
        ) from exc

    document = docx.Document(str(path))
    parts = [p.text for p in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.extend(cell.text for cell in row.cells)
    return "\n".join(parts)


def read_text(path: str | Path) -> str:
    """Return the plain text of a resume file."""
    path = Path(path).expanduser()
    if not path.exists():
        raise SystemExit(f"Resume not found: {path}")

    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text = _read_pdf(path)
    elif suffix in {".docx", ".doc"}:
        text = _read_docx(path)
    elif suffix in {".txt", ".md", ".rtf"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
    else:
        raise SystemExit(
            f"Unsupported resume format '{suffix}'. Use PDF, DOCX or TXT."
        )

    if not text.strip():
        raise SystemExit(
            f"No text could be extracted from {path.name}. "
            "If it is a scanned image, export a text-based PDF instead."
        )
    return text


def normalize(text: str) -> str:
    """Lowercase and collapse anything that is not a letter or digit."""
    text = text.lower()
    text = text.replace("c++", " cpp ").replace("c#", " csharp ").replace(".net", " dotnet ")
    text = re.sub(r"[^a-z0-9+#.]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    tokens = []
    for raw in normalize(text).split():
        token = raw.strip(".+#")
        if len(token) < 2 and token != "r":
            continue
        if token in STOPWORDS:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


def extract(path: str | Path) -> ResumeProfile:
    """Build a ResumeProfile from a resume file."""
    path = Path(path).expanduser()
    raw = read_text(path)
    normalized = normalize(raw)
    tokens = tokenize(raw)

    counts = Counter(tokens)
    skills = sorted({t for t in counts if t in TECH_TOKENS})
    phrases = [p for p in SKILL_PHRASES if p in normalized]

    return ResumeProfile(
        path=path,
        raw_text=raw,
        token_counts=counts,
        skills=skills,
        phrases=phrases,
    )
