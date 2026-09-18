"""Playwright driver for Handshake job search and application submission.

Design notes
    * A persistent browser profile lives in data/browser_profile, so the student
      signs in through their university once (including any MFA) and the session
      is reused on later runs. No password is ever read, stored or typed by this
      code.
    * Locators are tried role-first then CSS-fallback, because Handshake ships
      DOM changes regularly. Fallback CSS lives in selectors.json.
    * Postings that hand off to an external applicant tracking system are
      reported and skipped rather than half-filled.
"""

from __future__ import annotations

import os
import re
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote_plus, urlencode, urlparse, urlunparse

PLAYWRIGHT_HINT = (
    "Playwright is not installed. From the project folder run:\n"
    "  pip install -r requirements.txt\n"
    "  python -m playwright install chromium"
)

# Imported lazily so that `--help`, `majors` and `history` work without a
# browser stack installed. Annotations are strings here thanks to
# `from __future__ import annotations`, so the fallbacks below are safe.
try:
    from playwright.sync_api import (  # type: ignore[import-not-found]
        Locator,
        Page,
        TimeoutError as PlaywrightTimeout,
        sync_playwright,
    )

    PLAYWRIGHT_AVAILABLE = True
except ImportError:  # pragma: no cover - dependency hint
    Locator = Any  # type: ignore[assignment,misc]
    Page = Any  # type: ignore[assignment,misc]
    sync_playwright = None  # type: ignore[assignment]
    PLAYWRIGHT_AVAILABLE = False

    class PlaywrightTimeout(Exception):  # type: ignore[no-redef]
        """Stand-in so except clauses stay valid without Playwright."""

SECURITY_CHECK_MESSAGE = (
    "\nHandshake is showing a security check that blocks automated browsers.\n"
    "The assistant stops here rather than trying to get around it.\n"
    "You can still search and apply on Handshake yourself in your normal browser."
)

SEARCH_NOT_FOUND_MESSAGE = (
    "\nCould not find Handshake's job search box. Tried:\n  {tried}\n"
    "Handshake may have changed its layout. As a workaround, search for an\n"
    "internship on Handshake in your normal browser, copy the address, and set it\n"
    "as search_url_template in config.json with {{query}} and {{page}} in place of\n"
    "the search words and page number. See 'When it breaks' in the README."
)

LOCATION_FILTER_MESSAGE = (
    "\nCould not set Handshake's location filter to '{near}': {detail}.\n"
    "Stopping so nothing is applied to outside your area. Try a different city\n"
    "spelling or a ZIP code, or leave the city blank to search everywhere."
)


SEARCH_MOVED_MESSAGE = (
    "\nHandshake redirected the job search from {wanted} to {actual}.\n"
    "Its search page has probably moved, so no jobs could be found.\n"
    "To fix it: search for an internship on Handshake in your normal browser,\n"
    "copy the address from the address bar, and set it as search_url_template\n"
    "in config.json, with {{query}} and {{page}} in place of the search words\n"
    "and page number. See 'When it breaks' in the README."
)

# Job pages: /job-search/<id> (current), /jobs/<id>, /stu/jobs/<id>, /public/jobs/<id>.
JOB_ID_RE = re.compile(r"/(?:job-search|jobs)/(\d+)")

SEARCH_PAGE_PATHS = ("/job-search", "/explore", "/stu/postings")
JOB_PAGE_PATTERNS = ("{base}/job-search/{id}", "{base}/jobs/{id}", "{base}/stu/jobs/{id}")

# Addresses that mean "not signed in yet" or "Handshake wants a setup step first".
NOT_READY_URL_HINTS = ("login", "sign_in", "saml", "sso", "/access", "onboarding", "visibility-settings")

NEXT_TEXT = re.compile(r"^\s*(next|next page|›|»|>)\s*$", re.IGNORECASE)

SCROLL_LISTS_JS = """
() => {
  for (const el of document.querySelectorAll('main *, [role="main"] *, body > div *')) {
    const style = getComputedStyle(el);
    if ((style.overflowY === 'auto' || style.overflowY === 'scroll') && el.scrollHeight > el.clientHeight + 20) {
      el.scrollTop = el.scrollHeight;
    }
  }
}
"""

# Mark the job's own detail panel: climb from the heading until the next level
# up would also contain other jobs' links, i.e. a results list.
DETAIL_ROOT_JS = r"""
() => {
  document.querySelectorAll('[data-hsbot-detail]').forEach(e => e.removeAttribute('data-hsbot-detail'));
  const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
  const headings = [...document.querySelectorAll('h1')].filter(h => visible(h) && h.innerText.trim());
  // Handshake's job panel is data-hook="right-content"; the page also has a
  // separate "Jobs" heading above the results, so never just take the first h1.
  const panel = document.querySelector("[data-hook='right-content']");
  const heading = (panel && headings.find(h => panel.contains(h)))
    || headings.find(h => !/^\s*jobs\s*$/i.test(h.innerText))
    || headings[0];
  if (!heading) return {};
  const jobIds = el => {
    const ids = new Set();
    el.querySelectorAll('a[href]').forEach(a => {
      const m = (a.getAttribute('href') || '').match(/\/(?:job-search|jobs)\/(\d+)/);
      if (m) ids.add(m[1]);
    });
    return ids.size;
  };
  let root = heading;
  if (panel && panel.contains(heading)) {
    root = panel;
  } else {
    while (root.parentElement && root.parentElement !== document.body
           && root.parentElement !== document.documentElement
           && jobIds(root.parentElement) <= 1) {
      root = root.parentElement;
    }
  }
  root.setAttribute('data-hsbot-detail', '1');
  const employer = [...root.querySelectorAll("a[href*='/employers/'], a[href*='/e/']")]
    .find(a => a.innerText.trim());

  // Read the description without "Similar jobs" cards, whose titles would
  // otherwise make, say, a full-time job look like an internship. They are
  // hidden only for the instant it takes to read the text.
  const current = (location.pathname.match(/\/(?:job-search|jobs)\/(\d+)/) || [])[1];
  const hidden = [];
  root.querySelectorAll('a[href]').forEach(a => {
    const m = (a.getAttribute('href') || '').match(/\/(?:job-search|jobs)\/(\d+)/);
    if (m && m[1] !== current) { hidden.push([a, a.style.display]); a.style.display = 'none'; }
  });
  const text = (root.innerText || '').slice(0, 24000);
  hidden.forEach(([a, display]) => { a.style.display = display; });

  return {
    title: heading.innerText.trim().split('\n')[0],
    employer: employer ? employer.innerText.trim().split('\n')[0] : '',
    text,
  };
}
"""

APPLY_TEXT = re.compile(r"^\s*(quick\s+apply|easy\s+apply|apply\s+now|apply)\s*$", re.IGNORECASE)
EXTERNAL_TEXT = re.compile(
    r"apply\s+externally|apply\s+on\s+(company|employer)|external\s+application",
    re.IGNORECASE,
)
# Personal or legal questions. The assistant answers these only with words the
# student gave it, never with a guess of its own.
SENSITIVE_QUESTION = re.compile(
    r"sponsor|visa|citizen|green card|work permit|clearance|secret|salary|compensation|"
    r"wage|felon|criminal|conviction|background check|drug test|gender|race|ethnic|"
    r"hispanic|latino|veteran|disab|age\b|date of birth|birth date|marital|pregnan|religion",
    re.IGNORECASE,
)

# Never filled in, whatever is saved. These belong in no form the assistant drives.
NEVER_FILL = re.compile(
    r"social security|\bssn\b|passport|driver'?s? licen|bank|routing|account number|"
    r"credit card|card number|cvv|password|pin\b",
    re.IGNORECASE,
)

# Document controls are handled elsewhere, so the question filler skips them.
DOCUMENT_FIELD = re.compile(r"resume|cover letter|transcript|search your|upload", re.IGNORECASE)

