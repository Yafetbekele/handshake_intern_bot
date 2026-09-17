"""A tiny stand-in for Handshake, used to exercise the browser driver locally.

Its structure copies what Handshake's live job search looked like when this was
written (checked in a real, signed-in browser, read only):

* /job-search jumps to the first result, e.g. /job-search/11428461?page=1&per_page=25
* the search box is <input type="search" name="query"> with the placeholder
  "Describe a job you want"; searching gives ?query=...&per_page=25&sort=relevance&page=1
* the Internship filter adds jobType=3
* result cards are data-hook="job-result-card | <id>" wrapping a link to /job-search/<id>
* the page has a "Jobs" h1 above the results, and the job's own panel is
  data-hook="right-content" with its h1, an /e/<id> employer link, "At a glance"
  lines such as "Onsite, based in Baltimore, MD" and
  "Full-time∙From May 30, 2027 to August 7, 2027", a truncated description with a
  "Show more" button, and a "Similar jobs" list linking to /jobs/<id>
* action buttons are "Apply", "Quick apply" or "Apply externally"; paging is a
  button labeled "next page"

* the Apply form (inspected on a real posting and closed without submitting) is
  a visible div[role=dialog] among hidden ones, with one <fieldset> per document
  headed "Attach your resume" / "Attach your cover letter"; an attached document
  shows as [data-status=positive] with its name in an h5; an empty section has a
  search combobox ("Search your cover letters") listing saved documents as
  [role=option], plus an "Upload new" file input; then "Submit Application"

Extra routes simulate failures: a search address that redirects home, and a bot
check page.
"""

from __future__ import annotations

import html
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

NAV = (
    "<nav><a href='/home'>Home</a> <a href='/job-search'>Jobs</a> "
    "<a href='/stu/profile'>Profile</a></nav>"
)

JOBS: dict[str, dict[str, str]] = {
    "1001": {
        "title": "Software Engineering Intern - Summer 2027",
        "employer": "Acme Cloud",
        "where": "Onsite, based in Seattle, WA",
        "dates": "Full-time∙From June 1, 2027 to August 25, 2027",
        "kind": "Internship",
        "body": (
            "Join our backend team building distributed systems in Python and "
            "Java. You will write REST API endpoints, take part in code review, "
            "use Git and Docker daily, and work in an agile environment."
        ),
        # Only readable after clicking "Show more".
        "more": "Coursework in data structures and algorithms is required.",
        "button": "Apply",
    },
    "1002": {
        "title": "Machine Learning Intern",
        "employer": "Beta Labs",
        "where": "Remote or hybrid, based in Sacramento, CA",
        "dates": "Full-time∙From May 24, 2027 to August 13, 2027",
        "kind": "Internship",
        "body": (
            "Work on machine learning and computer vision models in PyTorch. "
            "Strong Python and data analysis skills expected."
        ),
        "more": "",
        "button": "Apply externally",
    },
    "1003": {
        "title": "Veterinary Clinic Intern",
        "employer": "Happy Paws",
        "where": "Onsite, based in Dayton, OH",
        "dates": "Part-time∙From June 5, 2027 to August 1, 2027",
        "kind": "Internship",
        "body": (
            "Help our veterinary staff care for animals, clean kennels, greet "
            "clients and assist with feeding schedules."
        ),
        "more": "",
        "button": "Apply",
    },
    "1004": {
        "title": "Senior Software Engineer",
        "employer": "Gamma Corp",
        "where": "Onsite, based in Austin, TX",
        "dates": "Full-time",
        "kind": "Job",
        "body": (
            "Full time permanent role for an experienced engineer. Python, "
            "Java, distributed systems, code review, Docker."
        ),
        "more": "",
        "button": "Apply",
    },
    # Requires a cover letter and a written answer, so it must NOT be submitted.
    "1005": {
        "title": "Backend Engineering Intern",
        "employer": "Delta Systems",
        "where": "Hybrid, based in Chicago, IL",
        "dates": "Full-time∙From May 24, 2027 to August 13, 2027",
        "kind": "Internship",
        "body": (
            "Internship on our backend platform. Build API services in Python "
            "and Java, write tests, use Git and Docker, and join code review. "
            "Data structures and algorithms coursework expected."
        ),
        "more": "",
        "button": "Apply",
        "sections": ["cover letter"],
        "question": "Why do you want to intern with us?",
    },
    # Requires a transcript, which is available as a saved document.
    "1006": {
        "title": "Cloud Software Intern",
        "employer": "Epsilon Cloud",
        "where": "Remote or hybrid, based in Denver, CO",
        "dates": "Full-time∙From June 8, 2027 to August 28, 2027",
        "kind": "Internship",
        "body": (
            "Internship building cloud software in Python. Work on distributed "
            "systems, backend API design, Docker and Git, with code review from "
            "senior engineers. Algorithms coursework required."
        ),
        "more": "",
        "button": "Quick apply",
        "sections": ["transcript"],
    },
    # A strong match that runs in spring, so the summer filter must drop it.
    "1007": {
        "title": "Platform Engineering Intern",
        "employer": "Zeta Software",
        "where": "Onsite, based in Boston, MA",
        "dates": "Full-time∙From January 11, 2027 to April 30, 2027",
        "kind": "Internship",
        "body": (
            "Internship building backend software in Python and Java: API "
            "design, distributed systems, Docker, Git, code review, algorithms."
        ),
        "more": "",
        "button": "Apply",
    },
    # Asks only simple questions the assistant has saved answers for.
    "1008": {
        "title": "Firmware Engineering Intern",
        "employer": "Theta Devices",
        "where": "Onsite, based in Austin, TX",
        "dates": "Full-time.From June 1, 2027 to August 20, 2027",
        "kind": "Internship",
        "body": (
            "Summer internship writing firmware in C++ for microcontrollers. "
            "Debugging with Git and Docker, code review, distributed systems "
            "background helpful, algorithms and data structures coursework."
        ),
        "more": "",
        "button": "Apply",
        "questions": [
            {"type": "text", "label": "Phone number", "required": True},
            {"type": "yesno", "label": "Are you legally authorized to work in the United States?"},
        ],
    },
    # Asks something personal that must be left for the student.
    "1009": {
        "title": "Systems Engineering Intern",
        "employer": "Iota Aerospace",
        "where": "Onsite, based in Denver, CO",
        "dates": "Full-time.From May 25, 2027 to August 15, 2027",
        "kind": "Internship",
        "body": (
            "Summer internship on embedded systems: Python, Java, API design, "
            "Docker, Git, code review, algorithms and distributed systems."
        ),
        "more": "",
        "button": "Apply",
        "questions": [
            {"type": "text", "label": "Phone number", "required": True},
            {"type": "text", "label": "Will you now or in the future require visa sponsorship?", "required": True},
        ],
    },
}

