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
        resume_path: str | Path,
        resume_doc_name: str = "",
        dry_run: bool = False,
    ) -> tuple[str, str]:
        """Attempt one application. Returns (status, note)."""
        assert self.page is not None

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

        attach_note = self._attach_resume(resume_path, resume_doc_name)

        if dry_run:
            self._dismiss_dialog()
            return "dry_run", f"form ready, not submitted ({attach_note})"

        submit = self._submit_button()
        if submit is None:
            self._dismiss_dialog()
            return "failed", f"no Submit button found ({attach_note})"

        try:
            submit.click()
        except Exception as exc:
            self._dismiss_dialog()
            return "failed", f"clicking Submit failed: {type(exc).__name__}"

        self._settle(2500)
        if self._application_succeeded():
            return "applied", attach_note
        return "uncertain", f"submitted but no confirmation seen ({attach_note})"

    def _attach_resume(self, resume_path: str | Path, resume_doc_name: str = "") -> str:
        """Pick an existing Handshake resume, or upload the local file."""
        assert self.page is not None
        resume_path = Path(resume_path).expanduser()
        wanted = (resume_doc_name or resume_path.stem).lower()

        # 1. A native <select> of saved documents.
        select = self._first_visible("document_select", timeout=1500)
        if select is not None:
            try:
                options = select.locator("option")
                for index in range(options.count()):
                    label = (options.nth(index).inner_text() or "").strip()
                    if not label or label.lower().startswith("select"):
                        continue
                    if wanted in label.lower() or "resume" in label.lower():
                        select.select_option(label=label)
                        return f"selected saved document '{label}'"
            except Exception:
                pass

        # 2. A radio / card list of saved documents.
        try:
            radios = self.page.locator(
                "div[role='dialog'] input[type='radio'], div[role='dialog'] [role='radio']"
            )
            for index in range(min(radios.count(), 12)):
                radio = radios.nth(index)
                label = ""
                try:
                    label = radio.evaluate(
                        "node => (node.closest('label') || node.parentElement)?.innerText || ''"
                    )
                except Exception:
                    pass
                if wanted in label.lower() or "resume" in label.lower():
                    radio.check(force=True)
                    clean = " ".join(label.split())[:60]
                    return f"selected saved document '{clean}'"
        except Exception:
            pass

        # 3. Upload the local file.
        if resume_path.exists():
            for selector in self._sel("file_input"):
                try:
                    file_input = self.page.locator(selector).first
                    if file_input.count() == 0:
                        continue
                    file_input.set_input_files(str(resume_path))
                    self.page.wait_for_timeout(2500)
                    return f"uploaded {resume_path.name}"
                except Exception:
                    continue

        return "no resume field found; posting may not require one"

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
