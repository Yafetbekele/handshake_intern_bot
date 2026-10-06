"""Fill in and send applications on employers' own career sites.

Most postings in the ranked lists apply on the employer's site, not Handshake.
This reads whatever form that site shows, answers it from the student's saved
answers, attaches their documents, and (when asked to) presses Submit.

Design notes
    * It was written against the three hiring systems most of those companies
      use, none of which needs an account: Greenhouse, Lever and Ashby. Their
      real forms were looked at, read only, in October 2026. Other single-page
      forms are read the same way, but only in the modes where the student
      checks the form before it is sent.
    * Sites that want an account first (Workday, iCIMS, Taleo and the like) are
      reported, never signed up for. This code never types a password.
    * A question is answered only with the student's own saved words, or with
      what they type when asked. Nothing is guessed, and Social Security
      numbers and financial details are never filled in. The one exception is
      an open question ("Why do you want to work here?"), which
      essay_answers.py may write from the student's profile, fact checked.
    * A security check (CAPTCHA) or an emailed code is never worked around. The
      student passes it in the browser window, or the application is left.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import essay_answers
from handshake import NEVER_FILL, SENSITIVE_QUESTION, PlaywrightTimeout, chime

# ------------------------------------------------------------------ which site

FORM_SITES = {
    "greenhouse": ("greenhouse.io",),
    "lever": ("lever.co",),
    "ashby": ("ashbyhq.com",),
}

# These want the student to make an account and sign in before applying.
ACCOUNT_SITES = {
    "Workday": ("myworkdayjobs.com", "myworkdaysite.com", "workday.com"),
    "iCIMS": ("icims.com",),
    "Taleo": ("taleo.net",),
    "SuccessFactors": ("successfactors.com", "successfactors.eu", "sapsf.com"),
    "Oracle": ("oraclecloud.com",),
    "ADP": ("adp.com",),
    "UKG": ("ultipro.com", "ukg.com"),
    "BrassRing": ("brassring.com",),
    "Dayforce": ("dayforcehcm.com",),
    "Paylocity": ("paylocity.com",),
    "Paycom": ("paycomonline.net",),
    "Avature": ("avature.net",),
    "USAJOBS": ("usajobs.gov",),
    "GovernmentJobs": ("governmentjobs.com",),
    "LinkedIn": ("linkedin.com",),
    "Indeed": ("indeed.com",),
}

SITE_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby"}


def _host_matches(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def site_kind(url: str) -> tuple[str, str]:
    """('greenhouse' | 'lever' | 'ashby' | 'account' | 'other', a name to show)."""
    host = urlparse(url).netloc.lower().split(":")[0]
    for kind, domains in FORM_SITES.items():
        if _host_matches(host, domains):
            return kind, SITE_NAMES[kind]
    for name, domains in ACCOUNT_SITES.items():
        if _host_matches(host, domains):
            return "account", name
    return "other", host or "unknown site"


def apply_address(url: str) -> str:
    """The address of the form itself, where the posting and its form are separate pages."""
    kind, _ = site_kind(url)
    parts = urlparse(url)
    path = parts.path.rstrip("/")
    if kind == "lever" and not path.endswith("/apply") and re.search(r"/[0-9a-f-]{36}$", path):
        return parts._replace(path=path + "/apply").geturl()
    if kind == "ashby" and not path.endswith("/application") and re.search(r"/[0-9a-f-]{36}$", path):
        return parts._replace(path=path + "/application").geturl()
    return url


# -------------------------------------------------------------------- answers


def clean_label(text: str) -> str:
    """A question as the student would read it, without required marks."""
    text = re.sub(r"[*✱]", " ", text or "")
    text = re.sub(r"\(\s*(required|optional)\s*\)", " ", text, flags=re.IGNORECASE)
    text = " ".join(text.split()).strip(" :")
    # Some sites tack the field's own code onto its label ("Location 296526d0").
    return re.sub(r"\s+(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{8}$", "", text)


def norm(text: str) -> str:
    return clean_label(str(text)).lower()


def _loose(text: str) -> str:
    """For comparing option text: letters and digits, plus the "+" and "#" that tell C++ and C# from C."""
    return re.sub(r"[^a-z0-9+#]+", " ", norm(text)).strip()


def _has_words(text: str, phrase: str) -> bool:
    return bool(phrase) and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


# "If yes, what type of sponsorship?" is not the sponsorship question. A
# follow-up like this is only answered by an answer saved for exactly it.
FOLLOW_UP = re.compile(
    r"^(if (yes|no|so|not|other|applicable|you|selected|your)\b|other\b|please (specify|explain|describe|elaborate|list|provide))",
    re.IGNORECASE,
)


# "Master's GPA" is not the GPA question, and "highest degree completed" is not
# the degree being studied for. And a question turned around ("Is there
# anything that would prevent you from working on site?") must not get the
# answer to the plain one. These need an answer saved for exactly them, or to
# be read properly (form_answers.py), not matched on a phrase.
NOT_THE_USUAL = re.compile(
    r"master'?s|doctora|ph\.?\s?d\b|graduate (school|degree)|\bmba\b|high school|highest|completed|obtained|earned|previous|"
    r"\bwithout\b|\bunable\b|\bcannot\b|\bnot (be )?(able|willing|available)\b|prevent|restrict|limitation|conflict|"
    r"barrier|any reason|\bobject",
    re.IGNORECASE,
)


