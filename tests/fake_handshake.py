"""A tiny stand-in for Handshake, used to exercise the browser driver locally.

It mimics the parts the driver touches: a signed-in nav bar, a postings list of
job links, job detail pages, a Quick Apply dialog with a saved-document picker,
and an externally-hosted posting that must be skipped.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

NAV = (
    "<nav><a href='/stu/postings'>Jobs</a> "
    "<a href='/stu/profile'>Profile</a></nav>"
)

JOBS: dict[str, dict[str, str]] = {
    "1001": {
        "title": "Software Engineering Intern - Summer 2027",
        "employer": "Acme Cloud",
        "location": "Seattle, WA",
        "body": (
            "Join our backend team building distributed systems in Python and "
            "Java. You will write REST API endpoints, take part in code review, "
            "use Git and Docker daily, and work in an agile environment. "
            "Coursework in data structures and algorithms is required. "
            "This is a paid summer internship for undergraduate students."
        ),
        "external": "no",
    },
    "1002": {
        "title": "Machine Learning Intern, Summer 2027",
        "employer": "Beta Labs",
        "location": "Remote",
        "body": (
            "Work on machine learning and computer vision models in PyTorch. "
            "Strong Python and data analysis skills expected. Summer internship "
            "for students returning to school in the fall."
        ),
        "external": "yes",
    },
    "1003": {
        "title": "Veterinary Clinic Summer Intern",
        "employer": "Happy Paws",
        "location": "Dayton, OH",
        "body": (
            "Help our veterinary staff care for animals, clean kennels, greet "
            "clients and assist with feeding schedules during the summer."
        ),
        "external": "no",
    },
    "1004": {
        "title": "Senior Software Engineer",
        "employer": "Gamma Corp",
        "location": "Austin, TX",
        "body": (
            "Full time permanent role for an experienced engineer. Python, "
            "Java, distributed systems, code review, Docker."
        ),
        "external": "no",
    },
    # Requires a cover letter and a written answer, so it must NOT be submitted.
    "1005": {
        "title": "Backend Engineering Intern - Summer 2027",
        "employer": "Delta Systems",
        "location": "Chicago, IL",
        "body": (
            "Summer internship on our backend platform. Build API services in "
            "Python and Java, write tests, use Git and Docker, and join code "
            "review. Data structures and algorithms coursework expected."
        ),
        "external": "no",
        "extra": (
            '<label for="cl">Cover letter *</label>'
            '<select id="cl" required>'
            '<option value="">Select a document</option>'
            '<option value="c1">Cover Letter.pdf</option>'
            "</select>"
            '<label for="why">Why do you want to intern with us? *</label>'
            '<textarea id="why" required></textarea>'
        ),
    },
    # Requires a transcript, which is available as a saved document.
    "1006": {
        "title": "Cloud Software Intern - Summer 2027",
        "employer": "Epsilon Cloud",
        "location": "Remote",
        "body": (
            "Summer internship building cloud software in Python. Work on "
            "distributed systems, backend API design, Docker and Git, with "
            "code review from senior engineers. Algorithms coursework required."
        ),
        "external": "no",
        "extra": (
            "<label>Unofficial transcript (required)"
            '<select id="tr">'
            '<option value="">Select a document</option>'
            '<option value="t1">Transcript Spring 2026.pdf</option>'
            "</select></label>"
        ),
    },
}

POSTINGS_PAGE = """<!doctype html><html><head><title>Jobs</title></head><body>
{nav}
<h2>Search results</h2>
<div id="results">{cards}</div>
</body></html>"""

JOB_PAGE = """<!doctype html><html><head><title>{title}</title></head><body>
{nav}
<h1>{title}</h1>
<a href="/stu/employers/55">{employer}</a>
<div data-hook="job-location">{location}</div>
<div data-hook="details-body">{body}</div>
<div id="actions">{action}</div>

<div id="dialog" role="dialog" style="display:none">
  <h3>Apply to {title}</h3>
  <label>Resume
    <select id="doc">
      <option value="">Select a document</option>
      <option value="r1">Jane Doe Resume.pdf</option>
      <option value="c1">Cover Letter.pdf</option>
    </select>
  </label>
  <input type="file" id="upload" />
  {extra}
  <button type="submit" onclick="submitApp()">Submit Application</button>
</div>

<div id="done" style="display:none">Application submitted</div>

<script>
function openDialog() {{
  document.getElementById('dialog').style.display = 'block';
}}
function submitApp() {{
  var picked = document.getElementById('doc').value;
  document.getElementById('dialog').style.display = 'none';
  document.getElementById('actions').style.display = 'none';
  var done = document.getElementById('done');
  done.style.display = 'block';
  done.setAttribute('data-picked', picked);
  var cl = document.getElementById('cl');
  if (cl) done.setAttribute('data-cover', cl.value);
  var tr = document.getElementById('tr');
  if (tr) done.setAttribute('data-transcript', tr.value);
}}
</script>
</body></html>"""

QUICK_ACTION = '<button type="button" onclick="openDialog()">Apply</button>'
EXTERNAL_ACTION = '<a href="https://example.com/apply" target="_blank">Apply Externally</a>'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence the default stderr spam
        return

    def _send(self, html: str) -> None:
        payload = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/stu/postings":
            page = int(params.get("page", ["1"])[0])
            if page > 1:
                self._send(POSTINGS_PAGE.format(nav=NAV, cards=""))
                return
            cards = "".join(
                f"<div class='card'><a href='/stu/jobs/{job_id}'>"
                f"{data['title']}</a></div>"
                for job_id, data in JOBS.items()
            )
            self._send(POSTINGS_PAGE.format(nav=NAV, cards=cards))
            return

        if parsed.path.startswith("/stu/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            data = JOBS.get(job_id)
            if data is None:
                self.send_error(404)
                return
            action = EXTERNAL_ACTION if data["external"] == "yes" else QUICK_ACTION
            self._send(
                JOB_PAGE.format(
                    nav=NAV,
                    title=data["title"],
                    employer=data["employer"],
                    location=data["location"],
                    body=data["body"],
                    action=action,
                    extra=data.get("extra", ""),
                )
            )
            return

        self._send(f"<html><body>{NAV}<p>ok</p></body></html>")


def serve(port: int = 8765) -> tuple[HTTPServer, threading.Thread]:
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
