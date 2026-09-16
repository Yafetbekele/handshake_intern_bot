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
from typing import Any
from urllib.parse import quote_plus

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

JOB_ID_RE = re.compile(r"/(?:stu/)?jobs/(\d+)")

APPLY_TEXT = re.compile(r"^\s*(quick\s+apply|apply\s+now|apply)\s*$", re.IGNORECASE)
EXTERNAL_TEXT = re.compile(
    r"apply\s+externally|apply\s+on\s+(company|employer)|external\s+application",
    re.IGNORECASE,
)
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

    def name_for(self, kind: str) -> str:
        return str(getattr(self, f"{kind}_name", "") or "").strip()

    def path_for(self, kind: str) -> Path | None:
        raw = str(getattr(self, f"{kind}_path", "") or "").strip()
        if not raw:
            return None
        path = Path(raw).expanduser()
        return path if path.exists() else None


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
            args=["--disable-blink-features=AutomationControlled"],
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
                    locator = self.page.locator(selector).first
                    if locator.count() > 0 and locator.is_visible():
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

    # ---------------------------------------------------------------- login

    def looks_logged_in(self) -> bool:
        assert self.page is not None
        url = self.page.url.lower()
        if "login" in url or "sign_in" in url or "saml" in url or "sso" in url:
            return False
        return self._first_visible("logged_in_marker", timeout=2500) is not None

    def ensure_logged_in(self, timeout_seconds: int = 420) -> bool:
        """Open Handshake and wait for the student to finish signing in."""
        assert self.page is not None
        try:
            self.page.goto(f"{self.base}/stu/postings", wait_until="domcontentloaded")
        except PlaywrightTimeout:
            pass
        self._settle()

        if self.looks_logged_in():
            print("Already signed in to Handshake.")
            return True

        print("\n" + "=" * 68)
        print("Sign in to Handshake in the browser window that just opened.")
        print("Use your normal school login. This tool never sees your password.")
        print("Waiting for the Handshake job page to load...")
        print("=" * 68 + "\n")

        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self.looks_logged_in():
                print("Signed in. Session saved for future runs.\n")
                return True
            self.page.wait_for_timeout(2000)

        print("Timed out waiting for sign-in.")
        return False

    # ---------------------------------------------------------------- searching

    def _search_url(self, query: str, page_number: int) -> str:
        template = str(
            self.config.get(
                "search_url_template",
                "{base}/stu/postings?query={query}&page={page}&per_page=25",
            )
        )
        return template.format(
            base=self.base, query=quote_plus(query), page=page_number
        )

    def collect_job_ids(self, queries: list[str], max_pages: int = 3) -> list[str]:
        """Run each search query and gather unique posting ids in result order."""
        assert self.page is not None
        found: list[str] = []
        seen: set[str] = set()

        for query in queries:
            print(f"  searching: {query!r}")
            for page_number in range(1, max_pages + 1):
                url = self._search_url(query, page_number)
                try:
                    self.page.goto(url, wait_until="domcontentloaded")
                except PlaywrightTimeout:
                    print(f"    page {page_number}: timed out, skipping")
                    continue
                self._settle(1500)

                # Handshake lazy-loads cards, so nudge the list a few times.
                for _ in range(4):
                    self.page.mouse.wheel(0, 2400)
                    self.page.wait_for_timeout(600)

                ids_on_page = self._job_ids_on_page()
                new_ids = [i for i in ids_on_page if i not in seen]
                for job_id in new_ids:
                    seen.add(job_id)
                    found.append(job_id)
                print(f"    page {page_number}: {len(ids_on_page)} listed, {len(new_ids)} new")

                if not ids_on_page:
                    break

        return found

    def _job_ids_on_page(self) -> list[str]:
        assert self.page is not None
        ids: list[str] = []
        seen: set[str] = set()
        for selector in self._sel("job_card_link"):
            try:
                hrefs = self.page.eval_on_selector_all(
                    selector,
                    "nodes => nodes.map(n => n.getAttribute('href') || '')",
                )
            except Exception:
                continue
            for href in hrefs:
                match = JOB_ID_RE.search(href or "")
                if match and match.group(1) not in seen:
                    seen.add(match.group(1))
                    ids.append(match.group(1))
            if ids:
                break
        return ids

    # ------------------------------------------------------------- job details

    def load_job(self, job_id: str) -> Job:
        assert self.page is not None
        url = f"{self.base}/stu/jobs/{job_id}"
        job = Job(job_id=job_id, url=url)

        try:
            self.page.goto(url, wait_until="domcontentloaded")
        except PlaywrightTimeout:
            job.error = "page load timed out"
            return job
        self._settle(900)

        title_block = self._text("job_title", limit=300)
        job.title = title_block.splitlines()[0].strip() if title_block else ""

        employer_block = self._text("job_employer", limit=200)
        job.employer = employer_block.splitlines()[0].strip() if employer_block else ""

        location_block = self._text("job_location", limit=400)
        job.location = " / ".join(
            line.strip() for line in location_block.splitlines()[:3] if line.strip()
        )
        job.description = self._text("job_description", limit=24_000)
        job.apply_kind = self._detect_apply_kind()
        return job

    def _detect_apply_kind(self) -> str:
        assert self.page is not None
        body = ""
        try:
            body = self.page.inner_text("body")[:6000]
        except Exception:
            pass

        lowered = body.lower()
        if "you applied" in lowered or "application submitted" in lowered:
            return "already_applied"
        if "no longer accepting applications" in lowered or "applications closed" in lowered:
            return "closed"

        if EXTERNAL_TEXT.search(body):
            return "external"
        if self._first_visible("external_apply_marker", timeout=800) is not None:
            return "external"

        if self._apply_button() is not None:
            return "quick"
        return "unknown"

    def _apply_button(self) -> Locator | None:
        assert self.page is not None
        # Role-based lookup first; it survives class name churn.
        try:
            button = self.page.get_by_role("button", name=APPLY_TEXT).first
            if button.count() > 0 and button.is_visible():
                return button
        except Exception:
            pass
        return self._first_visible("apply_button", timeout=1500)

    # ------------------------------------------------------------------ applying

    def apply(
        self,
        job: Job,
        documents: Documents,
        dry_run: bool = False,
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

        try:
            button.click()
        except Exception as exc:
            return "failed", f"clicking Apply failed: {type(exc).__name__}"

        dialog = self._first_visible("dialog", timeout=8000)
        if dialog is None:
            # A few postings apply in one click with no modal.
            if self._application_succeeded():
                return ("dry_run" if dry_run else "applied"), "single-click apply"
            return "failed", "no application dialog appeared"

        attached, attach_note = self._attach_documents(documents)

        missing = self._missing_required_fields(attached)
        if missing:
            self._dismiss_dialog()
            return "needs_manual", "unanswered required: " + "; ".join(missing[:5])

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

    def _on_job_page(self, job: Job) -> bool:
        assert self.page is not None
        match = JOB_ID_RE.search(self.page.url or "")
        return bool(match and match.group(1) == job.job_id)

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

    def _attach_documents(self, documents: Documents) -> tuple[set[str], str]:
        """Fill every document slot in the dialog. Returns (kinds attached, note)."""
        assert self.page is not None
        dialog = self._dialog_root()
        scope = dialog if dialog is not None else self.page.locator("body")
        attached: set[str] = set()
        notes: list[str] = []

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
            kind = classify_document_label(self._label_for(file_input))
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
        return attached, note

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
        try:
            button = self.page.get_by_role("button", name=SUBMIT_TEXT).first
            if button.count() > 0 and button.is_visible() and button.is_enabled():
                return button
        except Exception:
            pass
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