YES_NO_TEXT = re.compile(r"^\s*(yes|no)\s*$", re.IGNORECASE)

APPLIED_BUTTON_TEXT = re.compile(r"^\s*(applied|withdraw( application)?)\s*$", re.IGNORECASE)
SUBMIT_TEXT = re.compile(r"submit\s+application|^\s*submit\s*$", re.IGNORECASE)


@dataclass
class Job:
    job_id: str
    url: str
    title: str = ""
    employer: str = ""
    location: str = ""
    description: str = ""
    apply_kind: str = "unknown"  # quick | external | already_applied | closed | unknown
    error: str = ""

    @property
    def search_text(self) -> str:
        return " ".join([self.title, self.employer, self.location, self.description])

    def label(self) -> str:
        employer = self.employer or "unknown employer"
        location = self.location or "location not listed"
        return f"{self.title or 'Untitled'} @ {employer} ({location})"


DOCUMENT_KINDS = ("resume", "cover_letter", "transcript")

DOCUMENT_KEYWORDS = {
    "resume": ("resume", "résumé", "cv"),
    "cover_letter": ("cover letter", "cover"),
    "transcript": ("transcript",),
}


@dataclass
class Documents:
    """Files to attach. `*_name` picks a document already saved on Handshake;
    `*_path` is a local file uploaded when no saved document matches."""

    resume_path: str = ""
    resume_name: str = ""
    cover_letter_path: str = ""
    cover_letter_name: str = ""
    transcript_path: str = ""
    transcript_name: str = ""
    # When true, resume_path is a resume made for this job: it replaces the
    # default resume Handshake attaches, but only inside this one application.
    tailored_resume: bool = False
    # Writes a cover letter for this job. Called only when the application
    # actually has a cover letter slot and no letter was given.
    cover_letter_writer: Callable[[], Path | None] | None = field(default=None, repr=False, compare=False)

    def name_for(self, kind: str) -> str:
        return str(getattr(self, f"{kind}_name", "") or "").strip()

    def path_for(self, kind: str) -> Path | None:
        raw = str(getattr(self, f"{kind}_path", "") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.exists() else None


def same_search_page(wanted: str, actual: str) -> bool:
    """True when `actual` is the search page, allowing an auto-selected job
    number after it (/job-search -> /job-search/11428461)."""
    wanted = wanted.rstrip("/") or "/"
    actual = actual.rstrip("/") or "/"
    if actual == wanted:
        return True
    return re.fullmatch(re.escape(wanted) + r"/\d+", actual) is not None


JOB_TYPE_PARAMS = {
    # Handshake's own filter values, read from the live site.
    "internships": [("jobType", "3")],
    "jobs": [("jobType", "9"), ("employmentTypes", "1"), ("employmentTypes", "2")],
}


def filtered_search_url(url: str, looking_for: str, miles: int | None) -> str:
    """Return a search address with the job type set and, if given, the distance.

    The location itself must already be in the address (picked in the page);
    only its distance is changed here, to the "25mi" form Handshake uses.
    """
    import json as _json

    parsed = urlparse(url)
    pairs = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k not in {"jobType", "employmentTypes"}
    ]
    if miles is not None:
        updated = []
        for key, value in pairs:
            if key == "locationFilter":
                try:
                    data = _json.loads(value)
                    data["distance"] = f"{int(miles)}mi"
                    value = _json.dumps(data, separators=(",", ":"))
                except (ValueError, TypeError):
                    pass
            updated.append((key, value))
        pairs = updated
    pairs += JOB_TYPE_PARAMS.get(looking_for, [])
    return urlunparse(parsed._replace(query=urlencode(pairs)))


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}


def place_state(near: str) -> str:
    """The state named after the comma in "Baltimore, MD", spelled out."""
    parts = [part.strip() for part in near.split(",")[1:] if part.strip()]
    if not parts:
        return ""
    state = parts[0]
    return US_STATES.get(state.upper(), state)


def location_label(url: str) -> str:
    """The place and distance a search address is filtered to, if any."""
    import json as _json

    for key, value in parse_qsl(urlparse(url).query, keep_blank_values=True):
        if key == "locationFilter":
            try:
                data = _json.loads(value)
            except (ValueError, TypeError):
                return ""
            return str(data.get("label") or data.get("text") or "")
    return ""


LOCATION_LINE_RE = re.compile(
    r"^\s*((?:onsite|on-site|remote|hybrid|remote or hybrid)\b[^\n]{0,120})$",
    re.IGNORECASE | re.MULTILINE,
)


def location_from_text(text: str) -> str:
    """Pull a line like 'Onsite, based in Baltimore, MD' from a job panel."""
    match = LOCATION_LINE_RE.search(text[:3000] if text else "")
    return " ".join(match.group(1).split()) if match else ""


def classify_document_label(label: str) -> str:
    """Map an upload slot's label to resume, cover_letter, transcript or unknown."""
    text = " ".join(label.lower().split())
    if "cover" in text:
        return "cover_letter"
    if "transcript" in text:
        return "transcript"
    if "resume" in text or "résumé" in text or re.search(r"\bcv\b", text):
        return "resume"
    return "unknown"


# Label text for a form control, with the control's own option text removed.
# The question a radio button belongs to, e.g. "Are you authorized to work?"
# rather than the button's own "Yes" or "No".
GROUP_QUESTION_JS = """
(node) => {
  const clean = (el) => {
    if (!el) return '';
    const copy = el.cloneNode(true);
    copy.querySelectorAll('input, select, textarea, button, label').forEach(e => e.remove());
    return (copy.textContent || '').replace(/\\s+/g, ' ').trim();
  };
  const set = node.closest('fieldset, [role="radiogroup"], [role="group"]');
  if (set) {
    const legend = set.querySelector('legend, h3, h4, [id]');
    const label = set.getAttribute('aria-label') || (legend ? legend.textContent : '');
    const text = (label || '').replace(/\\s+/g, ' ').trim() || clean(set);
    if (text) return text.slice(0, 160);
  }
  let parent = node.parentElement;
  for (let i = 0; i < 4 && parent; i++) {
    const text = clean(parent);
    if (text) return text.slice(0, 160);
    parent = parent.parentElement;
  }
  return '';
}
"""

LABEL_JS = """
(node) => {
  const strip = (el) => {
    if (!el) return '';
    const copy = el.cloneNode(true);
    copy.querySelectorAll('select, option, input, textarea, button').forEach(e => e.remove());
    return (copy.textContent || '').replace(/\\s+/g, ' ').trim();
  };
  if (node.id) {
    const byFor = document.querySelector(`label[for="${CSS.escape(node.id)}"]`);
    if (byFor) { const t = strip(byFor); if (t) return t; }
  }
  const aria = node.getAttribute('aria-label');
  if (aria) return aria;
  const labelledBy = node.getAttribute('aria-labelledby');
  if (labelledBy) {
    const el = document.getElementById(labelledBy);
    if (el) { const t = strip(el); if (t) return t; }
  }
  const wrap = node.closest('label');
  if (wrap) { const t = strip(wrap); if (t) return t; }
  let parent = node.parentElement;
  for (let i = 0; i < 3 && parent; i++) {
    const t = strip(parent);
    if (t) return t.slice(0, 160);
    parent = parent.parentElement;
  }
  return node.getAttribute('name') || node.getAttribute('placeholder') || '';
}
"""

