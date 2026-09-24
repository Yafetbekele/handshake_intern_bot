"""Opens the apply-yourself list so Remove takes postings off for good.

A page opened straight from disk can't change files, so its Remove button only
hides rows in that browser. This serves the same page from this computer
(127.0.0.1 only), where Remove and Restore update list.json, the spreadsheet
and removed.json. The server stops by itself a few minutes after the page is
closed.

    python main.py list
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import applied_myself
from storage import ManualList

IDLE_AFTER_OPEN = 180.0  # seconds without a ping once the page has loaded
IDLE_BEFORE_OPEN = 600.0  # in case the browser never opens the page


class ListServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, folder: Path, port: int = 0) -> None:
        self.folder = Path(folder)
        self.last_seen = time.monotonic()
        self.opened = False
        self.lock = threading.Lock()
        super().__init__(("127.0.0.1", port), _Handler)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}/"

    def listing(self) -> ManualList:
        return ManualList(self.folder)

    def idle_for(self) -> float:
        return time.monotonic() - self.last_seen


class _Handler(BaseHTTPRequestHandler):
    server: ListServer

    def log_message(self, *_args: object) -> None:  # keep the console quiet
        pass

    def _send(self, code: int, body: bytes = b"", content_type: str = "text/plain; charset=utf-8") -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _trusted(self) -> bool:
        """Only this page may change the list, not another website in the browser."""
        host = self.headers.get("Host", "")
        origin = self.headers.get("Origin")
        own = f"127.0.0.1:{self.server.server_address[1]}"
        return host == own and origin in (None, f"http://{own}") and self.headers.get("X-Hsbot") == "1"

    def do_GET(self) -> None:  # noqa: N802
        self.server.last_seen = time.monotonic()
        path = self.path.split("?", 1)[0]
        pages = {"/ranked": "all_ranked.html", "/elsewhere": "found_elsewhere.html"}
        if path in pages:
            self.server.opened = True
            page = self.server.folder / pages[path]
            if page.is_file():
                self._send(200, page.read_bytes(), "text/html; charset=utf-8")
            else:
                self._send(404, b"Not made yet. Run rank_all.py or find_elsewhere.py first.")
            return
        if path == "/api/applied":
            if not self._trusted():
                self._send(403, b"Forbidden")
                return
            body = json.dumps(sorted(applied_myself.ids())).encode("utf-8")
            self._send(200, body, "application/json")
            return
        if path in ("/", "/apply_yourself.html"):
            self.server.opened = True
            with self.server.lock:
                page = self.server.listing().page_html(served=True)
            self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            return
        if path.startswith("/resume/"):
            item = self.server.listing().get(path[len("/resume/"):])
            resume = Path(str((item or {}).get("resume", "")))
            if item and resume.suffix.lower() in {".pdf", ".docx"} and resume.is_file():
                kind = "application/pdf" if resume.suffix.lower() == ".pdf" else (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
                self._send(200, resume.read_bytes(), kind)
                return
        self._send(404, b"Not found")

    def do_POST(self) -> None:  # noqa: N802
        self.server.last_seen = time.monotonic()
        # Read the whole request first; answering early makes Windows reset the connection.
        try:
            raw = self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 8192))
        except (ValueError, OSError):
            raw = b""
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        if not self._trusted():
            self._send(403, b"Forbidden")
            return
        if self.path == "/api/ping":
            self._send(204)
            return
        if self.path == "/api/applied":
            job_id = str(data.get("id", "")).strip()
            if not job_id:
                self._send(400, b"Missing id")
                return
            with self.server.lock:
                if data.get("undo"):
                    applied_myself.unmark(job_id)
                else:
                    applied_myself.mark(job_id, data)
                    listing = self.server.listing()
                    if listing.get(job_id) is not None and not listing.is_removed(job_id):
                        listing.dismiss(job_id)  # applied, so off the apply-yourself list too
                        listing.save()
            self._send(200, b'{"ok": true}', "application/json")
            return
        if self.path not in ("/api/remove", "/api/restore"):
            self._send(404, b"Not found")
            return
        job_id = str(data.get("id", ""))
        if not job_id:
            self._send(400, b"Missing id")
            return
        with self.server.lock:
            listing = self.server.listing()
            done = listing.dismiss(job_id) if self.path == "/api/remove" else listing.restore(job_id)
            if done:
                listing.save()
        if not done:
            self._send(404, b"Not on the list")
            return
        body = json.dumps({"ok": True, "count": len(listing)}).encode("utf-8")
        self._send(200, body, "application/json")


def serve(
    folder: Path,
    open_page: Callable[[str], object] | None = webbrowser.open,
    port: int = 0,
    idle_after_open: float = IDLE_AFTER_OPEN,
    idle_before_open: float = IDLE_BEFORE_OPEN,
) -> int:
    """Serve the list until the page has been closed for a while."""
    listing = ManualList(folder)
    listing.save()  # keep the plain file copy current too
    server = ListServer(folder, port)
    print(f"Your apply-yourself list is open at {server.url}")
    print("Remove and Restore there update the saved list. This window can be closed when you're done.")

    def watch() -> None:
        while True:
            time.sleep(1)
            limit = idle_after_open if server.opened else idle_before_open
            if server.idle_for() > limit:
                server.shutdown()
                return

    threading.Thread(target=watch, daemon=True).start()
    if open_page is not None:
        open_page(server.url)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