ALL_IDS = sorted(JOBS)

POSTINGS_PAGE = """<!doctype html><html><head><title>Jobs</title></head><body>
{nav}
<h2>Search results</h2>
<div id="results">{cards}</div>
</body></html>"""

PAGE = """<!doctype html><html><head><title>Jobs | Handshake</title></head><body>
{nav}
<main>
  <h1>Jobs</h1>
  <form onsubmit="return false">
    <input type="search" name="query" role="combobox" placeholder="Describe a job you want" value="{query_value}" />
    <label><input type="checkbox" aria-label="Internship" /> Internship</label>
  </form>
  <div id="results" style="max-height:400px; overflow-y:auto">{cards}</div>
  {pager}
  <div data-hook="right-content">{panel}</div>
</main>

<!-- Handshake keeps closed dialogs in the page; this hidden one comes first. -->
<div role="dialog" style="display:none"><h2>Share this job</h2><button type="button">Submit</button></div>

<div id="dialog" role="dialog" data-dialog="true" class="rosetta-dialog__sheet" style="display:none">
  <h2>Apply to {employer_name}</h2>
  <button type="button" data-hook="apply-modal-close-button" aria-label="Cancel application" onclick="closeDialog()"></button>
  <div data-hook="apply-modal-content"><form onsubmit="return false">
    <h3>Details from {employer_name}:</h3>
    <p>Applying requires a few documents. Attach them below and get one step closer to your next job!</p>
    <fieldset><h4>Attach your resume</h4>
      <div id="slot-resume">
        <div role="status" data-status="positive"><h5>Academic Resume.pdf</h5><a href="/docs/1">Preview document</a>
          <span><button type="button" aria-label="Close" onclick="removeResume()"></button></span>
        </div>
      </div>
    </fieldset>
    {extra}
    <button type="button" onclick="submitApp()">Submit Application</button>
  </form></div>
</div>

<div id="done" style="display:none">Application submitted</div>

<script>
document.querySelector("input[name='query']").addEventListener('keydown', function (e) {{
  if (e.key === 'Enter') {{
    location.href = '/job-search?query=' + encodeURIComponent(this.value).replace(/%20/g, '+')
      + '&per_page=25&sort=relevance&page=1';
  }}
}});
window.picked = {{ resume: 'Academic Resume.pdf' }};
function openDialog() {{
  document.getElementById('dialog').style.display = 'block';
}}
function closeDialog() {{
  document.getElementById('dialog').style.display = 'none';
}}
document.addEventListener('keydown', function (e) {{ if (e.key === 'Escape') closeDialog(); }});
function removeResume() {{
  // Detaching the default resume shows Handshake's search box and "Upload new".
  window.picked.resume = '';
  document.getElementById('slot-resume').innerHTML =
    '<div data-hook="apply-modal-document-search">'
    + '<input type="search" role="combobox" placeholder="Search your resumes" />'
    + '<input type="file" name="file-Resume" onchange="setTimeout(function (f) {{ pick(\\'resume\\', f.files[0].name); }}, 400, this)" />'
    + '</div>';
}}
function openList(kind) {{
  document.getElementById('lb-' + kind).style.display = 'block';
}}
function pick(kind, name) {{
  window.picked[kind] = name;
  document.getElementById('slot-' + kind).innerHTML =
    '<div role="status" data-status="positive"><h5>' + name + '</h5></div>';
}}
function showMore(button) {{
  document.getElementById('more-text').style.display = 'block';
  button.remove();
}}
function submitApp() {{
  closeDialog();
  var actions = document.getElementById('actions');
  if (actions) actions.innerHTML = '<button type="button">Withdraw application</button>';
  var done = document.getElementById('done');
  done.style.display = 'block';
  done.setAttribute('data-resume', window.picked.resume || '');
  done.setAttribute('data-cover', window.picked['cover-letter'] || '');
  done.setAttribute('data-transcript', window.picked.transcript || '');
  var typed = [];
  document.querySelectorAll('#dialog input[type=text], #dialog input[type=radio]:checked').forEach(function (el) {{
    typed.push((el.id || el.name) + '=' + el.value);
  }});
  done.setAttribute('data-answers', typed.join('|'));
}}
</script>
</body></html>"""


