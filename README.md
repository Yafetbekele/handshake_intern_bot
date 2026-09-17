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

## Easiest way to start: double-click

Double-click **Start Internship Assistant.bat** in this folder. No terminal
commands are needed.

1. The first time, it offers to download what it needs. This takes a few
   minutes once.
2. A window opens. Pick your resume, pick or type your major, and choose what
   it should do:
   - **See my matches only** ranks internships and applies to nothing.
   - **Practice run** fills in applications but submits nothing.
   - **Apply, asking me before each one** stops for a yes or no in the black
     window before every submission.
   - **Apply automatically** submits without asking, after a warning.
3. Click **Start**. A browser opens on Handshake. Sign in there the first time.
4. When it finishes, it offers to open your results in Excel.

The window remembers your choices for next time. The black window that stays
open is where progress shows up and where you answer yes or no in the "asking
me" mode.

For a desktop icon, right-click the .bat file, choose **Show more options**,
then **Send to**, then **Desktop (create shortcut)**.

Everything below is the command line version, which does the same thing with
more options.

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

It asks for your major, showing a numbered list of 28 majors with tuned search
terms, including Computer Engineering. You can type any other major instead and
it searches on that name.

It searches the way you would. It opens Handshake's job search, types each
search into the box, and turns on the Internship filter. It then reuses the
address Handshake produced for the rest of the run.

To skip the prompt:

```bash
python main.py search --resume resume.pdf --major "Mechanical Engineering"
```

## Jobs instead of internships, near a city

In the launcher, choose **Jobs (full-time and part-time)** under Looking for.
Then type a city or ZIP code and a distance from 1 to 100 miles. Leave the city
blank to search everywhere. On the command line:

```bash
python main.py search --resume resume.pdf --major Psychology --looking-for jobs --near "Baltimore, MD" --within 25
```

If you look for jobs without giving a city, the black window asks for one.

How it works:
- **The tool uses Handshake's own filters.** It ticks Full-time job and Part
  time, and picks your city from Handshake's Location suggestions at the
  distance you chose. Handshake decides which postings are in range. The tool
  doesn't measure distances itself.
- **Internships are skipped, and dates don't matter.** The summer check only
  applies when you're looking for internships.
- **If Handshake can't find your city, the run stops,** rather than applying
  to jobs everywhere. Try another spelling or a ZIP code.
- **Each major gets job searches.** Psychology has its own list: psychology,
  behavioral health technician, registered behavior technician, mental health,
  case manager, research assistant, human services, social services, counselor,
  psychiatric technician, youth development, and applied behavior analysis.
  Other majors reuse their internship searches with "intern" taken out.
- **The Psychology preset is broad on purpose.** It covers behavioral health,
  counseling, case management, ABA and RBT work, crisis and recovery programs,
  research assistant roles, youth and family services, and human services. Use
  the Broad setting to catch nearby roles too.

Everything else works the same for jobs: tailored resumes, saved answers, and
the apply-yourself list. Tailored resumes are built from your profile, so a
psychology job search needs a profile with psychology experience in it.

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

Handshake's application form has one section per document, such as "Attach your
resume" and "Attach your cover letter". The tool fills each section like this:

- **Resume.** Handshake usually attaches your default resume already. If not,
  the tool picks a saved resume or uploads the one you gave it.
- **Transcript.** It picks a saved transcript, or uploads the one you gave it.
- **Cover letter.** It only attaches one you named or gave it. It never guesses,
  because a letter written for one employer shouldn't go to another.

Then it checks for anything still empty, such as a document section or a
required question like a phone number. If something is left, it closes the
form without submitting.

**Practice runs never press "Quick apply".** On Handshake that button can send
the application in a single click. In the real modes it is pressed like any
other Apply button.

### Answering simple questions

Some applications ask for a phone number, your graduation date, or whether you
are authorized to work. Put those in `application_answers` in your profile and
the tool fills them in:

