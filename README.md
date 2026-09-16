# Handshake Summer Internship Assistant

Reads your resume, asks for your major, searches Handshake for summer
internships in that field, ranks every posting against your resume, and submits
Quick Apply applications.

## Read this first

Handshake's terms of service restrict automated access, and university career
centers can suspend a student account for bulk automated submissions. That risk
is yours to weigh. Three things reduce it a lot:

- The tool defaults to asking you before every single submission. Fully
  automatic mode exists but you have to pass `--auto-submit` and then type
  `yes` at a second prompt.
- It caps applications per run and waits 20 to 45 seconds between them.
- It drives a real visible browser using your own normal login. It never
  reads, stores or types your password.

Start with `search` and `--dry-run`. Only move to real submissions once you
have seen that the matches it picks are ones you would have picked yourself.

## Install

```bash
pip install -r requirements.txt
```

```bash
python -m playwright install chromium
```

## First run

Sign in once. The session is saved in `data/browser_profile`, so later runs
skip this.

```bash
python main.py login
```

A browser window opens on Handshake. Log in the way you normally do, including
any multi-factor step. The tool waits until it sees the job page, then exits.

The session normally lives in `data/browser_profile`. On Windows, Chromium
cannot store a session under a deeply nested folder, so if this project sits
more than about 120 characters deep the tool says so and uses
`%LOCALAPPDATA%\handshake_intern_bot\browser_profile` instead. Either way you
only sign in once.

If your school uses its own Handshake address, pass it every time, or set
`handshake_base_url` in `config.json`:

```bash
python main.py login --base-url https://yourschool.joinhandshake.com
```

## See what it would apply to

This applies to nothing. It ranks matches and writes `data/matches.csv`.

```bash
python main.py search --resume "C:\Users\you\Documents\resume.pdf"
```

It asks for your major, showing a numbered list of 27 majors with tuned search
terms. You can type any other major instead and it searches on that name.

To skip the prompt:

```bash
python main.py search --resume resume.pdf --major "Mechanical Engineering"
```

## Apply

Confirms each posting with you before submitting:

```bash
python main.py apply --resume resume.pdf
```

Fills in every application but never submits, so you can watch the flow:

```bash
python main.py apply --resume resume.pdf --dry-run
```

Fully automatic, capped at 10:

```bash
python main.py apply --resume resume.pdf --auto-submit --max 10
```

Some postings ask for a cover letter or transcript. Give the tool local copies
to upload, or name documents already saved on Handshake in `config.json`:

```bash
python main.py apply --resume resume.pdf --cover-letter cover_letter.pdf --transcript transcript.pdf
```

### What gets submitted and what doesn't

Before submitting, the tool fills every document slot it can recognize, then
checks the form for required fields that are still empty. If anything required
is left, such as a written question, it closes the form without submitting.

Nothing is ever sent with a required field blank. Those postings, plus good
matches that make you apply on the employer's own website, are printed at the
end of the run and saved to `data/follow_up.csv` so you can finish them by hand.

## Other commands

```bash
python main.py majors
```

```bash
python main.py history
```

`history` writes `data/history.csv` with every posting the tool has applied to,
skipped or declined.

## Useful flags

| Flag | Meaning |
| --- | --- |
| `--resume PATH` | Your resume as PDF, DOCX or TXT. |
| `--cover-letter PATH` | Uploaded when a posting requires a cover letter. |
| `--transcript PATH` | Uploaded when a posting requires a transcript. |
| `--major NAME` | Skip the prompt and target this major. |
| `--location CITY` | Preferred location, repeatable. Remote postings always pass. |
| `--min-score 0.15` | Lower the match threshold when too little gets through. |
| `--pages 6` | Read more search result pages per query. |
| `--scan 100` | Open and score more postings in one run. |
| `--max 10` | Cap applications this run. |
| `--dry-run` | Fill applications but never submit. |
| `--auto-submit` | Submit without asking each time. |
| `--headless` | Hide the browser window. |

## How matching works

The score is a plain weighted overlap, and every run prints which terms
matched so you can see why a posting ranked where it did.

| Signal | Weight |
| --- | --- |
| Major keyword your resume also backs up | 3.0 |
| Major keyword | 2.0 |
| Tool or topic detected in your resume | 1.5 |
| Word used repeatedly in your resume | 1.0 |

A posting also has to clear hard filters before it is scored at all. It must
read as an internship or co-op, mention summer, match a location preference if
you set one, and still be open. Matches that apply on the employer's own site
are kept for your follow-up list rather than dropped.

If almost nothing gets through, lower `--min-score` to around `0.12` and raise
`--pages`. If junk gets through, raise it toward `0.35`.

## Settings file

Copy `config.example.json` to `config.json` to make your choices permanent.
Command line flags override it.

```bash
copy config.example.json config.json
```

`resume_doc_name` is worth setting. Upload your resume to Handshake once under
Documents, put its exact name here, and the tool picks that saved document
instead of uploading a fresh copy for every application.

`cover_letter_doc_name` and `transcript_doc_name` work the same way.
`cover_letter_path` and `transcript_path` are the local files used when no saved
document matches. Without any of these, the tool still picks a saved document
whose name contains "cover letter" or "transcript" if the form requires one.

## When it breaks

Handshake ships DOM changes regularly, and any tool like this breaks when they
do. Everything fragile is isolated in `selectors.json`, which holds a list of
CSS selector candidates per element, tried in order.

To repair it: open the page in Chrome, inspect the element it can no longer
find, and add the working selector to the front of the matching list. No Python
changes needed.

The most common failure is search returning nothing. Handshake's filter query
parameters change often. The reliable fix is to search on Handshake by hand
with the filters you want, copy the URL from the address bar, and put it in
`config.json` as `search_url_template` with `{page}` in place of the page
number and `{query}` in place of your search text:

```json
"search_url_template": "{base}/stu/postings?query={query}&page={page}&per_page=25"
```

## Tests

None of these touch the real Handshake. The browser tests run against a local
mock served from `tests/fake_handshake.py`. They also run automatically on
GitHub for every push, from `.github/workflows/tests.yml`.

```bash
python tests/smoke_test.py
```

```bash
python tests/browser_test.py
```

```bash
python tests/cli_test.py
```

## Files

| File | Role |
| --- | --- |
| `main.py` | Command line interface and the run loop. |
| `handshake.py` | Playwright driver: login, search, job pages, submission. |
| `matcher.py` | Scoring and the internship, summer and location filters. |
| `majors.py` | Major to search terms and keywords, with the prompt. |
| `resume_parser.py` | Resume text extraction and keyword detection. |
| `storage.py` | Application ledger, follow-up list and CSV export. |
| `selectors.json` | CSS fallbacks, the part to edit when Handshake changes. |
| `config.example.json` | Template for `config.json`. |

## Limits worth knowing

- Postings on an employer's own site and forms with written questions are never
  filled in here. They land in `data/follow_up.csv` for you to finish.
- Required fields are detected from the form's own markings. A question that is
  required but not marked that way can't be seen, so watch the first few runs.
- Nothing here writes a cover letter or edits your resume per posting.
- The selectors were tested against a mock of Handshake, not the live site.
  Expect to adjust `selectors.json` on your first real run.
- `data/applied.json` is what stops repeat applications. Keep it.