# Every required control in the dialog that is still empty.
MISSING_REQUIRED_JS = """
(dialog) => {
  const labelOf = LABEL_FN;
  const missing = [];
  const seenGroups = new Set();
  const flaggedByLabel = (el) => {
    const text = labelOf(el) || '';
    return /\\*|\\brequired\\b/i.test(text) && !/optional/i.test(text);
  };
  for (const el of dialog.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search'].includes(type)) continue;
    if (el.disabled) continue;
    const required = el.required || el.getAttribute('aria-required') === 'true' || flaggedByLabel(el);
    if (!required) continue;

    if (type === 'radio') {
      const key = el.name || labelOf(el);
      if (seenGroups.has(key)) continue;
      seenGroups.add(key);
      const group = el.name
        ? dialog.querySelectorAll(`input[type="radio"][name="${CSS.escape(el.name)}"]`)
        : [el];
      if (![...group].some(r => r.checked)) missing.push(labelOf(el));
      continue;
    }
    if (type === 'checkbox') {
      if (!el.checked) missing.push(labelOf(el));
      continue;
    }
    if (type === 'file') {
      if (!el.files || el.files.length === 0) missing.push(labelOf(el));
      continue;
    }
    if (!String(el.value || '').trim()) missing.push(labelOf(el));
  }
  return missing.map(t => (t || 'unlabeled field').slice(0, 80));
}
""".replace("LABEL_FN", LABEL_JS.strip())


