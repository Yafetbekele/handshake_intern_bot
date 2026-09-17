"""Checks that Remove on the served apply-yourself list takes postings off for good.

Uses a temporary list, never the student's real one. Run from the project root:
    python tests/list_server_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import list_server
from storage import ManualList

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


def post(url: str, body: dict, headers: dict[str, str]) -> int:
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


with tempfile.TemporaryDirectory() as tmp:
    folder = Path(tmp) / "Internships to apply to yourself"
    resume = Path(tmp) / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    listing = ManualList(folder)
    listing.add("1", "FPGA Intern", "Chips", "Remote", "https://example.test/1", 1.0, "external", str(resume), fit=90)
    listing.add("2", "Sales Intern", "Shop", "Remote", "https://example.test/2", 0.4, "external", fit=30)
    listing.add("3", "Firmware Intern", "Boards", "Remote", "https://example.test/3", 0.9, "external", fit=80)
    listing.save()

    opened: list[str] = []
    ready = threading.Event()

    def open_page(url: str) -> None:
        opened.append(url)
        ready.set()

    thread = threading.Thread(
        target=list_server.serve,
        kwargs=dict(folder=folder, open_page=open_page, idle_after_open=4, idle_before_open=60),
        daemon=True,
    )
    thread.start()
    check("server started", ready.wait(10))
    url = opened[0]
    good = {"Content-Type": "application/json", "X-Hsbot": "1"}

    print("=" * 70)
    print("1. ONLY THIS PAGE CAN CHANGE THE LIST")
    print("=" * 70)
    check("request without the page header refused", post(url + "api/remove", {"id": "2"}, {"Content-Type": "application/json"}) == 403)
    check("request from another website refused",
          post(url + "api/remove", {"id": "2"}, dict(good, Origin="https://evil.example")) == 403)
    check("nothing removed by refused requests", len(ManualList(folder)) == 3)
    check("unknown posting reported", post(url + "api/remove", {"id": "999"}, good) == 404)

    print("=" * 70)
    print("2. REMOVE AND RESTORE IN THE BROWSER")
    print("=" * 70)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url)
        check("page lists all three", page.locator("tbody tr[data-removed='0']").count() == 3)
        check("resume served through the list", page.locator("a[href='/resume/1']").count() == 1)
        check("no 'only hides' note when served", page.locator("#note").is_hidden())

        page.locator("tr[data-id='2'] button.remove").click()
        page.wait_for_function("document.querySelector(\"tr[data-id='2']\").getAttribute('data-removed') === '1'")
        saved = ManualList(folder)
        check("removed from list.json", saved.get("2") is not None and "2" not in [r["job_id"] for r in saved.items()])
        check("kept in removed.json", saved.is_removed("2"))
        check("removed from the spreadsheet", "Sales Intern" not in (folder / "apply_yourself.csv").read_text(encoding="utf-8"))
        check("row hidden on the page", page.locator("tr[data-id='2']").is_hidden())
        check("count updated", "2 shown, 1 removed" in page.locator("#count").inner_text(), page.locator("#count").inner_text())

        saved.add("2", "Sales Intern", "Shop", "Remote", "https://example.test/2", 0.4, "external", fit=30)
        saved.save()
        check("a later run doesn't add it back", ManualList(folder).is_removed("2") and len(ManualList(folder)) == 2)

        page.locator("#toggle").click()
        page.locator("tr[data-id='2'] button.remove").click()
        page.wait_for_function("document.querySelector(\"tr[data-id='2']\").getAttribute('data-removed') === '0'")
        check("restore puts it back", not ManualList(folder).is_removed("2") and len(ManualList(folder)) == 3)

        resume_response = page.request.get(url + "resume/1")
        check("resume opens", resume_response.ok and resume_response.body().startswith(b"%PDF"))
        check("other files are not served", page.request.get(url + "list.json").status == 404)

        plain = browser.new_page()
        plain.goto((folder / "apply_yourself.html").as_uri())
        check("plain file explains Remove only hides", "only hides" in plain.locator("#note").inner_text())
        browser.close()

    print("=" * 70)
    print("3. STOPS BY ITSELF")
    print("=" * 70)
    thread.join(timeout=15)
    check("server stopped after the page closed", not thread.is_alive())

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL LIST SERVER TESTS PASSED")
print("=" * 70)