def result_cards(query_string: str) -> str:
    """Cards shaped like Handshake's. Job 1003's card carries a 'You applied'
    badge, which must never leak into another job's details."""
    suffix = f"?{query_string}" if query_string else ""
    cards = []
    for job_id in ALL_IDS:
        data = JOBS[job_id]
        badge = "<span>You applied</span>" if job_id == "1003" else ""
        label = html.escape(f"{data['employer']} {data['title']} {data['kind']}", quote=True)
        cards.append(
            f"<div data-hook='job-result-card | {job_id}'>"
            f"<a role='button' href='/job-search/{job_id}{suffix}' aria-label='{label}'>"
            f"{data['employer']} {data['title']}</a>{badge}</div>"
            f"<div data-hook='job-result-card-footer'></div>"
        )
    return "".join(cards)


def button_cards() -> str:
    """Result cards with no links; clicking one changes the address in place."""
    return "".join(
        f"<div data-hook='job-card' onclick=\"history.pushState(null, '', '/job-search/{job_id}')\">"
        f"{JOBS[job_id]['title']}</div>"
        for job_id in ALL_IDS
    )


def job_panel(job_id: str) -> str:
    data = JOBS[job_id]
    similar = next(i for i in ALL_IDS if i != job_id)
    external = data["button"] == "Apply externally"
    onclick = "window.open('https://example.com/apply')" if external else "openDialog()"
    more = ""
    if data["more"]:
        more = (
            "<button aria-label='Show more (job description)' onclick='showMore(this)'>More</button>"
            f"<div id='more-text' style='display:none'>{data['more']}</div>"
        )
    return (
        f"<a href='/e/55' target='_blank' aria-label='{data['employer']}'></a>"
        f"<a href='/e/55' target='_blank'>{data['employer']}</a>"
        f"<a href='/jobs/{job_id}'><h1>{data['title']}</h1></a>"
        "<div>Posted 2 days ago∙Apply by October 14, 2026 at 11:59 PM</div>"
        "<button>Save</button><button>Share</button>"
        f"<span id='actions'><button aria-label='{data['button']}' onclick=\"{onclick}\">{data['button']}</button></span>"
        "<h2>At a glance</h2>"
        f"<div>{data['where']}</div><div>{data['kind']}</div><div>{data['dates']}</div>"
        f"<h2>Job description</h2><div>{data['body']}</div>{more}"
        "<h2>Similar jobs</h2>"
        f"<a href='/jobs/{similar}' target='_blank'>{JOBS[similar]['title']}</a>"
    )


SAVED_DOCUMENTS = {
    "cover letter": ["Cover Letter.pdf"],
    "transcript": ["Transcript Spring 2026.pdf"],
}