class HandshakeSession:
    """Owns the browser context and all page interaction."""

    def __init__(
        self,
        config: dict[str, Any],
        selectors: dict[str, list[str]],
        profile_dir: str | Path = "data/browser_profile",
    ) -> None:
        self.config = config
        self.selectors = selectors
        self.base = str(config.get("handshake_base_url", "https://app.joinhandshake.com")).rstrip("/")
        self.profile_dir = self._resolve_profile_dir(Path(profile_dir))
        self._playwright = None
        self._context = None
        self.page: Page | None = None
        self._learned_template = ""
        # Answers the student typed during the last application, to save for next time.
        self.last_learned_answers: list[dict[str, Any]] = []

    @staticmethod
    def _resolve_profile_dir(preferred: Path) -> Path:
        """Keep the browser profile inside the project unless Windows forbids it.

        Chromium creates deeply nested paths such as
        ``Default\\Code Cache\\js\\index-dir`` under the profile. On Windows the
        classic 260 character limit then silently breaks session storage, which
        would make the student sign in on every run. When the project sits too
        deep, fall back to a short path under LOCALAPPDATA.
        """
        try:
            resolved = preferred.resolve()
        except OSError:
            resolved = preferred

        headroom = 140
        if os.name != "nt" or len(str(resolved)) + headroom < 260:
            resolved.mkdir(parents=True, exist_ok=True)
            return resolved

        base = Path(os.environ.get("LOCALAPPDATA") or tempfile.gettempdir())
        fallback = base / "handshake_intern_bot" / "browser_profile"
        fallback.mkdir(parents=True, exist_ok=True)
        print(
            "[note] This project sits too deep in the filesystem for Chromium to "
            "store a session here.\n"
            f"       Using {fallback} for the saved login instead."
        )
        return fallback

    # ---------------------------------------------------------------- lifecycle

    def __enter__(self) -> "HandshakeSession":
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def start(self) -> None:
        if not PLAYWRIGHT_AVAILABLE or sync_playwright is None:
            raise SystemExit(PLAYWRIGHT_HINT)
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_dir),
            headless=bool(self.config.get("headless", False)),
            viewport={"width": 1440, "height": 950},
        )
        self._context.set_default_timeout(20_000)
        self.page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def close(self) -> None:
        try:
            if self._context is not None:
                self._context.close()
        finally:
            if self._playwright is not None:
                self._playwright.stop()
            self._context = None
            self._playwright = None
            self.page = None

    # ------------------------------------------------------------------ helpers

    def _sel(self, key: str) -> list[str]:
        value = self.selectors.get(key, [])
        return [v for v in value if isinstance(v, str)]

    def _first_visible(self, key: str, timeout: int = 4000) -> Locator | None:
        """Return the first selector in `key`'s list that resolves to a visible node."""
        assert self.page is not None
        deadline = time.time() + timeout / 1000
        candidates = self._sel(key)
        while time.time() < deadline:
            for selector in candidates:
                try:
                    matches = self.page.locator(selector)
                    # Pages keep hidden copies around (Handshake has several
                    # closed dialogs in the DOM), so check more than the first.
                    for index in range(min(matches.count(), 15)):
                        locator = matches.nth(index)
                        if locator.is_visible():
                            return locator
                except Exception:
                    continue
            self.page.wait_for_timeout(250)
        return None

    def _text(self, key: str, limit: int = 20_000) -> str:
        locator = self._first_visible(key, timeout=2500)
        if locator is None:
            return ""
        try:
            return (locator.inner_text() or "").strip()[:limit]
        except Exception:
            return ""

    def _settle(self, ms: int = 1200) -> None:
        assert self.page is not None
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except PlaywrightTimeout:
            pass
        self.page.wait_for_timeout(ms)

    # ------------------------------------------------------- stop conditions

    def security_check_showing(self) -> bool:
        """True when Handshake is serving a bot check instead of its own page."""
        assert self.page is not None
        url = (self.page.url or "").lower()
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            title = ""
        if "cf_challenge" in url or "just a moment" in title:
            return True
        try:
            body = self.page.inner_text("body")[:2000].lower()
        except Exception:
            body = ""
        return any(
            phrase in body
            for phrase in ("verify you are human", "checking your browser", "checking if the site connection is secure")
        )

    def stop_if_security_check(self) -> None:
        if self.security_check_showing():
            raise SystemExit(SECURITY_CHECK_MESSAGE)

    # ---------------------------------------------------------------- login

    def looks_logged_in(self) -> bool:
        assert self.page is not None
        url = self.page.url.lower()
        if any(hint in url for hint in NOT_READY_URL_HINTS):
            return False
        return self._first_visible("logged_in_marker", timeout=2500) is not None

    def ensure_logged_in(self, timeout_seconds: int = 420) -> bool:
        """Open Handshake and wait for the student to finish signing in."""
        assert self.page is not None
        try:
            self.page.goto(f"{self.base}/home", wait_until="domcontentloaded")
        except PlaywrightTimeout:
            pass
        self._settle()
        self.stop_if_security_check()

        if self.looks_logged_in():
            print("Already signed in to Handshake.")
            return True

        print("\n" + "=" * 68)
        print("Finish signing in to Handshake in the browser window that opened,")
        print("including any Handshake setup screens it shows you.")
        print("Use your normal school login. This tool never sees your password.")
        print("=" * 68 + "\n")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            self.stop_if_security_check()
            if self.looks_logged_in():
                print("Signed in. Session saved for future runs.\n")
                return True
            self.page.wait_for_timeout(2000)

        print("Timed out waiting for sign-in.")
        return False

    # ---------------------------------------------------------------- searching

    def _explicit_template(self) -> str:
        return str(self.config.get("search_url_template") or "").strip()

    def collect_job_ids(
        self,
        queries: list[str],
        max_pages: int = 3,
        want: int | None = None,
        skip: Callable[[str], bool] | None = None,
        deepest_page: int = 30,
    ) -> list[str]:
        """Run each search query and gather unique posting ids in result order.

        Reads `max_pages` pages of every search first. With `want`, it then keeps
        going deeper into each search, `max_pages` more at a time, until it has
        found `want` postings worth reviewing (those `skip` doesn't rule out) or
        every search has run out, stopping at `deepest_page`.
        """
        assert self.page is not None
        found: list[str] = []
        seen: set[str] = set()
        next_page = {query: 1 for query in queries}
        finished: set[str] = set()

        def enough() -> bool:
            return bool(want) and sum(1 for i in found if not (skip and skip(i))) >= int(want)

        limit = max_pages
        while True:
            for query in queries:
                if query in finished or next_page[query] > limit:
                    continue
                first = next_page[query]
                if first == 1:
                    print(f"  searching: {query!r}")
                elif self._explicit_template() or self._learned_template:
                    print(f"  searching deeper: {query!r} from page {first}")
                else:
                    finished.add(query)  # deeper pages can only be reached by address
                    continue
                while next_page[query] <= limit:
                    page_number = next_page[query]
                    next_page[query] += 1
                    if not self._open_results(query, page_number):
                        finished.add(query)
                        break
                    self._load_more_results()

                    ids_on_page = self._job_ids_on_page()
                    new_ids = [i for i in ids_on_page if i not in seen]
                    for job_id in new_ids:
                        seen.add(job_id)
                        found.append(job_id)
                    print(f"    page {page_number}: {len(ids_on_page)} listed, {len(new_ids)} new")

                    # Stop on an empty page, or when "next page" just repeats results.
                    if not ids_on_page or (page_number > 1 and not new_ids):
                        finished.add(query)
                        break
                    if limit > max_pages and enough():
                        return found  # every search got its first pages; that's enough

            if not want or enough() or limit >= deepest_page or all(q in finished for q in queries):
                return found
            limit = min(deepest_page, limit + max_pages)

    def _open_results(self, query: str, page_number: int) -> bool:
        """Put the browser on results page `page_number` for `query`."""
        assert self.page is not None
        explicit = self._explicit_template()
        template = explicit or self._learned_template

        if template:
            url = template.format(base=self.base, query=quote_plus(query), page=page_number)
            try:
                self.page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                print(f"    page {page_number}: timed out")
                return False
            self._settle(1500)
            self.stop_if_security_check()

            wanted_path = urlparse(url).path.rstrip("/")
            actual_path = urlparse(self.page.url).path.rstrip("/")
            if same_search_page(wanted_path, actual_path):
                return True
            if explicit:
                raise SystemExit(
                    SEARCH_MOVED_MESSAGE.format(wanted=wanted_path or "/", actual=actual_path or "/")
                )
            # A learned address stopped working; relearn it through the page.
            self._learned_template = ""
            if page_number > 1:
                return False

        if page_number == 1:
            return self._search_via_ui(query)
        return self._next_results_page()

    def _search_via_ui(self, query: str) -> bool:
        """Open Handshake's job search page and type the query like a person would."""
        assert self.page is not None
        visited: list[str] = []
        for path in SEARCH_PAGE_PATHS:
            try:
                self.page.goto(self.base + path, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                visited.append(f"{path} (timed out)")
                continue
            self._settle(1500)
            self.stop_if_security_check()

            actual = urlparse(self.page.url).path.rstrip("/") or "/"
            # Handshake jumps straight to the first job, e.g. /job-search/11428461.
            if not same_search_page(path, actual):
                visited.append(f"{path} (went to {actual})")
                continue

            box = self._search_box()
            if box is None:
                visited.append(f"{path} (no search box)")
                continue

            try:
                box.click()
                box.fill("")
                box.fill(query)
                box.press("Enter")
                self._settle(2500)
                self.stop_if_security_check()
                if not self._query_in_address(query):
                    # Some inputs ignore programmatic fills; type it key by key.
                    box.click()
                    box.press("Control+A")
                    box.press_sequentially(query, delay=25)
                    box.press("Enter")
                    self._settle(2500)
                    self.stop_if_security_check()
            except SystemExit:
                raise
            except Exception as exc:
                visited.append(f"{path} (typing failed: {type(exc).__name__})")
                continue
            self._apply_search_filters()
            self._learn_template(query)
            return True

        raise SystemExit(SEARCH_NOT_FOUND_MESSAGE.format(tried="\n  ".join(visited)))

    # ------------------------------------------------------------ search filters

    def _apply_search_filters(self) -> None:
        """Apply Handshake's own job type and location filters to the current search.

        Handshake keeps both in the address (checked on the live site):
        internships are jobType=3; jobs are jobType=9 with employmentTypes=1
        (full-time) and 2 (part time); location is a locationFilter value holding
        Handshake's own place and a distance such as "25mi". The place has to
        come from Handshake's city suggestions, so it is picked in the page; the
        rest is written into the address. The search address learned afterwards
        keeps all of it for every later search and page.
        """
        assert self.page is not None
        looking_for = str(self.config.get("looking_for", "internships")).lower()
        near = str(self.config.get("near_location") or "").strip()
        miles = max(1, min(100, int(self.config.get("within_miles") or 25)))

        if near:
            ok, detail = self._pick_location(near)
            if not ok:
                raise SystemExit(LOCATION_FILTER_MESSAGE.format(near=near, detail=detail))

        if looking_for == "jobs" or near:
            adjusted = filtered_search_url(self.page.url, looking_for, miles if near else None)
            if adjusted != self.page.url:
                try:
                    self.page.goto(adjusted, wait_until="domcontentloaded")
                except PlaywrightTimeout:
                    pass
                self._settle(2000)
                self.stop_if_security_check()

        if near:
            place = location_label(self.page.url)
            if not place:
                raise SystemExit(LOCATION_FILTER_MESSAGE.format(
                    near=near, detail="Handshake did not keep the location in the search"))
            print(f"    location filter: within {miles} miles of {place}")
        if looking_for == "jobs":
            print("    job type filter: full-time and part-time jobs")

    def _visible(self, locator: Locator) -> Locator | None:
        try:
            for index in range(min(locator.count(), 6)):
                if locator.nth(index).is_visible():
                    return locator.nth(index)
        except Exception:
            pass
        return None

    def _suggestions_for(self, box: Locator) -> Locator:
        """The options of the list this box controls, not every option on the page.

        Handshake keeps other dropdowns (sort order, saved searches) in the page,
        so reading every option picked "Most relevant" instead of a city.
        """
        assert self.page is not None
        try:
            controls = (box.get_attribute("aria-controls") or "").split()
        except Exception:
            controls = []
        if controls:
            selector = ", ".join(f'[id="{c}"] [role=option]' for c in controls)
            return self.page.locator(selector)
        return self.page.locator("[role=listbox]:visible [role=option]")

    @staticmethod
    def _visible_texts(options: Locator) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        try:
            for index in range(min(options.count(), 20)):
                option = options.nth(index)
                if option.is_visible():
                    text = " ".join(option.inner_text().split())
                    if text:
                        found.append((index, text))
        except Exception:
            pass
        return found

    def _choose_suggestion(
        self, options: Locator, city: str, state: str, wait_seconds: float
    ) -> tuple[int, str] | None:
        """Wait for suggestions and pick the city, in the right state when one was given."""
        assert self.page is not None
        wanted = city.lower()
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            texts = self._visible_texts(options)
            starts = [(i, t) for i, t in texts if wanted and t.lower().startswith(wanted)]
            if state:
                in_state = [(i, t) for i, t in starts if re.search(rf"{re.escape(state)}", t, re.I)]
                if in_state:
                    return in_state[0]
            elif starts:
                return starts[0]
            if texts and time.time() > deadline - 3:
                return starts[0] if starts else None
            self.page.wait_for_timeout(300)
        return None

    def _pick_location(self, near: str) -> tuple[bool, str]:
        """Open Handshake's Location filter, type the city and pick its suggestion."""
        assert self.page is not None
        page = self.page
        location_name = re.compile(r"^\s*location\b", re.IGNORECASE)

        box = self._visible(page.get_by_role("combobox", name=location_name))
        if box is None:
            opener = self._visible(page.get_by_role("button", name=location_name))
            if opener is None:
                return False, "the Location filter button was not found"
            try:
                opener.click()
                page.wait_for_timeout(1000)
            except Exception as exc:
                return False, f"opening the Location filter failed ({type(exc).__name__})"
            box = self._visible(page.get_by_role("combobox", name=location_name))
        if box is None:
            box = self._visible(page.locator("input[placeholder*='city' i]"))
        if box is None:
            return False, "the Location search box was not found"

        options = self._suggestions_for(box)
        city = near.split(",")[0].strip()
        state = place_state(near)
        # Handshake's place search often finds nothing for "Baltimore, MD" but
        # does for "Baltimore", so fall back to the city alone.
        attempts = [near] if city.lower() == near.strip().lower() else [near, city]
        choice: tuple[int, str] | None = None
        for attempt, typed in enumerate(attempts):
            try:
                box.click()
                box.fill("")
                box.press_sequentially(typed, delay=40)
            except Exception as exc:
                return False, f"typing the city failed ({type(exc).__name__})"
            last_try = attempt == len(attempts) - 1
            choice = self._choose_suggestion(options, city, state, wait_seconds=12 if last_try else 6)
            if choice is not None:
                break
        if choice is None:
            return False, "Handshake suggested no places for it"

        try:
            options.nth(choice[0]).click()
        except Exception as exc:
            return False, f"choosing '{choice[1]}' failed ({type(exc).__name__})"
        page.wait_for_timeout(1500)
        if not location_label(page.url):
            # Some filter panels only update the search after pressing Apply.
            apply_button = self._visible(page.locator("[role=dialog]").get_by_role("button", name=re.compile(r"^\s*apply\s*$", re.I)))
            if apply_button is not None:
                try:
                    apply_button.click()
                except Exception:
                    pass
        self._settle(2000)
        self.stop_if_security_check()
        try:
            page.keyboard.press("Escape")
        except Exception:
            pass
        return True, choice[1]

    def _query_in_address(self, query: str) -> bool:
        assert self.page is not None
        wanted = " ".join(query.lower().split())
        pairs = parse_qsl(urlparse(self.page.url).query, keep_blank_values=True)
        return any(" ".join(v.lower().split()) == wanted for _, v in pairs)

    def _search_box(self) -> Locator | None:
        assert self.page is not None
        located = self._first_visible("search_input", timeout=4000)
        if located is not None:
            return located
        try:
            box = self.page.get_by_role("searchbox").first
            if box.count() > 0 and box.is_visible() and box.is_editable():
                return box
        except Exception:
            pass
        return None

    def _learn_template(self, query: str) -> None:
        """Turn the address Handshake produced for a search into a reusable template."""
        assert self.page is not None
        parsed = urlparse(self.page.url)
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        wanted = " ".join(query.lower().split())

        query_key = next(
            (k for k, v in pairs if " ".join(v.lower().split()) == wanted), None
        )
        if query_key is None:
            return  # the search is not reflected in the address; keep typing it

        parts: list[str] = []
        has_page = False
        for key, value in pairs:
            safe_key = quote_plus(key).replace("{", "{{").replace("}", "}}")
            if key == query_key:
                parts.append(f"{safe_key}={{query}}")
            elif key.lower() == "page":
                parts.append(f"{safe_key}={{page}}")
                has_page = True
            else:
                safe_value = quote_plus(value).replace("{", "{{").replace("}", "}}")
                parts.append(f"{safe_key}={safe_value}")
        if not has_page:
            parts.append("page={page}")
        # Handshake's Internship filter, in case the checkbox click didn't stick.
        internships = str(self.config.get("looking_for", "internships")).lower() == "internships"
        if internships and self.config.get("internship_only", True) and not any(k == "jobType" for k, _ in pairs):
            parts.append("jobType=3")

        # Drop the auto-selected job number: /job-search/11428461 -> /job-search
        search_path = re.sub(r"/\d+$", "", parsed.path.rstrip("/"))
        path = search_path.replace("{", "{{").replace("}", "}}")
        self._learned_template = "{base}" + path + "?" + "&".join(parts)
        print(f"    learned search address: {self._learned_template}")

    def _next_results_page(self) -> bool:
        """Click a 'next page' control on the current results, if there is one."""
        assert self.page is not None
        before = self.page.url
        candidates = []
        try:
            candidates.append(self.page.get_by_role("button", name=NEXT_TEXT).first)
            candidates.append(self.page.get_by_role("link", name=NEXT_TEXT).first)
        except Exception:
            pass
        candidates.append(self.page.locator("[aria-label*='next' i]").first)

        for control in candidates:
            try:
                if control.count() == 0 or not control.is_visible() or not control.is_enabled():
                    continue
                if (control.get_attribute("aria-disabled") or "").lower() == "true":
                    continue
                control.click()
                self._settle(2000)
                self.stop_if_security_check()
                return True
            except Exception:
                continue
        return self.page.url != before

    def _load_more_results(self) -> None:
        """Scroll the page and any scrollable result lists so lazy cards render."""
        assert self.page is not None
        for _ in range(4):
            try:
                self.page.mouse.wheel(0, 2400)
                self.page.evaluate(SCROLL_LISTS_JS)
            except Exception:
                pass
            self.page.wait_for_timeout(600)

    def _job_ids_on_page(self) -> list[str]:
        assert self.page is not None
        ids: list[str] = []
        seen: set[str] = set()

        def add(text: str) -> None:
            match = JOB_ID_RE.search(text or "")
            if match and match.group(1) not in seen:
                seen.add(match.group(1))
                ids.append(match.group(1))

        # Handshake labels each result card with its job number, e.g.
        # data-hook="job-result-card | 11375870". This is the most direct source.
        try:
            hooks = self.page.eval_on_selector_all(
                "[data-hook^='job-result-card']",
                "nodes => nodes.map(n => n.getAttribute('data-hook') || '')",
            )
        except Exception:
            hooks = []
        for hook in hooks:
            match = re.search(r"\|\s*(\d+)\s*$", hook or "")
            if match and match.group(1) not in seen:
                seen.add(match.group(1))
                ids.append(match.group(1))
        if ids:
            return ids

        for selector in self._sel("job_card_link"):
            try:
                hrefs = self.page.eval_on_selector_all(
                    selector, "nodes => nodes.map(n => n.getAttribute('href') || '')"
                )
            except Exception:
                continue
            for href in hrefs:
                add(href)
            if ids:
                return ids

        # Some result lists are clickable cards without links: click each one
        # and read the job id from the address. This only selects a result.
        start_url = self.page.url
        add(start_url)
        for selector in self._sel("job_card_clickable"):
            try:
                cards = self.page.locator(selector)
                count = min(cards.count(), 25)
            except Exception:
                continue
            if count == 0:
                continue
            for index in range(count):
                try:
                    cards.nth(index).click()
                    self.page.wait_for_timeout(900)
                    add(self.page.url)
                    if urlparse(self.page.url).path != urlparse(start_url).path and not JOB_ID_RE.search(self.page.url):
                        self.page.go_back()
                        self._settle(800)
                except Exception:
                    continue
            if len(ids) > 1:
                break
        return ids

    # ------------------------------------------------------------- job details

    def load_job(self, job_id: str) -> Job:
        assert self.page is not None
        job = Job(job_id=job_id, url="")

        for pattern in JOB_PAGE_PATTERNS:
            url = pattern.format(base=self.base, id=job_id)
            try:
                self.page.goto(url, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                continue
            self._settle(900)
            self.stop_if_security_check()
            if self._on_job_id(job_id):
                job.url = url
                break
        else:
            job.url = JOB_PAGE_PATTERNS[0].format(base=self.base, id=job_id)
            job.error = "job page could not be opened"
            return job

        details = self._mark_detail_root()
        if self._expand_description():
            details = self._mark_detail_root() or details
        job.title = details.get("title", "")
        if not job.title:
            title_block = self._text("job_title", limit=300)
            job.title = title_block.splitlines()[0].strip() if title_block else ""

        job.employer = details.get("employer", "")
        if not job.employer:
            employer_block = self._text("job_employer", limit=200)
            job.employer = employer_block.splitlines()[0].strip() if employer_block else ""

        # Handshake lists it under "At a glance", e.g. "Onsite, based in Baltimore, MD".
        job.location = location_from_text(details.get("text", ""))
        if not job.location:
            location_block = self._text("job_location", limit=400)
            job.location = " / ".join(
                line.strip() for line in location_block.splitlines()[:3] if line.strip()
            )

        job.description = details.get("text", "") or self._text("job_description", limit=24_000)
        job.apply_kind = self._detect_apply_kind()
        return job

    def _expand_description(self) -> bool:
        """Click 'Show more' inside the job panel so the full description is read."""
        scope = self._detail_scope()
        try:
            buttons = scope.locator("button[aria-label^='Show more' i]")
            count = min(buttons.count(), 3)
        except Exception:
            return False
        clicked = False
        for index in range(count):
            try:
                button = buttons.nth(index)
                if button.is_visible():
                    button.click()
                    clicked = True
            except Exception:
                continue
        if clicked and self.page is not None:
            self.page.wait_for_timeout(500)
        return clicked

    def _mark_detail_root(self) -> dict[str, str]:
        """Find the job's own detail panel, separate from any results list beside it."""
        assert self.page is not None
        try:
            result = self.page.evaluate(DETAIL_ROOT_JS)
        except Exception:
            return {}
        return {k: str(v or "") for k, v in (result or {}).items()}

    def _detail_scope(self) -> Locator:
        assert self.page is not None
        marked = self.page.locator("[data-hsbot-detail='1']")
        try:
            if marked.count() > 0:
                return marked.first
        except Exception:
            pass
        return self.page.locator("body")

    def _detect_apply_kind(self) -> str:
        assert self.page is not None
        scope = self._detail_scope()
        try:
            text = scope.inner_text()[:8000]
        except Exception:
            text = ""

        lowered = text.lower()
        if "you applied" in lowered or "application submitted" in lowered or re.search(r"\bapplied on\b", lowered):
            return "already_applied"
        if self._visible_button(scope, APPLIED_BUTTON_TEXT) is not None:
            return "already_applied"
        if "no longer accepting applications" in lowered or "applications closed" in lowered:
            return "closed"

        if EXTERNAL_TEXT.search(text):
            return "external"
        try:
            links = scope.get_by_role("link", name=re.compile(r"apply", re.IGNORECASE))
            for index in range(min(links.count(), 5)):
                href = links.nth(index).get_attribute("href") or ""
                host = urlparse(href).netloc.lower()
                if host and "joinhandshake.com" not in host and host != urlparse(self.base).netloc:
                    return "external"
        except Exception:
            pass
        if self._first_visible("external_apply_marker", timeout=800) is not None:
            return "external"

        if self._apply_button() is not None:
            return "quick"
        return "unknown"

    def _apply_button(self) -> Locator | None:
        assert self.page is not None
        # Role-based lookup first; it survives class name churn. Look inside the
        # job's own panel so a button on a neighbouring card is never used.
        for scope in (self._detail_scope(), self.page):
            try:
                button = scope.get_by_role("button", name=APPLY_TEXT).first
                if button.count() > 0 and button.is_visible():
                    return button
            except Exception:
                continue
        return self._first_visible("apply_button", timeout=1500)

    # ------------------------------------------------------------------ applying

    def apply(
        self,
        job: Job,
        documents: Documents,
        dry_run: bool = False,
        answers: list[dict[str, Any]] | None = None,
        ask: Callable[[str], str | None] | None = None,
    ) -> tuple[str, str]:
        """Attempt one application. Returns (status, note).

        Statuses: applied, dry_run, needs_manual, uncertain, failed, and
        skipped_external / skipped_already_applied / skipped_closed.
        Nothing is ever submitted while a required field is still empty.
        """
        assert self.page is not None

        if job.apply_kind == "external":
            return "skipped_external", "posting redirects to an external site"

        # Always act on this job's own page. Scoring loads many postings in a
        # row, so the browser is usually sitting on a different one.
        if not self._on_job_page(job):
            try:
                self.page.goto(job.url, wait_until="domcontentloaded")
            except PlaywrightTimeout:
                return "failed", "job page timed out"
            self._settle(900)
            if not self._on_job_page(job):
                return "failed", f"could not open job page {job.url}"
            self.stop_if_security_check()
            self._mark_detail_root()
            job.apply_kind = self._detect_apply_kind()
            if job.apply_kind == "external":
                return "skipped_external", "posting redirects to an external site"

        if job.apply_kind == "already_applied":
            return "skipped_already_applied", "Handshake shows an existing application"
        if job.apply_kind == "closed":
            return "skipped_closed", "no longer accepting applications"

        button = self._apply_button()
        if button is None:
            return "failed", "could not find an Apply button"

        # "Quick apply" can send the application in one click, so a practice
        # run must never press it.
        label = ""
        try:
            label = " ".join(
                [(button.inner_text() or ""), (button.get_attribute("aria-label") or "")]
            ).lower()
        except Exception:
            pass
        if dry_run and "quick" in label:
            return "dry_run", "Quick apply posting, not clicked in a practice run because it may submit instantly"

        try:
            button.click()
        except Exception as exc:
            return "failed", f"clicking Apply failed: {type(exc).__name__}"

        dialog = self._first_visible("dialog", timeout=8000)
        if dialog is None:
            # A few postings apply in one click with no modal.
            if self._application_succeeded():
                return ("dry_run" if dry_run else "applied"), "single-click apply"
            if "quick" in label:
                return "uncertain", "Quick apply was clicked and no form appeared; it may already be submitted"
            return "failed", "no application dialog appeared"

        attached, attach_note, missing_documents = self._attach_documents(documents, dry_run)

        answered, refused, learned = self._fill_saved_answers(dialog, answers or [], ask)
        self.last_learned_answers = learned
        if answered:
            attach_note += "; answered " + "; ".join(answered[:4])

        missing = [f"attach your {kind}" for kind in missing_documents]
        missing += [f"{q} (left for you to answer)" for q in refused]
        # A refused question is also an empty required field; report it once.
        refused_text = " ".join(refused).lower()
        missing += [
            field for field in self._missing_required_fields(attached)
            if not refused or field.lower().strip("* ") not in refused_text
        ]
        if missing:
            self._dismiss_dialog()
            note = "unanswered required: " + "; ".join(missing[:5])
            if answered:
                note += " | answered: " + "; ".join(answered[:4])
            return "needs_manual", note

        if dry_run:
            self._dismiss_dialog()
            return "dry_run", f"form ready, not submitted ({attach_note})"

        submit = self._submit_button()
        if submit is None:
            self._dismiss_dialog()
            return "needs_manual", f"Submit is unavailable, the form likely wants more ({attach_note})"

        try:
            submit.click()
        except Exception as exc:
            self._dismiss_dialog()
            return "failed", f"clicking Submit failed: {type(exc).__name__}"

        self._settle(2500)
        if self._application_succeeded():
            return "applied", attach_note
        return "uncertain", f"submitted but no confirmation seen ({attach_note})"

    def _on_job_id(self, job_id: str) -> bool:
        assert self.page is not None
        match = JOB_ID_RE.search(self.page.url or "")
        return bool(match and match.group(1) == job_id)

    def _on_job_page(self, job: Job) -> bool:
        return self._on_job_id(job.job_id)

    # ---------------------------------------------------------------- documents

    def _dialog_root(self) -> Locator | None:
        return self._first_visible("dialog", timeout=1500)

    def _label_for(self, locator: Locator) -> str:
        try:
            return str(locator.evaluate(LABEL_JS) or "")
        except Exception:
            return ""

    @staticmethod
    def _pick_option(options: list[str], kind: str, wanted_name: str) -> str | None:
        """Choose a saved document for `kind` from a list of option labels."""
        usable = [
            o for o in options
            if o.strip() and not re.match(r"^\s*(select|choose|--)", o, re.IGNORECASE)
        ]
        if wanted_name:
            for option in usable:
                if wanted_name.lower() in option.lower():
                    return option
        for keyword in DOCUMENT_KEYWORDS.get(kind, ()):
            for option in usable:
                if keyword in option.lower():
                    return option
        return None

    @staticmethod
    def _visible_button(scope: Locator | Page, name: re.Pattern[str]) -> Locator | None:
        try:
            buttons = scope.get_by_role("button", name=name)
            for index in range(min(buttons.count(), 10)):
                if buttons.nth(index).is_visible():
                    return buttons.nth(index)
        except Exception:
            pass
        return None

    @staticmethod
    def _slot_filled(fieldset: Locator) -> str | None:
        """Name of the document attached to a Handshake document section, if any."""
        try:
            status = fieldset.locator("[data-status='positive']")
            for index in range(min(status.count(), 3)):
                if status.nth(index).is_visible():
                    name = status.nth(index).locator("h5").first
                    text = name.inner_text() if name.count() else status.nth(index).inner_text()
                    return " ".join((text or "").split())[:80] or "attached"
        except Exception:
            pass
        return None

    def _wait_for_slot(self, fieldset: Locator, seconds: float) -> str | None:
        assert self.page is not None
        deadline = time.time() + seconds
        while time.time() < deadline:
            filled = self._slot_filled(fieldset)
            if filled:
                return filled
            self.page.wait_for_timeout(300)
        return None

    def _choose_saved_document(self, fieldset: Locator, kind: str, wanted: str) -> str | None:
        """Pick a saved document from a section's search box. Returns its name."""
        assert self.page is not None
        box = fieldset.locator("input[role='combobox'], input[type='search']").first
        try:
            if box.count() == 0 or not box.is_visible():
                return None
            box.click()
            if wanted:
                box.fill(wanted)
            listbox_id = box.get_attribute("aria-controls") or ""
        except Exception:
            return None

        options = (
            self.page.locator(f"[id='{listbox_id}'] [role='option']")
            if listbox_id
            else fieldset.locator("[role='option']")
        )
        deadline = time.time() + 4
        while time.time() < deadline:
            try:
                if options.count() > 0:
                    break
            except Exception:
                pass
            self.page.wait_for_timeout(250)

        try:
            texts = [" ".join((t or "").split()) for t in options.all_inner_texts()]
        except Exception:
            texts = []
        usable = [(i, t) for i, t in enumerate(texts) if t and not re.search(r"no (results|documents)", t, re.I)]
        if not usable:
            self.page.keyboard.press("Escape")
            return None

        choice = None
        if wanted:
            choice = next(((i, t) for i, t in usable if wanted.lower() in t.lower()), None)
        else:
            keywords = DOCUMENT_KEYWORDS.get(kind, ())
            choice = next(((i, t) for i, t in usable if any(k in t.lower() for k in keywords)), usable[0])
        if choice is None:
            self.page.keyboard.press("Escape")
            return None

        try:
            options.nth(choice[0]).click()
        except Exception:
            return None
        return self._wait_for_slot(fieldset, 6) and choice[1]

    def _replace_with_upload(self, fieldset: Locator, path: Path) -> str | None:
        """Detach whatever a document section holds and upload `path` instead.

        This only changes the document used for this one application. The
        student's default resume on Handshake is left as it is.
        """
        assert self.page is not None
        if self._slot_filled(fieldset):
            remove = fieldset.locator(
                "[data-status='positive'] button[aria-label='Close'], "
                "[data-status='positive'] button[aria-label*='remove' i]"
            ).first
            try:
                if remove.count() == 0:
                    return None
                remove.click()
            except Exception:
                return None
            deadline = time.time() + 6
            while time.time() < deadline and self._slot_filled(fieldset):
                self.page.wait_for_timeout(250)
            if self._slot_filled(fieldset):
                return None

        upload = fieldset.locator("input[type='file']").first
        deadline = time.time() + 6
        while time.time() < deadline:
            try:
                if upload.count():
                    break
            except Exception:
                pass
            self.page.wait_for_timeout(250)
        try:
            upload.set_input_files(str(path))
        except Exception:
            return None
        return self._wait_for_slot(fieldset, 30)

    def _attach_handshake_sections(
        self, dialog: Locator, documents: Documents, dry_run: bool = False
    ) -> tuple[bool, set[str], list[str], list[str]]:
        """Fill Handshake's "Attach your ..." document sections.

        Resumes and transcripts may use any saved document of that type. A cover
        letter is only attached when the student named one or gave a file, so a
        letter written for one employer never goes to another.
        Returns (layout found, kinds attached, notes, kinds still missing).
        """
        attached: set[str] = set()
        notes: list[str] = []
        missing: list[str] = []
        found = False

        try:
            fieldsets = dialog.locator("fieldset")
            count = min(fieldsets.count(), 8)
        except Exception:
            return False, attached, notes, missing

        for index in range(count):
            fieldset = fieldsets.nth(index)
            try:
                heading = fieldset.locator("h4, legend, h3").first
                title = " ".join((heading.inner_text() if heading.count() else "").split())
            except Exception:
                continue
            if not re.search(r"\battach\b|\bupload\b|resume|cover letter|transcript|document", title, re.I):
                continue
            found = True
            kind = classify_document_label(title)
            label = re.sub(r"^\s*(attach|upload)\s+(your|a|an)?\s*", "", title, flags=re.I).strip().lower() or "document"
            key = kind if kind != "unknown" else label

            tailored = documents.path_for("resume") if kind == "resume" and documents.tailored_resume else None
            if tailored is not None:
                if dry_run:
                    attached.add(key)
                    notes.append(f"resume: tailored {tailored.name} ready, not uploaded in a practice run")
                    continue
                uploaded = self._replace_with_upload(fieldset, tailored)
                if uploaded:
                    attached.add(key)
                    notes.append(f"resume: tailored '{uploaded}'")
                else:
                    missing.append("tailored resume (upload did not finish)")
                continue

            filled = self._slot_filled(fieldset)
            if filled:
                attached.add(key)
                notes.append(f"{label}: '{filled}'")
                continue

            chosen = None
            if kind in DOCUMENT_KINDS:
                wanted = documents.name_for(kind)
                if wanted or kind in ("resume", "transcript"):
                    chosen = self._choose_saved_document(fieldset, kind, wanted)
                if not chosen:
                    path = documents.path_for(kind)
                    upload = fieldset.locator("input[type='file']").first
                    if path is not None and upload.count():
                        try:
                            upload.set_input_files(str(path))
                            chosen = self._wait_for_slot(fieldset, 20)
                            if chosen:
                                chosen = f"uploaded {path.name}"
                        except Exception:
                            chosen = None
                if not chosen and kind == "cover_letter" and documents.cover_letter_writer is not None:
                    written = documents.cover_letter_writer()
                    if written is not None and dry_run:
                        attached.add(key)
                        notes.append(f"cover letter: {written.name} written, not uploaded in a practice run")
                        continue
                    upload = fieldset.locator("input[type='file']").first
                    if written is not None and upload.count():
                        try:
                            upload.set_input_files(str(written))
                            if self._wait_for_slot(fieldset, 20):
                                chosen = f"uploaded {written.name}, written for this job"
                        except Exception:
                            chosen = None

            if chosen:
                attached.add(key)
                notes.append(f"{label}: '{chosen}'")
            else:
                missing.append(label)

        return found, attached, notes, missing

    def _attach_documents(
        self, documents: Documents, dry_run: bool = False
    ) -> tuple[set[str], str, list[str]]:
        """Fill every document slot in the dialog.

        Returns (kinds attached, note, document sections still empty).
        """
        assert self.page is not None
        dialog = self._dialog_root()
        scope = dialog if dialog is not None else self.page.locator("body")

        if dialog is not None:
            found, attached, notes, missing = self._attach_handshake_sections(dialog, documents, dry_run)
            if found:
                note = ", ".join(notes) if notes else "no documents attached"
                return attached, note, missing

        attached = set()
        notes = []

        # 1. Dropdowns of documents already saved on Handshake.
        selects = scope.locator("select")
        for index in range(min(selects.count(), 10)):
            select = selects.nth(index)
            label = self._label_for(select)
            kind = classify_document_label(label)
            try:
                option_texts = [
                    (t or "").strip()
                    for t in select.locator("option").all_inner_texts()
                ]
            except Exception:
                continue

            # Unlabeled document pickers are assumed to be the resume slot,
            # but only when the options actually look like documents.
            if kind == "unknown":
                if "resume" in attached or not any(
                    k in o.lower() for o in option_texts for k in ("resume", ".pdf", ".docx")
                ):
                    continue
                kind = "resume"
            if kind in attached:
                continue

            choice = self._pick_option(option_texts, kind, documents.name_for(kind))
            if choice is None:
                continue
            try:
                select.select_option(label=choice)
                attached.add(kind)
                notes.append(f"{kind.replace('_', ' ')}: '{choice}'")
            except Exception:
                continue

        # 2. Radio or card lists of saved documents.
        radios = scope.locator("input[type='radio'], [role='radio']")
        for index in range(min(radios.count(), 20)):
            radio = radios.nth(index)
            label = self._label_for(radio)
            kind = classify_document_label(label)
            candidates = [kind] if kind != "unknown" else list(DOCUMENT_KINDS)
            for candidate in candidates:
                if candidate in attached:
                    continue
                if self._pick_option([label], candidate, documents.name_for(candidate)):
                    try:
                        radio.check(force=True)
                        attached.add(candidate)
                        notes.append(f"{candidate.replace('_', ' ')}: '{label[:50]}'")
                    except Exception:
                        pass
                    break

        # 3. Upload local files into any slot still empty.
        file_inputs = scope.locator("input[type='file']")
        for index in range(min(file_inputs.count(), 6)):
            file_input = file_inputs.nth(index)
            # The input's name often says what it is for, e.g. "file-Cover Letter".
            kind = classify_document_label(
                f"{self._label_for(file_input)} {file_input.get_attribute('name') or ''}"
            )
            if kind == "unknown":
                kind = "resume"
            if kind in attached:
                continue
            path = documents.path_for(kind)
            if path is None:
                continue
            try:
                file_input.set_input_files(str(path))
                self.page.wait_for_timeout(2500)
                attached.add(kind)
                notes.append(f"{kind.replace('_', ' ')}: uploaded {path.name}")
            except Exception:
                continue

        note = ", ".join(notes) if notes else "no document fields filled"
        return attached, note, []

    @staticmethod
    def _saved_answer(label: str, answers: list[dict[str, Any]]) -> dict[str, Any] | None:
        text = " ".join(label.lower().split())
        for answer in answers:
            for phrase in answer.get("match", []):
                if str(phrase).lower() in text:
                    return answer
        return None

    def _fill_saved_answers(
        self,
        dialog: Locator,
        answers: list[dict[str, Any]],
        ask: Callable[[str], str | None] | None = None,
    ) -> tuple[list[str], list[str], list[dict[str, Any]]]:
        """Answer questions from the student's saved answers.

        Personal questions (sponsorship, demographics, pay and so on) are
        answered only from the student's own saved words, or from `ask`, which
        puts the question to them during the run. Nothing is ever guessed.

        Returns (what was filled, questions left unanswered, newly learned answers).
        """
        assert self.page is not None
        filled: list[str] = []
        refused: list[str] = []
        learned: list[dict[str, Any]] = []
        if not answers and ask is None:
            return filled, refused, learned

        try:
            controls = dialog.locator("input, textarea, select")
            count = min(controls.count(), 40)
        except Exception:
            return filled, refused, learned

        handled_groups: set[str] = set()
        for index in range(count):
            control = controls.nth(index)
            try:
                if not control.is_visible() or not control.is_editable():
                    continue
                kind = (control.get_attribute("type") or control.evaluate("n => n.tagName")).lower()
            except Exception:
                continue
            if kind in {"hidden", "file", "submit", "button", "reset", "image", "checkbox"}:
                continue

            own_label = " ".join(self._label_for(control).split())[:120]
            question = own_label
            if kind == "radio":
                try:
                    question = " ".join(str(control.evaluate(GROUP_QUESTION_JS) or "").split())[:160]
                except Exception:
                    question = ""
            label = question or own_label
            if not label or DOCUMENT_FIELD.search(label):
                continue
            if NEVER_FILL.search(label):
                refused.append(f"{label} (the assistant never fills this in)")
                continue

            answer = self._saved_answer(label, answers)
            if answer is None and ask is not None and kind != "radio":
                given = ask(label)
                if given:
                    answer = {"match": [label.strip("* ").lower()[:60]], "value": given}
                    learned.append(dict(answer, sensitive=bool(SENSITIVE_QUESTION.search(label))))
            if answer is None and kind == "radio":
                # Ask once per group, then match the option to the answer.
                name = control.get_attribute("name") or label
                if ask is not None and name not in handled_groups:
                    given = ask(f"{label} (options include '{own_label}')")
                    if given:
                        answer = {"match": [label.strip("* ").lower()[:60]], "value": given}
                        learned.append(dict(answer, sensitive=bool(SENSITIVE_QUESTION.search(label))))
                        answers = answers + [answer]
            if answer is None:
                if SENSITIVE_QUESTION.search(label):
                    refused.append(label)
                continue

            try:
                if kind == "radio":
                    name = control.get_attribute("name") or label
                    if name in handled_groups:
                        continue
                    if own_label.strip().lower() != str(answer["value"]).strip().lower():
                        continue
                    control.check(force=True)
                    handled_groups.add(name)
                    label = f"{label} -> {own_label}"
                elif kind == "select":
                    wanted = str(answer["value"]).strip().lower()
                    options = [" ".join(o.split()) for o in control.locator("option").all_inner_texts()]
                    match = next((o for o in options if o.strip().lower() == wanted), None)
                    match = match or next((o for o in options if wanted and wanted in o.lower()), None)
                    if match is None:
                        continue
                    control.select_option(label=match)
                else:
                    if (control.input_value() or "").strip():
                        continue
                    control.fill(str(answer["value"]))
            except Exception:
                continue
            filled.append(f"{label}: {answer['value']}")

        return filled, refused, learned

    def _missing_required_fields(self, attached: set[str]) -> list[str]:
        """Labels of required fields still empty, ignoring satisfied document slots."""
        dialog = self._dialog_root()
        if dialog is None:
            return []
        try:
            labels = list(dialog.evaluate(MISSING_REQUIRED_JS) or [])
        except Exception:
            return []

        missing: list[str] = []
        for label in labels:
            kind = classify_document_label(label)
            # A saved-document choice satisfies the matching upload box too.
            if kind in attached:
                continue
            clean = " ".join(str(label).replace("*", " ").split())
            if clean and clean not in missing:
                missing.append(clean)
        return missing

    def _submit_button(self) -> Locator | None:
        assert self.page is not None
        dialog = self._dialog_root()
        for scope in ([dialog] if dialog is not None else []) + [self.page]:
            button = self._visible_button(scope, SUBMIT_TEXT)
            try:
                if button is not None and button.is_enabled():
                    return button
            except Exception:
                continue
        locator = self._first_visible("submit_button", timeout=2000)
        if locator is not None:
            try:
                if locator.is_enabled():
                    return locator
            except Exception:
                return locator
        return None

    def _application_succeeded(self) -> bool:
        assert self.page is not None
        if self._first_visible("success_marker", timeout=3000) is not None:
            return True
        # Once applied, Handshake offers to withdraw the application instead.
        if self._visible_button(self._detail_scope(), APPLIED_BUTTON_TEXT) is not None:
            return True
        try:
            body = self.page.inner_text("body")[:5000].lower()
        except Exception:
            return False
        return any(
            phrase in body
            for phrase in (
                "application submitted",
                "you applied",
                "successfully applied",
                "your application was submitted",
            )
        )

    def _dismiss_dialog(self) -> None:
        assert self.page is not None
        try:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
        except Exception:
            pass