```json
{"match": ["phone", "mobile"], "value": "(555) 010-0000"}
{"match": ["authorized to work"], "value": "Yes", "kind": "yesno"}
```

A field is only filled when its label clearly matches one of your answers.

**Questions it doesn't recognize, it asks you.** When a run is in a window you
can type in, the black window stops and asks, then saves your answer to your
profile so the next application uses it. In a run you can't type in, an
unanswered question holds the application back and it goes on your
apply-yourself list.

**Personal and legal questions are only ever answered in your own words.**
Sponsorship, citizenship, clearance, pay, criminal history and demographic
questions are never guessed. The tool uses your saved answer, asks you, or
leaves the question blank.

**Two things are never filled in at all:** Social Security numbers and
financial details such as bank or card numbers. If a form insists on one, it
is left for you to finish on Handshake.

Set `answer_questions` to false to switch the whole feature off.

### Internships to apply to yourself

Good matches the tool can't apply to go into a folder named **Internships to
apply to yourself**, next to this README. That covers postings that apply on the
employer's own website, and forms with questions or documents the tool won't
fill in.

- Double-click `apply_yourself.html` for a page with a link to each posting,
  best fit first. `apply_yourself.csv` has the same list for Excel.
- The list keeps at most **50** postings. When a better one turns up, the
  weakest drops off (and gets no tailored resume).
- The list grows across runs without duplicates.
- A posting drops off once the tool applies to it.
- **Remove** hides an internship you've handled or don't want. Your browser
  remembers it, so it stays hidden after you reopen the page or a later run
  rebuilds it. **Show removed** brings hidden ones back into view, and
  **Restore** puts one back on the list. Removing only affects the page in that
  browser. The spreadsheet copy still lists everything.

**How the list is ordered.** Lots of postings score 100% on the major preset,
so this list uses a finer **Fit** score out of 100. It only orders this list;
what the tool applies to automatically is unchanged.

| Part | Up to | What earns it |
| --- | --- | --- |
| Major match | 30 | the same preset score used for applying |
| Your resume | 35 | skills and topics from your resume and `profile/career_profile.json` that the posting mentions (in the title counts double) |
| Student level | 25 | undergraduate-friendly wording ("undergraduate", "rising junior", "entry level", an intern title) scores high; PhD or graduate-only, years of experience, or a senior title score low |
| Title | 10 | the title names your major, one of its title words, or one of your skills |

Under each title the page shows why, for example
"matches your resume: fpga, verilog; open to undergraduates". Words that say
little on their own (communication, testing, systems and so on) are left out
in `GENERIC_TERMS` in `ranking.py`.

The launcher offers to open this page when a run finishes. Each run's own
summary is also saved to `data/follow_up.csv`.

## Tailored resumes

Tick **Tailor my resume to each job** in the launcher, or add `--tailor-resume`
on the command line. The tool then makes a one-page resume for each job it
applies to, and for each posting on your apply-yourself list.

### Where the facts come from

Everything comes from `profile/career_profile.json`, your own record of true
facts. That file stays on your computer and is never uploaded to GitHub.

- **Facts** are the plain truth about each job, project and school.
- **Bullets** are ready-made resume wordings of those facts, tagged by topic.
- **never_claim** lists things that are false for an entry, such as published
  findings for research that has none yet.
- **always_include** entries appear on every resume. **trim_first** entries are
  cut first when a page overflows.

Keep it up to date. Add a new job, project or result there and every future
resume can use it.

### How a resume is tailored

- **With Claude, when available.** It uses Claude Code on your existing Claude
  plan, with no API key and no extra cost. Claude picks, orders and rewords
  your facts for the job.
- **Then every AI-written bullet is checked.** A bullet is thrown out if it
  adds a number or a technical term your profile doesn't contain, makes a
  claim on a false-claims list, or uses mostly words your facts don't support.
  A true pre-written bullet replaces it.
- **With rules otherwise.** If Claude isn't signed in or doesn't answer, the
  tool ranks your pre-written bullets, coursework and skills by how well they
  match the posting. That is free, instant and always honest.

The result keeps your original resume's format: Times, bold section headings,
the organization in bold with the role in italics, and dash bullets. It is
always one page.

### Signing Claude Code in, once

Claude Code comes with the Claude desktop app, but it needs its own sign-in.
When you tick tailoring, the launcher offers to open it for you. By hand, in
PowerShell:

```bash
& (Get-ChildItem "$env:APPDATA\Claude\claude-code\*\claude.exe", "$env:LOCALAPPDATA\Packages\Claude_*\LocalCache\Roaming\Claude\claude-code\*\claude.exe" -ErrorAction SilentlyContinue | Select-Object -Last 1).FullName auth login
```

The second location is where the Microsoft Store version of the Claude app
keeps Claude Code. The tool checks both.

### What happens in an application

- **Real runs** swap your default resume for the tailored one inside that
  application only. Your default resume on Handshake is not changed.
- **Practice runs** make the tailored PDF so you can look at it, but never
  upload it.
- **If the upload doesn't finish,** the application is held back rather than
  sent without a resume, and the posting goes on your apply-yourself list.

Each resume is saved in `tailored_resumes/<job number>-<employer>/` with the
posting it was made for and `plan.json`, which records what was chosen and
anything the fact checks rejected. A job keeps its first resume on later runs.
Delete its folder to make a new one.

Add `--no-ai` to tailor with rules only.

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
| `--strictness broad` | How picky matching is: broad, balanced or strict. |
| `--min-score 0.15` | An exact minimum score instead of a strictness level. |
| `--pages 6` | Read more search result pages per query. |
| `--scan 100` | How many postings to open and score in one run. The default is 300. |
| `--max 10` | Cap applications this run. |
| `--dry-run` | Fill applications but never submit. |
| `--auto-submit` | Submit without asking each time. |
| `--tailor-resume` | Make a resume for each job from your profile. |
| `--no-ai` | Tailor with rules only, without Claude. |
| `--headless` | Hide the browser window. |

## How matching works

Postings are scored against a fixed preset for your major, never against your
resume. Computer Engineering always means the same terms, whichever resume you
pick. Every run prints what matched so you can see why a posting passed.

The presets live in `majors.json`. Each major has four tiers:

| Tier | Example for Computer Engineering | Effect |
| --- | --- | --- |
| Major name | computer engineering, CMPE | In the title: 100%. Anywhere in the posting: at least 90%. |
| Core terms | FPGA, Verilog, embedded, firmware, ASIC | 3 points each |
| Related terms | C++, Python, PCB, oscilloscope, Linux | 1 point each |
| Title words | FPGA, embedded, hardware, software, chip | 25% boost when in the job title |

Core and related points are capped at 12, which is worth 75%. Four core terms
are enough to max that out. The title boost adds the last 25%.

So a posting that names your major passes on any setting, a textbook FPGA
internship scores about 88%, and a veterinary internship scores 0%.

### How picky it is

Choose in the launcher, or with `--strictness` on the command line:

| Setting | Minimum score | In practice |
| --- | --- | --- |
| Broad | 15% | Most internships in your field, including nearby ones |
| Balanced | 25% | The default |
| Strict | 40% | Only close matches |

`--min-score` sets an exact number instead. A posting passes when the
percentage shown is at least the minimum.

### Editing a major

Open `majors.json` and add or remove terms in any list. Terms match whole words
regardless of capitalization, so "rtl" won't fire inside "portal". Changes take
effect on the next run.

A posting also has to clear hard filters before it is scored at all:

- **It must be an internship or co-op.**
- **It must run in summer.** Handshake lists dates like "From May 30, 2027 to
  August 7, 2027". Postings that clearly run in fall, winter or spring are
  dropped. Postings with no dates at all are kept, unless you set
  `include_undated_internships` to false.
- **It must match your locations,** if you set any.
- **It must still be open.**

Matches that apply on the employer's own site are kept for your apply-yourself
list rather than dropped.

If almost nothing gets through, choose Broad and raise `--pages`. If postings
outside your field get through, choose Strict.

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
document matches. Without them, the tool still picks a saved transcript when a
form requires one. It never picks a cover letter you didn't name.

## When it breaks

Handshake ships DOM changes regularly, and any tool like this breaks when they
do. Everything fragile is isolated in `selectors.json`, which holds a list of
CSS selector candidates per element, tried in order.

To repair it: open the page in Chrome, inspect the element it can no longer
find, and add the working selector to the front of the matching list. No Python
changes needed.

The tool tells you plainly when something outside its control stops it:

- **"Handshake is showing a security check."** Handshake's bot protection
  stepped in. The tool stops rather than trying to get around it. Try again
  later, or apply in your normal browser.
- **"Could not find Handshake's job search box"** or **"redirected the job
  search".** Handshake changed its search page.

If search breaks, set the search address yourself. Search on Handshake by hand
with the filters you want and copy the address from the address bar. Put it in
`config.json` as `search_url_template`, with `{query}` in place of your search
words and `{page}` in place of the page number. Drop the job number after
`/job-search`:

```json
"search_url_template": "{base}/job-search?query={query}&per_page=25&sort=relevance&page={page}&jobType=3"
```

## Tests

None of these touch the real Handshake. The browser tests run against a local
mock served from `tests/fake_handshake.py`. Its layout copies Handshake's real
job search and application form as they looked in September 2026. They also run automatically on
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

```bash
python tests/launcher_test.py
```

```bash
python tests/tailor_test.py
```

The tailoring tests use a made-up student in `tests/sample_profile.json` and a
fake Claude program, so they never read your profile or use your Claude plan.

## Files

| File | Role |
| --- | --- |
| `Start Internship Assistant.bat` | The double-click starting point. |
| `launcher.py` | The window behind the double-click, plus first-time setup. |
| `main.py` | Command line interface and the run loop. |
| `handshake.py` | Playwright driver: login, search, job pages, submission. |
| `matcher.py` | Scoring and the internship, summer and location filters. |
| `majors.py` | Loads the majors and asks which one you want. |
| `majors.json` | Each major's searches and scoring presets. Edit freely. |
| `resume_parser.py` | Resume text extraction and keyword detection. |
| `storage.py` | Application ledger, apply-yourself list and CSV export. |
| `ranking.py` | Fit score that orders the apply-yourself list. |
| `tailor.py` | Resume tailoring, fact checks and the one-page PDF. |
| `profile/career_profile.json` | Your facts for tailoring. Local only. |
| `selectors.json` | CSS fallbacks, the part to edit when Handshake changes. |
| `config.example.json` | Template for `config.json`. |

## Limits worth knowing

- Most internships on Handshake apply on the employer's own website. The tool
  can't fill those in, so they go on your apply-yourself list.
- Written questions, phone numbers and other personal fields are never filled
  in. Those forms go on your apply-yourself list too.
- Required fields are detected from the form's own markings. A question that is
  required but not marked that way can't be seen, so watch the first few runs.
- Nothing here writes cover letters.
- The fact checks catch invented numbers, tools, and listed false claims. They
  can't catch every possible stretch of the truth, so skim `plan.json` or the
  PDF for the first few jobs.
- **What has been checked on the real site:** search, result pages, job
  details, the three Apply button types, and opening and filling the
  application form in a practice run.
- **What hasn't:** an actual submission, the confirmation Handshake shows
  afterward, and what "Quick apply" does when pressed. Use "Apply, asking me
  before each one" for your first real applications and watch what happens.
- `data/applied.json` is what stops repeat applications. Keep it.