def candidates(label: str, answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Saved answers that fit this question, best first.

    An answer saved for exactly this question comes first, newest first. After
    that the longest matching phrase wins, so "authorized to work" beats
    "state" in "...authorized to work in the United States?". One common word
    inside a long question doesn't count as a match at all.
    """
    text = norm(label)
    if not text:
        return []
    words = len(text.split())
    follow_up = bool(FOLLOW_UP.search(text) or NOT_THE_USUAL.search(text))
    exact: list[dict[str, Any]] = []
    partial: list[tuple[int, int, dict[str, Any]]] = []
    for position, answer in enumerate(answers):
        best = 0
        for phrase in answer.get("match", []):
            p = norm(str(phrase))
            if not p:
                continue
            if p == text:
                best = -1
                break
            if answer.get("exact") or follow_up:
                continue
            if words > int(answer.get("max_words", 99)):
                continue
            if len(p) >= 40 and text.startswith(p):  # a long question saved cut short
                best = max(best, len(p))
            elif _has_words(text, p) and (" " in p or words <= 8):
                best = max(best, len(p))
        if best == -1:
            exact.append(answer)
        elif best:
            partial.append((best, position, answer))
    partial.sort(key=lambda item: (-item[0], -item[1]))
    return exact[::-1] + [answer for _, _, answer in partial]


YES = {"yes", "y", "true"}
NO = {"no", "n", "false"}


def pick_option(options: list[str], value: str) -> int | None:
    """Which option an answer means, or None unless exactly one fits."""
    want = _loose(value)
    if not want:
        return None
    loose = [_loose(o) for o in options]
    for index, option in enumerate(loose):
        if option == want:
            return index
    if want in YES | NO:
        word = "yes" if want in YES else "no"
        hits = [i for i, option in enumerate(loose) if re.match(rf"{word}\b", option)]
        return hits[0] if len(hits) == 1 else None
    hits = [i for i, option in enumerate(loose) if _has_words(option, want)]
    if len(hits) == 1:
        return hits[0]
    hits = [i for i, option in enumerate(loose) if len(option) >= 4 and _has_words(want, option)]
    if len(hits) == 1:
        return hits[0]
    return None


def contact_answers(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Name, email and the like from the profile's contact block.

    These only answer short labels ("Email", "First Name"), never a sentence
    that happens to contain the word, and a saved answer always comes first.
    """
    contact = profile.get("contact") or {}
    name = str(contact.get("name") or "").strip()
    parts = name.split()
    found: list[dict[str, Any]] = []

    def add(phrases: list[str], value: Any, **extra: Any) -> None:
        if str(value or "").strip():
            found.append({"match": phrases, "value": str(value).strip(), "max_words": 4, **extra})

    add(["name", "full name", "your name", "legal name", "full legal name", "legal first and last name",
         "first and last name"], name, exact=True)
    if len(parts) >= 2:
        add(["first name", "given name", "legal first name", "preferred first name", "preferred name"], parts[0])
        add(["last name", "surname", "family name", "legal last name"], parts[-1])
    add(["email", "e-mail", "email address"], contact.get("email"))
    add(["phone", "mobile", "telephone", "phone number"], contact.get("phone"))
    add(["linkedin"], contact.get("linkedin"))
    add(["github"], contact.get("github"))
    add(["website", "portfolio"], contact.get("website") or contact.get("portfolio"))
    add(["location", "current location", "city", "location (city)"], contact.get("location"), exact=True)
    if re.search(r",\s*[A-Z]{2}\b", str(contact.get("location") or "")):
        add(["country", "country of residence"], "United States", exact=True)
    return found


def education_answers(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """School, degree, major, GPA and graduation from the profile's first education entry.

    Forms ask for these in pieces ("End date month", "Discipline", "Degree"
    with their own list of choices), so each fact is offered in the shapes a
    form might want. Like the contact details, they only answer short labels.
    """
    schools = profile.get("education") or []
    school = schools[0] if schools and isinstance(schools[0], dict) else {}
    found: list[dict[str, Any]] = []

    def add(phrases: list[str], value: Any) -> None:
        if str(value or "").strip():
            found.append({"match": phrases, "value": str(value).strip(), "max_words": 6})

    degree = str(school.get("degree") or "")  # "B.S. in Computer Engineering"
    split = re.match(r"\s*(.+?)\s+in\s+(.+)", degree)
    level = ""
    if re.match(r"\s*(b\.?\s?[sae]\.?\b|bachelor)", degree, re.IGNORECASE):
        level = "Bachelor's"
    elif re.match(r"\s*(m\.?\s?[sae]\.?\b|master)", degree, re.IGNORECASE):
        level = "Master's"
    elif re.match(r"\s*(a\.?\s?[sa]\.?\b|associate)", degree, re.IGNORECASE):
        level = "Associate's"
    when = re.match(r"\s*([A-Za-z]+)\.?\s+(\d{4})", str(school.get("expected_graduation") or ""))

    add(["school", "university", "college", "college/university", "institution", "school name"], school.get("school"))
    add(["degree", "degree level", "degree type", "degree level currently pursuing", "degree pursuing",
         "current degree", "level of education"], level)
    add(["major", "discipline", "field of study", "area of study", "major/field of study", "course of study"],
        split.group(2) if split else "")
    add(["gpa", "undergrad gpa", "undergraduate gpa", "cumulative gpa", "current gpa", "overall gpa"], school.get("gpa"))
    add(["class standing", "year in school", "current year in school", "academic standing", "class year"],
        school.get("standing"))
    if when:
        add(["graduation month", "end date month", "expected graduation month"], when.group(1))
        add(["graduation year", "end date year", "expected graduation year", "year of graduation"], when.group(2))
        add(["graduation date", "expected graduation", "expected graduation date", "anticipated graduation",
             "anticipated graduation date"], f"{when.group(1)} {when.group(2)}")
    return found


def profile_answers(profile: dict[str, Any]) -> list[dict[str, Any]]:
    """Everything the profile itself answers, before any saved answers."""
    return contact_answers(profile) + education_answers(profile)


def remember_answers(profile_path: Path, learned: list[dict[str, Any]]) -> int:
    """Keep what the student typed during a run, in their profile, for next time."""
    import json

    if not learned or not profile_path.exists():
        return 0
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    saved = profile.setdefault("application_answers", [])
    if not isinstance(saved, list):
        return 0
    changed = 0
    for answer in learned:
        entry = {k: answer[k] for k in ("match", "value", "sensitive") if k in answer}
        same = next((a for a in saved if a.get("match") == entry["match"]), None)
        if same is None:
            saved.append(entry)
            changed += 1
        elif same.get("value") != entry["value"]:
            same["value"] = entry["value"]
            changed += 1
    if changed:
        try:
            profile_path.write_text(json.dumps(profile, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            return 0
    return changed


def document_kind(label: str, hint: str = "") -> str | None:
    """resume, cover_letter or transcript for an upload slot, or None for anything else."""
    text = f"{label} {hint}".lower().replace("_", " ")
    if "autofill" in text or "auto-fill" in text:
        return None  # a "fill this form from your resume" shortcut, not the application's resume
    if "cover" in text:
        return "cover_letter"
    if "transcript" in text:
        return "transcript"
    if "resume" in text or "résumé" in text or re.search(r"\bcv\b", text):
        return "resume"
    return None


# Labels for controls a site leaves unlabeled.
NAME_LABELS = {"opportunityLocationId": "Which location are you applying for?"}

CLOSED_TEXT = re.compile(
    r"no longer (available|accepting|open|active|posted)|has been (filled|closed|removed)|"
    r"job (not found|has expired|is closed|you are looking for)|posting (has )?(expired|closed)|"
    r"position (is|has been) (closed|filled)|page (not found|doesn.t exist|you.re looking for)|"
    r"couldn.t find (anything|that|the page)|this job is closed",
    re.IGNORECASE,
)
# An address that is a sign-in or sign-up page, whatever the site.
SIGN_IN_PATH = re.compile(r"/(login|log-in|signin|sign-in|sso|signup|sign-up|register|auth)(/|$)", re.IGNORECASE)
CONFIRM_URL = re.compile(r"confirmation|/thanks|thank[-_]?you|/success|submitted", re.IGNORECASE)
CONFIRM_TEXT = re.compile(
    r"thank you for applying|thanks for applying|thank you for your application|"
    r"application (has been|was) (successfully )?(submitted|received|sent)|"
    r"(successfully|has been) submitted|we('ve| have) received your application|"
    r"application submitted|application received|your application is (in|complete)",
    re.IGNORECASE,
)
CODE_TEXT = re.compile(r"(verification|security) code|code (we )?sent to|enter the code|check your email for a code",
                       re.IGNORECASE)
APPLY_LINK = re.compile(
    r"^\s*(apply|apply now|apply here|apply today|apply for (this|the) (job|position|role)|"
    r"start (your )?application|i.m interested)\s*$",
    re.IGNORECASE,
)
SUBMIT_PATTERNS = [
    re.compile(r"submit\s+(my\s+)?application", re.IGNORECASE),
    re.compile(r"^\s*submit\s*$", re.IGNORECASE),
    re.compile(r"send\s+(my\s+)?application|complete\s+application|^\s*apply(\s+now)?\s*$", re.IGNORECASE),
]

# ------------------------------------------------------------------ page code

# Finds the application form on a page and says whose form it looks like.
FIND_ROOT_JS = r"""
() => {
  document.querySelectorAll('[data-hsbot-root]').forEach(e => e.removeAttribute('data-hsbot-root'));
  const shown = el => {
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  };
  const usable = el => [...el.querySelectorAll('input, textarea, select')].filter(c => {
    const t = (c.getAttribute('type') || '').toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password'].includes(t)) return false;
    return t === 'file' || t === 'radio' || t === 'checkbox' || shown(c);
  });
  let flavor = '';
  if (document.querySelector('[class*="ashby-application-form"]')) flavor = 'ashby';
  else if (document.querySelector('#application-form .application-question, form .application-question .application-field')) flavor = 'lever';
  else if (document.querySelector('form.application--form, form#application_form, #application-form [id^="question_"]')) flavor = 'greenhouse';

  let best = null, most = 0;
  // Ashby has no wrapper around its form, only one around each section of it,
  // so the whole page is read there.
  const selectors = flavor === 'ashby' ? [] : ['#application-form', '#application_form', 'form[id*="application" i]',
                                               'form[class*="application" i]', '[class*="application-form" i]', 'form'];
  for (const selector of selectors) {
    for (const el of document.querySelectorAll(selector)) {
      const count = usable(el).length;
      if (count > most) { best = el; most = count; }
    }
    if (best && most >= 3) break;
  }
  if (!best || most < 2) { best = document.body; most = usable(best).length; }
  const controls = usable(best);
  const file = controls.some(c => c.type === 'file');
  const email = controls.some(c => c.type === 'email' || /e-?mail/i.test((c.name || '') + ' ' + (c.id || '') + ' ' + (c.getAttribute('autocomplete') || '')));
  const password = [...document.querySelectorAll('input[type="password"]')].some(shown);
  const ok = flavor ? most >= 2 : (most >= 3 && (file || email) && !password);
  if (ok && best !== document.body) best.setAttribute('data-hsbot-root', '1');
  return { ok, selector: ok && best !== document.body ? '[data-hsbot-root="1"]' : '', flavor, count: most, password };
}
"""

# Lists every question on the form: what it asks, what kind of control answers
# it, its choices, whether it is required and whether it is still empty. Each
# control is tagged data-hsbot-f, and each choice data-hsbot-o, so it can be
# found again to fill it in.
SCAN_JS = r"""
(rootSelector) => {
  const root = (rootSelector && document.querySelector(rootSelector)) || document.body;
  for (const a of ['data-hsbot-f', 'data-hsbot-o']) document.querySelectorAll('[' + a + ']').forEach(e => e.removeAttribute(a));
  const tidy = t => (t || '').replace(/\s+/g, ' ').trim();
  const shown = el => {
    if (!el) return false;
    const s = getComputedStyle(el);
    return s.display !== 'none' && s.visibility !== 'hidden' && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  };
  const strip = el => {
    if (!el) return '';
    const copy = el.cloneNode(true);
    copy.querySelectorAll('input, select, textarea, button, option, script, style, svg, [role="listbox"], [role="option"], .select__control, .select__menu, [aria-live]').forEach(e => e.remove());
    copy.querySelectorAll('*').forEach(e => e.after(' '));  // so neighbouring blocks don't run together
    return tidy(copy.textContent);
  };
  const byIds = ids => tidy((ids || '').split(/\s+/).filter(Boolean).map(i => strip(document.getElementById(i))).join(' '));
  const labelFor = el => el.id ? document.querySelector('label[for="' + CSS.escape(el.id) + '"]') : null;
  const real = c => (c.getAttribute('type') || '').toLowerCase() !== 'hidden' && c.getAttribute('aria-hidden') !== 'true';
  const outside = el => root === document.body && !!el.closest('nav, header, footer, [role="search"], [id*="cookie" i], [class*="cookie" i], [id*="onetrust" i]');

  // The block that holds one question and nothing else.
  const ENTRY = '[class*="fieldEntry"], .application-question, .select__container, .form-group, .form-field, .field-wrapper, .field';
  const entryOf = el => {
    const e = el.closest(ENTRY);
    if (!e || e === root || e.contains(root)) return null;
    const groups = new Set();
    for (const c of e.querySelectorAll('input, textarea, select')) if (real(c)) groups.add(c.getAttribute('name') || c.id || c);
    return groups.size <= 1 ? e : null;
  };
  const entryLabel = el => {
    const e = entryOf(el);
    if (!e) return '';
    const head = e.querySelector('.application-label') || e.querySelector('legend, label, [class*="question-title" i], [class*="label" i]');
    if (!head) return '';
    if (head.contains(el) && head.matches('label') && e.querySelectorAll('label').length > 1) return '';
    if (labelFor(el) === head && e.querySelectorAll('label').length > 1) return '';
    return strip(head);
  };
  const nearby = el => {
    let p = el.parentElement;
    for (let i = 0; i < 5 && p && p !== root; i++, p = p.parentElement) {
      for (const head of p.querySelectorAll(':scope > legend, :scope > label, :scope > [class*="label" i], :scope > [class*="question" i], :scope > h3, :scope > h4, :scope > p')) {
        if (head.contains(el) || (el.id && head.getAttribute('for') === el.id)) continue;
        if (head.querySelector('input, select, textarea')) continue;
        const t = strip(head);
        if (t) return t.slice(0, 300);
      }
    }
    return '';
  };
  const ownLabel = el => {
    let t = strip(labelFor(el));
    if (t) return t;
    t = byIds(el.getAttribute('aria-labelledby'));
    if (t) return t;
    t = tidy(el.getAttribute('aria-label'));
    if (t) return t;
    const wrap = el.closest('label');
    return wrap ? strip(wrap.querySelector('.application-label') || wrap) : '';
  };
  const fieldLabel = el => ownLabel(el) || entryLabel(el) || nearby(el) || tidy(el.getAttribute('placeholder')) || '';
  const groupLabel = el => {
    const set = el.closest('fieldset, [role="radiogroup"], [role="group"]');
    if (set && set !== root && !set.contains(root)) {
      const t = byIds(set.getAttribute('aria-labelledby')) || tidy(set.getAttribute('aria-label')) || strip(set.querySelector('legend'));
      if (t) return t;
    }
    return entryLabel(el) || nearby(el);
  };
  const marked = raw => /[*✱]\s*$/.test(raw) || /\(required\)/i.test(raw);
  const isRequired = (el, raw) => {
    if (/\(optional\)/i.test(raw)) return false;
    if (el.required || el.getAttribute('aria-required') === 'true' || marked(raw)) return true;
    const set = el.closest('fieldset, [role="group"], [role="radiogroup"]');
    if (set && set.getAttribute('aria-required') === 'true') return true;
    const e = entryOf(el);
    return !!(e && e.querySelector('.required, [class*="required" i]'));
  };
  const blank = t => !t || /^(select|please select|choose|--|—|- select)/i.test(t);

  const out = [];
  const used = new Set();
  let n = 0;
  const add = (el, f) => {
    f.n = n++;
    el.setAttribute('data-hsbot-f', String(f.n));
    f.y = Math.round(el.getBoundingClientRect().top + window.scrollY);
    f.hint = tidy((el.id || '') + ' ' + (el.getAttribute('name') || ''));
    f.raw = (f.raw || '').slice(0, 400);
    out.push(f);
    return f;
  };
  const tag = (f, el, i) => el.setAttribute('data-hsbot-o', f.n + '-' + i);

  // Yes / No offered as a pair of buttons.
  const yesNo = b => /^(yes|no)$/i.test(tidy(b.innerText));
  for (const b of root.querySelectorAll('button, [role="button"]')) {
    if (used.has(b) || !shown(b) || !yesNo(b)) continue;
    const box = b.parentElement;
    const pair = [...box.children].filter(x => x.matches('button, [role="button"]') && shown(x));
    if (pair.length !== 2 || !pair.every(yesNo)) continue;
    pair.forEach(x => used.add(x));
    (entryOf(b) || box).querySelectorAll('input[type="checkbox"], input[type="radio"]').forEach(x => used.add(x));
    const raw = entryLabel(b) || groupLabel(b);
    const on = x => x.getAttribute('aria-pressed') === 'true' || x.getAttribute('aria-checked') === 'true'
      || /(^|[\s_-])(active|selected|checked)([\s_-]|$)/i.test(x.className);
    const f = add(box, { kind: 'buttons', raw, required: isRequired(b, raw), options: pair.map(x => tidy(x.innerText)), empty: !pair.some(on) });
    pair.forEach((x, i) => tag(f, x, i));
  }

  for (const el of root.querySelectorAll('input, textarea, select, [role="combobox"]')) {
    if (used.has(el)) continue;
    const name = el.tagName.toLowerCase();
    const type = name === 'input' ? (el.getAttribute('type') || 'text').toLowerCase() : name;
    if (['hidden', 'submit', 'button', 'reset', 'image', 'search', 'password'].includes(type)) continue;
    if (el.disabled || el.getAttribute('aria-hidden') === 'true') continue;
    if (/captcha/i.test((el.getAttribute('name') || '') + ' ' + el.id + ' ' + el.className)) continue;
    if (outside(el)) continue;

    if (type === 'radio' || type === 'checkbox') {
      const group = el.getAttribute('name');
      let members = group ? [...root.querySelectorAll('input[type="' + type + '"]')].filter(m => m.getAttribute('name') === group) : [el];
      members.forEach(m => used.add(m));
      members = members.filter(m => !m.disabled && (shown(m) || shown(m.closest('label')) || shown(labelFor(m))));
      if (!members.length) continue;
      const optionText = m => ownLabel(m) || tidy(m.value);
      const lone = type === 'checkbox' && members.length === 1;
      let raw = groupLabel(el);
      if (lone) {
        const option = optionText(el);
        raw = raw && raw !== option ? raw + ': ' + option : option;
      }
      const f = add(el, {
        kind: lone ? 'checkbox' : (type === 'radio' ? 'radio' : 'checkboxes'), raw,
        required: members.some(m => isRequired(m, raw)), options: lone ? ['Yes', 'No'] : members.map(optionText),
        empty: !members.some(m => m.checked),
      });
      members.forEach((m, i) => tag(f, m, i));
      continue;
    }
    if (type === 'file') {
      const group = el.closest('[role="group"][aria-labelledby]');
      const raw = (group && byIds(group.getAttribute('aria-labelledby'))) || entryLabel(el) || ownLabel(el);
      add(el, { kind: 'file', raw, required: isRequired(el, raw), options: [], empty: !(el.files && el.files.length) });
      continue;
    }
    if (!shown(el)) continue;
    if (name === 'select') {
      const options = [...el.options].filter(o => o.value !== '' && !o.disabled && !blank(tidy(o.text)));
      const chosen = el.selectedOptions[0];
      const raw = fieldLabel(el);
      add(el, { kind: 'select', raw, required: isRequired(el, raw), options: options.map(o => tidy(o.text)),
                empty: !chosen || chosen.value === '' || blank(tidy(chosen.text)) });
      continue;
    }
    if (el.getAttribute('role') === 'combobox') {
      const shell = el.closest('.select__control, [class*="control" i]') || el.parentElement;
      const has = !!(shell && shell.querySelector('[class*="single-value" i], [class*="singleValue"], [class*="multi-value" i], [class*="multiValue"]'));
      const text = tidy(name === 'input' ? el.value : el.innerText);
      const raw = fieldLabel(el);
      add(el, { kind: 'combobox', raw, required: isRequired(el, raw), options: [], empty: !has && blank(text) });
      continue;
    }
    const raw = fieldLabel(el);
    add(el, { kind: name === 'textarea' ? 'textarea' : 'text', raw, required: isRequired(el, raw), options: [],
              empty: !tidy(el.value), value: tidy(el.value).slice(0, 200), type, max: el.maxLength > 0 ? el.maxLength : 0 });
  }
  return out.sort((a, b) => a.y - b.y);
}
"""

CAPTCHA_JS = r"""
() => [...document.querySelectorAll('iframe')].some(f => {
  if (!/recaptcha.*bframe|hcaptcha.*challenge|challenges\.cloudflare|arkoselabs|funcaptcha/i.test(f.src || '')) return false;
  const s = getComputedStyle(f);
  const box = f.getBoundingClientRect();
  return s.display !== 'none' && s.visibility !== 'hidden' && box.height > 80 && box.width > 80;
})
"""

ERRORS_JS = r"""
() => {
  const shown = el => { const s = getComputedStyle(el); return s.display !== 'none' && s.visibility !== 'hidden' && !!(el.offsetWidth || el.offsetHeight); };
  const found = [];
  for (const el of document.querySelectorAll('[role="alert"], [class*="error" i], [class*="invalid" i]')) {
    if (!shown(el) || el.querySelector('input, select, textarea, form')) continue;
    const t = (el.innerText || '').replace(/\s+/g, ' ').trim();
    if (t && t.length < 160 && !found.includes(t)) found.push(t);
  }
  return found.slice(0, 4);
}
"""

# ---------------------------------------------------------------------- types

# Asks the student one question. `options` lists the choices when there are
# any; `many` means several may be picked. Returns their answer (the option
# text for a choice), a list for `many`, or None when they gave none.
Asker = Callable[[str, "list[str] | None", bool], "str | list[str] | None"]
# Gives the file for "resume", "cover_letter" or "transcript", or None.
DocumentSource = Callable[[str, bool], "Path | None"]
# Writes the answer to an open question: (question, most characters or 0, is
# it a one-line box). None leaves the question for the student.
EssayWriter = Callable[[str, int, bool], "str | None"]
# Settles what it can of the questions nothing saved answers, from the
# student's profile. Takes [{"id", "question", "choices" or None, "pick":
# "one" | "any" | "text"}] and returns {id: (answer, where it comes from)}.
WorkOut = Callable[["list[dict[str, Any]]"], "dict[int, tuple[Any, str]]"]


@dataclass
class FormReport:
    filled: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    attached: set[str] = field(default_factory=set)
    written: list[tuple[str, str]] = field(default_factory=list)  # (question, the answer written for it)
    worked: list[tuple[str, str, str]] = field(default_factory=list)  # (question, answer, the profile fact behind it)
    guessed: list[tuple[str, str, str]] = field(default_factory=list)  # (question, best-judgement answer, why)
    unanswered: list[dict[str, Any]] = field(default_factory=list)  # required questions left empty, with their choices
    choices: dict[str, list[str]] = field(default_factory=dict)  # choices of the questions that were put to Claude


class EmployerSite:
    """One browser tab on an employer's application form."""

    def __init__(
        self,
        page: Any,
        answers: list[dict[str, Any]],
        ask: Asker | None = None,
        person_present: bool = False,
        patience: float = 300.0,
        say: Callable[[str], None] = print,
    ) -> None:
        self.home = page
        self.page = page
        self.frame = page.main_frame
        self.answers = answers
        self.ask = ask
        self.person_present = person_present
        self.patience = patience
        self.say = say
        self.root = ""
        self.flavor = ""
        self.learned: list[dict[str, Any]] = []
        self.attached: set[str] = set()
        self._handled: set[tuple[str, str, str]] = set()
        self._given: set[tuple[str, str, str]] = set()  # questions this code answered, to notice a wiped one
        self._extra_tabs: list[Any] = []
        self.choices: dict[str, list[str]] = {}  # the choices of lists that had to be opened to read them
        self._essays: EssayWriter | None = None

    # ------------------------------------------------------------- opening

    def open(self, url: str) -> tuple[str, str]:
        """Go to a posting's form. Returns (state, note); state "form" means ready to fill.

        Other states: needs_account, closed, no_form, needs_manual, failed, and
        stopped (the tab is gone; nothing more can be done in it).
        """
        if not self.alive():
            return "stopped", "the browser tab was closed"
        self._reset()
        kind, name = site_kind(url)
        if kind == "account":
            return "needs_account", f"{name} wants you to sign in with your own account"
        try:
            self.page.goto(apply_address(url), wait_until="domcontentloaded", timeout=45_000)
        except PlaywrightTimeout:
            return "failed", "the employer's page took too long to open"
        except Exception as exc:
            if not self.alive():
                return "stopped", "the browser tab was closed"
            return "failed", f"the employer's page wouldn't open ({type(exc).__name__})"
        self._settle()
        if not self._pass_security_check():
            return "needs_manual", "the site showed a security check"

        for attempt in range(2):
            kind, name = site_kind(self.page.url)
            if kind == "account":
                return "needs_account", f"{name} wants you to sign in with your own account"
            if kind == "other" and SIGN_IN_PATH.search(urlparse(self.page.url).path):
                return "needs_account", "the site asks you to sign in first"
            password = closed = False
            # Some forms are drawn a few seconds after the page arrives, so look more than once.
            for _look in range(12 if kind in FORM_SITES else 5):
                for frame in self._frames():
                    try:
                        info = frame.evaluate(FIND_ROOT_JS)
                    except Exception:
                        continue
                    password = password or bool(info.get("password"))
                    if info.get("ok"):
                        self.frame = frame
                        self.root = info.get("selector") or ""
                        self.flavor = info.get("flavor") or (kind if kind in FORM_SITES else "")
                        if self._is_application():
                            return "form", SITE_NAMES.get(self.flavor, "a form this tool hasn't seen before")
                        self.frame, self.root, self.flavor = self.page.main_frame, "", ""
                closed = "error=true" in self.page.url or bool(CLOSED_TEXT.search(self._text(self.page.main_frame)))
                if password or closed:
                    break
                try:
                    self.page.wait_for_timeout(1000)
                except Exception:
                    break
            if password:
                return "needs_account", "the site asks you to sign in first"
            if closed:
                return "closed", "the posting is no longer open"
            if attempt == 0 and self._press_apply():
                continue
            break
        return "no_form", "couldn't find an application form on the page"

    def alive(self) -> bool:
        """False once the student has closed this tab, or the whole browser."""
        try:
            return not self.home.is_closed()
        except Exception:
            return False

    def _is_application(self) -> bool:
        """A careers page has search and job-alert boxes too; an application asks who you are."""
        fields = self.scan()
        if self.flavor:
            return len(fields) >= 2
        if any(f["kind"] == "file" for f in fields):
            return len(fields) >= 3
        labels = " | ".join(f["label"] for f in fields)
        return len(fields) >= 4 and bool(re.search(r"\bname\b", labels, re.IGNORECASE)) and bool(
            re.search(r"e-?mail", labels, re.IGNORECASE))

    def _reset(self) -> None:
        for tab in self._extra_tabs:
            try:
                tab.close()
            except Exception:
                pass
        self._extra_tabs = []
        self.page = self.home
        self.frame = self.page.main_frame
        self.root = self.flavor = ""
        self.attached = set()
        self._handled = set()
        self._given = set()
        self.choices = {}

    def _frames(self) -> list[Any]:
        main = self.page.main_frame
        others = [f for f in self.page.frames if f is not main and str(f.url).startswith("http")]
        return [main] + others[:6]

    def _settle(self, ms: int = 1200) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        try:
            self.page.wait_for_timeout(ms)
        except Exception:
            pass

    @staticmethod
    def _text(frame: Any, limit: int = 6000) -> str:
        try:
            return (frame.inner_text("body") or "")[:limit]
        except Exception:
            return ""

    def _press_apply(self) -> bool:
        """Some sites show the posting first, with the form behind an Apply button."""
        target = None
        for role in ("link", "button"):
            try:
                found = self.page.get_by_role(role, name=APPLY_LINK)
                for index in range(min(found.count(), 5)):
                    if found.nth(index).is_visible():
                        target = found.nth(index)
                        break
            except Exception:
                continue
            if target is not None:
                break
        if target is None:
            return False
        before = len(self.page.context.pages)
        try:
            target.click()
        except Exception:
            return False
        self._settle(1500)
        tabs = self.page.context.pages
        if len(tabs) > before:  # the form opened in a new tab; carry on there
            self.page = tabs[-1]
            self._extra_tabs.append(self.page)
            self._settle(1500)
        return True

    def _security_check_showing(self) -> bool:
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            title = ""
        if "just a moment" in title:
            return True
        body = self._text(self.page.main_frame, 1500).lower()
        return any(p in body for p in ("verify you are human", "checking your browser",
                                       "checking if the site connection is secure"))

    def _pass_security_check(self) -> bool:
        """Never answered here. The student passes it in the window, or it's left."""
        if not self._security_check_showing():
            return True
        if not self.person_present:
            return False
        self.say("  This site is showing a security check. Pass it yourself in the browser window;"
                 f" waiting up to {round(self.patience / 60)} minutes.")
        return self._wait_for(lambda: not self._security_check_showing(), self.patience)

    def _wait_for(self, done: Callable[[], bool], seconds: float) -> bool:
        try:
            self.page.bring_to_front()
        except Exception:
            pass
        started = time.monotonic()
        next_chime = started
        while time.monotonic() - started < seconds:
            if time.monotonic() >= next_chime:
                chime()
                next_chime = time.monotonic() + 30
            try:
                self.page.wait_for_timeout(1500)
                if done():
                    return True
            except Exception:
                if self.page.is_closed():
                    return False
        return False

    # -------------------------------------------------------------- reading

    def scan(self) -> list[dict[str, Any]]:
        try:
            fields = self.frame.evaluate(SCAN_JS, self.root)
        except Exception:
            return []
        for item in fields:
            label = clean_label(item.get("raw", ""))
            if not label:
                label = next((v for k, v in NAME_LABELS.items() if k in item.get("hint", "")), "")
            item["label"] = label
        return fields

    def _control(self, item: dict[str, Any]) -> Any:
        return self.frame.locator(f'[data-hsbot-f="{item["n"]}"]').first

    def _option(self, item: dict[str, Any], index: int) -> Any:
        return self.frame.locator(f'[data-hsbot-o="{item["n"]}-{index}"]').first

    @staticmethod
    def _key(item: dict[str, Any]) -> tuple[str, str, str]:
        return item["kind"], norm(item["label"]), item.get("hint", "")

    def missing_required(self) -> list[str]:
        """Required questions still empty, as the student would read them."""
        missing: list[str] = []
        fields = self.scan()
        if not fields:  # the tab was closed or went elsewhere: never mistake that for a finished form
            return ["the form itself, which can no longer be read"]
        for item in fields:
            if not item["required"] or not item["empty"]:
                continue
            label = item["label"] or "a field with no label"
            if item["kind"] == "file":
                kind = document_kind(label, item.get("hint", ""))
                if kind in self.attached:
                    continue
                label = label if kind else f"{label} (a file to attach)"
            if label[:90] not in missing:
                missing.append(label[:90])
        return missing

    def unanswered(self) -> list[dict[str, Any]]:
        """The required questions still empty, each with its choices: [{"label", "options"}]. Not files."""
        found: list[dict[str, Any]] = []
        for item in self.scan():
            label = item["label"]
            if not item["required"] or not item["empty"] or item["kind"] == "file" or not label:
                continue
            if all(label != other["label"] for other in found):
                found.append({"label": label, "options": item["options"] or self.choices.get(label, [])})
        return found

    # -------------------------------------------------------------- filling

    def fill(self, documents: DocumentSource, essays: EssayWriter | None = None,
             work_out: WorkOut | None = None, best_guess: WorkOut | None = None) -> FormReport:
        """Fill the form in. Returns what was done and what is still empty.

        Each question is answered from the first of these that has an answer:
        the student's saved answers and profile facts; for an open question,
        an answer written from their profile (`essays`); what `work_out` can
        settle from the profile for the questions still left; the student, at
        the keyboard; and, for what they leave unanswered, `best_guess`.
        """
        report = FormReport()
        self._essays = essays
        # Documents first: Lever and Ashby read the resume and fill in a few
        # fields themselves, which the saved answers then correct.
        for item in self.scan():
            if item["kind"] == "file":
                self._handled.add(self._key(item))
                self._attach(item, documents, report)
        if report.attached:
            self._after_upload()
        # Answering one question can reveal another, so look again a few times.
        for round_number in range(3):
            todo = [i for i in self.scan() if self._key(i) not in self._handled]
            if not todo:
                break
            waiting = []
            for item in todo:
                self._handled.add(self._key(item))
                if not self._try(item, report, overwrite=round_number == 0) and self._needs_answer(item):
                    waiting.append(self._key(item))
            if waiting and work_out is not None:
                self._work_out(waiting, work_out, report, report.worked)
            if waiting and self.ask is not None:
                for item in self.scan():
                    if self._key(item) in waiting and self._needs_answer(item):
                        self._try(item, report, overwrite=False, ask=True)
            if waiting and best_guess is not None:  # asked and not answered, or nobody there to ask
                self._work_out(waiting, best_guess, report, report.guessed)
            self._pause(500)
        self._repair()
        report.attached = set(self.attached)
        report.missing = self.missing_required()
        report.unanswered = self.unanswered()
        report.choices = dict(self.choices)
        return report

    def _pause(self, ms: int) -> None:
        try:
            self.page.wait_for_timeout(ms)
        except Exception:
            pass

    @staticmethod
    def _needs_answer(item: dict[str, Any]) -> bool:
        return bool(item["required"] and item["empty"] and item["label"]
                    and item["kind"] != "file" and not NEVER_FILL.search(item["label"]))

    def _try(self, item: dict[str, Any], report: FormReport, overwrite: bool, ask: bool = False) -> bool:
        """Answer one question if there is an answer for it. True when it now has one."""
        try:
            answered = self._answer(item, report, overwrite, ask)
        except Exception as exc:  # one awkward control never stops the form
            self.say(f"  [note] couldn't fill in \"{item['label'][:60]}\" ({type(exc).__name__})")
            return False
        if answered and item["empty"]:
            self._given.add(self._key(item))
        return answered

    def _repair(self) -> bool:
        """Give again any answer the site wiped. True when there was one to give.

        Some sites redraw the form once they finish reading the resume, which
        empties questions that were already answered.
        """
        self._pause(1000)
        wiped = [i for i in self.scan() if i["empty"] and self._key(i) in self._given]
        for item in wiped:
            self._try(item, FormReport(), overwrite=False)
        if wiped:
            self._pause(500)
        return bool(wiped)

    def _attach(self, item: dict[str, Any], documents: DocumentSource, report: FormReport) -> None:
        kind = document_kind(item["label"], item.get("hint", ""))
        if kind is None or kind in self.attached or not item["empty"]:
            return
        path = documents(kind, bool(item["required"]))
        if path is None:
            return
        try:
            self._control(item).set_input_files(str(path))
        except Exception as exc:
            self.say(f"  [note] couldn't attach your {kind.replace('_', ' ')} ({type(exc).__name__})")
            return
        self.attached.add(kind)
        report.attached.add(kind)
        report.filled.append(f"{kind.replace('_', ' ')}: {Path(path).name}")

    def _after_upload(self) -> None:
        """Give the site a moment to take the file in (and read it, if it does that)."""
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                self.page.wait_for_timeout(700)
                busy = self.frame.evaluate(
                    "() => /uploading\\.\\.\\.|analyzing resume|parsing (your )?resume/i.test((document.body.innerText || '').slice(0, 20000))"
                    " && ![...document.querySelectorAll('.resume-upload-success')].some(e => e.offsetWidth)"
                )
            except Exception:
                return
            if not busy:
                break
        self._pause(800)

    def _saved(self, label: str) -> list[dict[str, Any]]:
        return candidates(label, self.answers)

    def _learn(self, label: str, value: str) -> None:
        answer = {"match": [norm(label)[:160]], "value": value, "sensitive": bool(SENSITIVE_QUESTION.search(label))}
        self.learned.append(answer)
        self.answers.append(answer)

    def _ask(self, label: str, options: list[str] | None = None, many: bool = False) -> Any:
        if self.ask is None:
            return None
        return self.ask(label, options, many)

    def _work_out(self, waiting: list[tuple[str, str, str]], work_out: WorkOut, report: FormReport,
                  record: list[tuple[str, str, str]]) -> None:
        """Put the questions still empty to `work_out`, fill in what it settles, and note each in `record`."""
        fields = {self._key(i): i for i in self.scan()}
        questions: list[dict[str, Any]] = []
        asked: dict[int, tuple[str, str, str]] = {}
        for key in waiting:
            item = fields.get(key)
            if item is None or not self._needs_answer(item):
                continue
            # Only a box to type in can be an essay. A question with choices is never one,
            # however essay-like its wording ("...the circumstances described below?").
            if item["kind"] in ("text", "textarea") and essay_answers.is_essay(item["label"], item["kind"], True):
                continue  # written by the essay writer, or left for the student
            choices = item["options"] or (self._list_options(item) if item["kind"] == "combobox" else [])
            self.choices[item["label"]] = choices
            number = len(questions) + 1
            questions.append({
                "id": number, "question": item["label"], "choices": choices or None,
                "pick": "any" if item["kind"] == "checkboxes" else ("one" if choices else "text"),
            })
            asked[number] = key
        if not questions:
            return
        try:
            found = work_out(questions)
        except Exception as exc:  # never let this stop the form; the questions are asked instead
            self.say(f"  [note] couldn't work out answers from your profile ({type(exc).__name__})")
            return
        settled: dict[tuple[str, str, str], tuple[str, str]] = {}
        for number, (value, because) in found.items():
            key = asked.get(number)
            if key is None:
                continue
            text = "; ".join(str(v) for v in value) if isinstance(value, list) else str(value)
            # Kept for this run, so the same question on the next form needs no
            # working out. Not saved to the profile: the student didn't say it.
            self.answers.append({"match": [norm(fields[key]["label"])], "value": text, "exact": True})
            settled[key] = (text, because)
        for item in self.scan():
            key = self._key(item)
            if key in settled and self._needs_answer(item) and self._try(item, report, overwrite=False):
                record.append((item["label"], settled[key][0], settled[key][1]))

    def _list_options(self, item: dict[str, Any]) -> list[str]:
        """The choices in a list that has to be opened to see them; none for one that searches as you type."""
        try:
            control = self._control(item)
            control.scroll_into_view_if_needed()
            control.click()
            _, texts = self._open_options()
            self.page.keyboard.press("Escape")
        except Exception:
            return []
        return texts if 0 < len(texts) <= 150 else []

    def _answer(self, item: dict[str, Any], report: FormReport, overwrite: bool, ask: bool = False) -> bool:
        label, kind = item["label"], item["kind"]
        if not label or NEVER_FILL.search(label):
            return False  # a required one shows up as still empty, for the student
        if kind in ("text", "textarea"):
            return self._answer_text(item, report, overwrite, ask)
        if kind == "combobox":
            return self._answer_combobox(item, report, ask)
        if kind == "checkboxes":
            return self._answer_many(item, report, ask)
        return self._answer_choice(item, report, ask)

    def _answer_text(self, item: dict[str, Any], report: FormReport, overwrite: bool, ask: bool) -> bool:
        label = item["label"]
        saved = self._saved(label)
        if item["empty"]:
            value = str(saved[0]["value"]) if saved else None
            if (value is None and self._essays is not None
                    and essay_answers.is_essay(label, item["kind"], bool(item["required"]))):
                value = self._essays(label, int(item.get("max") or 0), item["kind"] == "text") or None
                if value:
                    report.written.append((label, value))
            if value is None and ask and item["required"]:
                given = self._ask(label)
                if given:
                    value = str(given)
                    self._learn(label, value)
            if not value:
                return False
        else:
            # Something is there already, usually read from the resume by the
            # site. A saved answer replaces it; otherwise it's left alone.
            value = str(saved[0]["value"]) if saved and overwrite else None
            if not value or _loose(value) == _loose(item.get("value", "")):
                return True
        self._control(item).fill(value)
        report.filled.append(f"{label[:60]}: {value[:60]}")
        return True

    def _choose(self, item: dict[str, Any], ask: bool, want_many: bool = False) -> list[int]:
        """Indexes of the options the student's answers mean."""
        label, options = item["label"], item["options"]
        for answer in self._saved(label):
            values = [v for v in re.split(r"\s*[;|]\s*", str(answer["value"])) if v] if want_many else [str(answer["value"])]
            picked = [i for i in (pick_option(options, v) for v in values) if i is not None]
            if picked:
                return picked
        if not (ask and item["required"]):
            return []
        given = self._ask(label, options, want_many)
        if not given:
            return []
        values = given if isinstance(given, list) else [str(given)]
        picked = [i for i in (pick_option(options, str(v)) for v in values) if i is not None]
        if picked:
            self._learn(label, "; ".join(options[i] for i in picked))
        return picked

    def _answer_choice(self, item: dict[str, Any], report: FormReport, ask: bool) -> bool:
        if not item["empty"]:
            return True
        picked = self._choose(item, ask)
        if not picked:
            return False
        index, kind = picked[0], item["kind"]
        choice = item["options"][index]
        if kind == "select":
            self._control(item).select_option(label=self._select_text(item, choice))
        elif kind == "checkbox":
            if index == 0:  # "No" leaves it unticked
                self._tick(self._option(item, 0))
        elif kind == "radio":
            self._tick(self._option(item, index))
        else:  # a pair of buttons
            self._option(item, index).click()
        report.filled.append(f"{item['label'][:60]}: {choice[:60]}")
        return True

    def _select_text(self, item: dict[str, Any], choice: str) -> str:
        """The option's text exactly as the page has it (the scan tidies spaces)."""
        for text in self._control(item).locator("option").all_inner_texts():
            if " ".join(text.split()) == choice:
                return text
        return choice

    def _answer_many(self, item: dict[str, Any], report: FormReport, ask: bool) -> bool:
        if not item["empty"]:
            return True
        picked = self._choose(item, ask, want_many=True)
        for index in picked:
            self._tick(self._option(item, index))
        if picked:
            report.filled.append(f"{item['label'][:60]}: " + "; ".join(item["options"][i][:40] for i in picked))
        return bool(picked)

    def _tick(self, control: Any) -> None:
        try:
            control.check(timeout=2000)
        except Exception:
            # A styled control hides the real one; its label takes the click.
            control.evaluate(
                "el => (el.closest('label') || (el.id && document.querySelector('label[for=\"' + CSS.escape(el.id) + '\"]')) || el).click()"
            )

    # A list that opens when clicked, often one that searches as you type.

    def _open_options(self, wait: int = 1500) -> tuple[Any, list[str]]:
        options = self.frame.locator('[role="option"]:visible')
        deadline = time.monotonic() + wait / 1000
        while time.monotonic() < deadline:
            try:
                if options.count() > 0:
                    break
            except Exception:
                pass
            self.page.wait_for_timeout(150)
        try:
            texts = [" ".join(t.split()) for t in options.all_inner_texts()[:400]]
        except Exception:
            texts = []
        if len(texts) == 1 and re.match(r"no (options|results|matches)", texts[0], re.IGNORECASE):
            texts = []
        return options, texts

    def _pick_from_list(self, control: Any, wanted: str) -> tuple[str | None, list[str]]:
        """Open the list and click what `wanted` means. Returns (chosen, options seen)."""
        control.click()
        options, texts = self._open_options()
        seen = texts
        index = pick_option(texts, wanted) if texts and len(texts) <= 60 else None
        if index is None:
            # Long or empty lists search as you type; a few leading words are enough.
            words = wanted.split()
            for typed in dict.fromkeys([wanted, " ".join(words[:3]), words[0] if words else ""]):
                if not typed:
                    continue
                try:
                    control.fill("")
                    control.press_sequentially(typed, delay=15)
                except Exception:
                    break  # not something that can be typed into
                self.page.wait_for_timeout(700)
                options, texts = self._open_options(2500)
                if texts:
                    seen = seen or texts
                    index = pick_option(texts, wanted)
                    if index is not None:
                        break
        if index is None:
            try:
                control.fill("")
            except Exception:
                pass
            self.page.keyboard.press("Escape")
            return None, seen
        options.nth(index).click()
        self.page.wait_for_timeout(250)
        return texts[index], seen

    def _answer_combobox(self, item: dict[str, Any], report: FormReport, ask: bool) -> bool:
        if not item["empty"]:
            return True
        label, control = item["label"], self._control(item)
        control.scroll_into_view_if_needed()
        seen: list[str] = []
        for answer in self._saved(label)[:4]:
            chosen, seen_now = self._pick_from_list(control, str(answer["value"]))
            seen = seen or seen_now
            if chosen is not None:
                report.filled.append(f"{label[:60]}: {chosen[:60]}")
                return True
        if not (ask and item["required"]):
            return False
        if not seen:
            control.click()
            _, seen = self._open_options()
            self.page.keyboard.press("Escape")
        given = self._ask(label, seen if 0 < len(seen) <= 40 else None)
        if not given:
            return False
        chosen, _ = self._pick_from_list(control, str(given))
        if chosen is None:
            return False
        self._learn(label, chosen)
        report.filled.append(f"{label[:60]}: {chosen[:60]}")
        return True

    # ------------------------------------------------------------- sending

    def _submit_button(self) -> Any:
        scopes = [self.frame.locator(self.root)] if self.root else []
        scopes.append(self.frame)
        for scope in scopes:
            for pattern in SUBMIT_PATTERNS:
                try:
                    buttons = scope.get_by_role("button", name=pattern)
                    for index in range(min(buttons.count(), 5)):
                        if buttons.nth(index).is_visible():
                            return buttons.nth(index)
                except Exception:
                    continue
        try:
            last = self.frame.locator('button[type="submit"]:visible, input[type="submit"]:visible')
            if last.count() > 0:
                return last.last
        except Exception:
            pass
        return None

    def _confirm_text(self) -> bool:
        frames = [self.page.main_frame] + ([self.frame] if self.frame is not self.page.main_frame else [])
        return any(CONFIRM_TEXT.search(self._text(f)) for f in frames)

    def _confirmed(self, url_before: str, said_before: bool) -> bool:
        url = self.page.url
        if url != url_before and CONFIRM_URL.search(urlparse(url).path):
            return True
        return self._confirm_text() and not said_before

    def _captcha_showing(self) -> bool:
        for frame in {self.page.main_frame, self.frame}:
            try:
                if frame.evaluate(CAPTCHA_JS):
                    return True
            except Exception:
                continue
        return False

    def _errors(self) -> list[str]:
        try:
            return [str(e) for e in self.frame.evaluate(ERRORS_JS)]
        except Exception:
            return []

    def submit(self) -> tuple[str, str]:
        """Press Submit and wait for the site to say it went through.

        Returns (status, note): applied, needs_manual (the site wanted
        something more and nobody gave it) or uncertain (sent, no confirmation
        seen).
        """
        self._repair()  # a last look: a site can empty an answer between filling and sending
        button = self._submit_button()
        if button is None:
            if self._confirm_text():  # the student pressed it themselves
                return "applied", "the employer's site shows it was sent"
            return "needs_manual", "couldn't find the form's Submit button"
        url_before, said_before = self.page.url, self._confirm_text()
        code_before = bool(CODE_TEXT.search(self._text(self.frame)))
        errors_before = set(self._errors())
        try:
            button.scroll_into_view_if_needed()
            button.click()
        except Exception as exc:
            return "needs_manual", f"pressing Submit failed ({type(exc).__name__})"

        clicked = time.monotonic()
        deadline = clicked + 25
        handed_over = retried = False
        refused = ""  # what the site said was wrong with the form, if it turned it down
        while True:
            try:
                self.page.wait_for_timeout(1000)
            except Exception:
                return "uncertain", "the browser tab closed after Submit was pressed"
            if self._confirmed(url_before, said_before):
                return "applied", "the employer's site confirmed it"
            wants = ""
            if self._captcha_showing():
                wants = "a security check"
            elif not code_before and CODE_TEXT.search(self._text(self.frame)):
                wants = "a code it emailed you"
            else:
                problems = [p for p in self._errors() if p not in errors_before] if time.monotonic() - clicked > 3 else []
                if problems:
                    refused = "; ".join(problems)[:300]
                    if not retried:
                        # The site turned the form down. If it had emptied answers already given,
                        # they're given again and the form is sent once more.
                        retried = True
                        again = self._submit_button() if self._repair() else None
                        if again is not None:
                            try:
                                again.click()
                                clicked = time.monotonic()
                                deadline = clicked + 25
                                refused = ""
                                continue
                            except Exception:
                                pass
                if time.monotonic() >= deadline:
                    if refused and (handed_over or not self.person_present):
                        return "needs_manual", f"the site turned the form down: {refused}"
                    if not refused and (handed_over or not self.person_present):
                        return "uncertain", "Submit was pressed but the site showed no confirmation; check your email"
                    wants = ("something fixed: " + refused) if refused else "something more before it confirms"
            if not wants:
                continue
            if handed_over:
                if time.monotonic() >= deadline:
                    return "needs_manual", f"the site wanted {wants} and didn't confirm in time"
                continue
            if not self.person_present:
                return "needs_manual", f"the site wanted {wants}"
            handed_over = True
            deadline = time.monotonic() + self.patience
            self.say(f"  The site wants {wants}.")
            self.say("  Finish it in the browser window (press its Submit button if it asks again);"
                     f" waiting up to {round(self.patience / 60)} minutes for the confirmation.")
            try:
                self.page.bring_to_front()
            except Exception:
                pass
            chime()