def apply_form_extras(job_id: str) -> str:
    """Document sections beyond the resume, shaped like Handshake's, plus any question."""
    parts = []
    for section in JOBS[job_id].get("sections", []):
        kind = section.replace(" ", "-")
        options = "".join(
            f"<div role='option' onclick=\"pick('{kind}', '{name}')\">{name}</div>"
            for name in SAVED_DOCUMENTS[section]
        )
        parts.append(
            f"<fieldset><h4>Attach your {section}</h4><div id='slot-{kind}'>"
            f"<div data-hook='apply-modal-document-search'>"
            f"<input type='search' role='combobox' placeholder='Search your {section}s' "
            f"aria-controls='lb-{kind}' onclick=\"openList('{kind}')\" />"
            f"<div role='listbox' id='lb-{kind}' style='display:none'>{options}</div>"
            f"<input type='file' name='file-{section.title()}' "
            f"onchange=\"pick('{kind}', this.files[0].name)\" />"
            "</div></div></fieldset>"
        )
    question = JOBS[job_id].get("question")
    if question:
        parts.append(f"<label for='q1'>{question} *</label><textarea id='q1' required></textarea>")
    for index, item in enumerate(JOBS[job_id].get("questions", [])):
        field_id = f"q-{index}"
        star = " *" if item.get("required") else ""
        if item["type"] == "yesno":
            parts.append(
                f"<fieldset role='radiogroup'><legend>{item['label']}{star}</legend>"
                f"<label for='{field_id}-y'>Yes</label>"
                f"<input type='radio' id='{field_id}-y' name='{field_id}' value='Yes' />"
                f"<label for='{field_id}-n'>No</label>"
                f"<input type='radio' id='{field_id}-n' name='{field_id}' value='No' />"
                "</fieldset>"
            )
        else:
            required = " required" if item.get("required") else ""
            parts.append(
                f"<label for='{field_id}'>{item['label']}{star}</label>"
                f"<input type='text' id='{field_id}' name='{field_id}'{required} />"
            )
    return "".join(parts)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence the default stderr spam
        return

    def _send(self, body: str) -> None:
        payload = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.end_headers()

    def _search_page(self, job_id: str | None, params: dict[str, list[str]]) -> None:
        query = params.get("query", [""])[0]
        page = int(params.get("page", ["1"])[0])
        flat = {k: v[0] for k, v in params.items()}
        query_string = urlencode(flat)

        cards, pager = "", ""
        if page == 1:
            if flat.get("style") == "buttons":
                cards = button_cards()
            else:
                cards = result_cards(query_string)
                next_params = dict(flat, page="2")
                pager = (
                    "<button aria-label='first page' disabled></button>"
                    "<button aria-label='previous page' disabled></button>"
                    f"<button aria-label='next page' onclick=\"location.href='/job-search?{urlencode(next_params)}'\"></button>"
                )

        panel, extra, employer_name = "", "", ""
        if job_id is not None:
            if job_id not in JOBS:
                self.send_error(404)
                return
            panel = job_panel(job_id)
            extra = apply_form_extras(job_id)
            employer_name = JOBS[job_id]["employer"]

        self._send(
            PAGE.format(
                nav=NAV,
                cards=cards,
                pager=pager,
                panel=panel,
                extra=extra,
                employer_name=employer_name,
                query_value=html.escape(query, quote=True),
            )
        )

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)
        path = parsed.path.rstrip("/") or "/"

        # A search address that no longer exists and bounces to the home page.
        if path == "/old/postings":
            self._redirect("/home")
            return

        # A bot check served in place of the real page.
        if path == "/guarded/postings":
            self._send(
                "<html><head><title>Just a moment...</title></head>"
                "<body><p>Verify you are human</p></body></html>"
            )
            return

        if path == "/explore":
            self._redirect("/home")
            return

        # Like Handshake: the search page jumps to its first result when there is one.
        if path == "/job-search":
            page = int(params.get("page", ["1"])[0])
            if page == 1 and params.get("style", [""])[0] != "buttons":
                flat = {k: v[0] for k, v in params.items()}
                flat.setdefault("page", "1")
                flat.setdefault("per_page", "25")
                self._redirect(f"/job-search/{ALL_IDS[0]}?{urlencode(flat)}")
                return
            self._search_page(None, params)
            return

        match = re.fullmatch(r"/(?:job-search|jobs|stu/jobs)/(\d+)", path)
        if match:
            self._search_page(match.group(1), params)
            return

        # Older list layout, kept for the explicit search-address tests.
        if path == "/stu/postings":
            page = int(params.get("page", ["1"])[0])
            cards = ""
            if page == 1:
                cards = "".join(
                    f"<div class='card'><a href='/stu/jobs/{job_id}'>{JOBS[job_id]['title']}</a></div>"
                    for job_id in ALL_IDS
                )
            self._send(POSTINGS_PAGE.format(nav=NAV, cards=cards))
            return

        self._send(
            f"<html><head><title>Home | Handshake</title></head><body>{NAV}<p>Home</p></body></html>"
        )


def serve(port: int = 8765) -> tuple[HTTPServer, threading.Thread]:
    server = HTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread
