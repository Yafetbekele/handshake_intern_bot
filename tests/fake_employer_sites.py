"""Stand-ins for employers' application forms, used to test employer_sites.py locally.

Their markup copies what the real forms looked like in October 2026 (looked at
in a browser, read only, nothing typed or sent):

* Greenhouse: form#application-form.application--form; text inputs with
  label[for] and aria-required; dropdowns that are not <select> but an
  input.select__input[role=combobox] whose choices appear as [role=option]
  once it's clicked (some only after typing); checkbox questions in a
  fieldset with a legend; file inputs hidden inside
  [role=group][aria-labelledby]; an aria-hidden "requiredInput" beside each
  dropdown; and a hidden phone-country list that also uses [role=option].
* Lever: form#application-form with one li.application-question per question,
  its text in .application-label (required ones carry a "✱"), radios and
  checkboxes wrapped in their own labels, real <select>s, a button#btn-submit,
  and a resume upload that fills in the name from the file.
* Ashby: no <form> at all, only a wrapper around each section of it (and the
  demographic section can hold more controls than the main one); each question is a
  .ashby-application-form-field-entry with a label, Yes/No questions are a
  pair of buttons with aria-pressed, radio groups are a fieldset, there's an
  extra "autofill from resume" file input, and sending swaps the page for a
  success message without changing the address.

Everything sent is kept in SUBMISSIONS for the tests to read.
"""

from __future__ import annotations

import email
import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

SUBMISSIONS: list[dict] = []

STYLE = """
<style>
  body { font: 15px system-ui, sans-serif; margin: 24px; }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); }
  .select__menu { border: 1px solid #999; max-height: 160px; overflow: auto; }
  .select__option { padding: 4px 8px; cursor: pointer; }
  .iti__hide, .hidden { display: none; }
  label, legend { display: block; margin-top: 10px; }
  .error { color: #b00; }
</style>
"""

# A small copy of react-select: click to open, type to filter, click a choice.
SELECT_JS = """
<script>
function makeSelect(id, choices, searchOnly) {
  var input = document.getElementById(id);
  var control = input.closest('.select__control');
  var shell = control.parentElement;
  var store = document.getElementById(id + '__value');
  function close() { var m = shell.querySelector('.select__menu'); if (m) m.remove(); }
  function open() {
    close();
    var typed = input.value.toLowerCase();
    var shownChoices = choices.filter(function (c) { return c.toLowerCase().indexOf(typed) !== -1; });
    if (searchOnly && typed.length < 2) shownChoices = [];
    var menu = document.createElement('div');
    menu.className = 'select__menu';
    var list = document.createElement('div');
    list.className = 'select__menu-list';
    list.setAttribute('role', 'listbox');
    if (!shownChoices.length) {
      var none = document.createElement('div');
      none.className = 'select__menu-notice';
      none.textContent = 'No options';
      list.appendChild(none);
    }
    shownChoices.forEach(function (c) {
      var option = document.createElement('div');
      option.className = 'select__option';
      option.setAttribute('role', 'option');
      option.textContent = c;
      option.addEventListener('mousedown', function (e) { e.preventDefault(); });
      option.addEventListener('click', function () {
        var old = control.querySelector('.select__single-value');
        if (old) old.remove();
        var placeholder = control.querySelector('.select__placeholder');
        if (placeholder) placeholder.remove();
        var chosen = document.createElement('div');
        chosen.className = 'select__single-value';
        chosen.textContent = c;
        control.querySelector('.select__value-container').prepend(chosen);
        store.value = c;
        input.value = '';
        close();
      });
      list.appendChild(option);
    });
    menu.appendChild(list);
    shell.appendChild(menu);
  }
  input.addEventListener('click', open);
  input.addEventListener('input', open);
  input.addEventListener('keydown', function (e) { if (e.key === 'Escape') { input.value = ''; close(); } });
}
</script>
"""


def gh_text(field_id: str, label: str, required: bool = True, kind: str = "text") -> str:
    star = '<span aria-hidden="true">*</span>' if required else ""
    return (
        f'<div class="text-input-wrapper"><label id="{field_id}-label" for="{field_id}" class="label">{label}{star}</label>'
        f'<input id="{field_id}" name="{field_id}" type="{kind}" class="input input__single-line" '
        f'aria-required="{"true" if required else "false"}"></div>'
    )


def gh_select(field_id: str, label: str, choices: list[str], required: bool = True, search_only: bool = False) -> str:
    star = '<span aria-hidden="true">*</span>' if required else ""
    shadow = '<input aria-hidden="true" tabindex="-1" class="remix-css-requiredInput" value="">' if required else ""
    return (
        f'<div class="select__container"><label id="{field_id}-label" for="{field_id}" class="label select__label">{label}{star}</label>'
        '<div class="select-shell"><div class="select__control"><div class="select__value-container">'
        '<div class="select__placeholder">Select...</div>'
        f'<div class="select__input-container"><input class="select__input" id="{field_id}" type="text" role="combobox" '
        f'aria-required="{"true" if required else "false"}" aria-labelledby="{field_id}-label" autocomplete="off"></div>'
        f'</div></div></div>{shadow}'
        f'<input type="hidden" id="{field_id}__value" name="{field_id}" data-required="{int(required)}" data-label="{html.escape(label, quote=True)}">'
        f'<script>makeSelect({json.dumps(field_id)}, {json.dumps(choices)}, {json.dumps(search_only)});</script></div>'
    )


def gh_file(field_id: str, label: str, required: bool) -> str:
    star = '<span class="required">*</span>' if required else ""
    return (
        f'<div role="group" aria-labelledby="upload-label-{field_id}" aria-required="{"true" if required else "false"}" class="file-upload">'
        f'<div id="upload-label-{field_id}" class="label upload-label">{label}{star}</div>'
        '<div class="file-upload__wrapper"><div class="secondary-button"><div>'
        '<button type="button" class="btn btn--pill">Attach</button>'
        f'<label class="visually-hidden" for="{field_id}">Attach</label>'
        f'<input id="{field_id}" name="{field_id}" class="visually-hidden" type="file" accept=".pdf,.doc,.docx"></div></div></div></div>'
    )


COUNTRIES = ["Canada", "Mexico", "United Kingdom", "United States", "United States Minor Outlying Islands"]
SCHOOLS = ["Springfield College", "University of Springfield", "University of Springfield - Shelbyville", "Shelbyville Tech"]


def greenhouse_page(action: str) -> str:
    checks = "".join(
        f'<div class="checkbox__wrapper"><div class="checkbox__input"><input required type="checkbox" '
        f'id="question_4[]_{n}" name="question_4[]" value="{n}"></div>'
        f'<label for="question_4[]_{n}">{text}</label></div>'
        for n, text in enumerate(["Seattle, Washington", "Austin, Texas", "Remote"])
    )
    return f"""<!doctype html><html><head><title>Job Application for Hardware Intern at Acme</title>{STYLE}{SELECT_JS}</head><body>
<h1>Hardware Intern</h1><button type="button" class="btn btn--pill" aria-label="Apply">Apply</button>
<p>Thank you for your interest in Acme. We build embedded systems.</p>
<form id="application-form" class="application--form" method="post" action="{action}" enctype="multipart/form-data" novalidate>
{gh_text("first_name", "First Name")}{gh_text("last_name", "Last Name")}{gh_text("email", "Email")}
{gh_select("country", "Country", COUNTRIES)}
<div class="iti__dropdown-content iti__hide"><input type="search" id="iti-0__search-input" role="combobox" aria-label="Search">
<ul role="listbox"><li role="option" class="iti__country">Afghanistan+93</li><li role="option" class="iti__country">Albania+355</li></ul></div>
{gh_text("phone", "Phone", kind="tel")}
{gh_file("resume", "Resume/CV", True)}{gh_file("cover_letter", "Cover Letter", False)}
{gh_select("school--0", "School", SCHOOLS, search_only=True)}
{gh_text("question_3", "LinkedIn Profile", required=False)}
{gh_select("question_1", "Are you legally authorized to work in the United States?", ["Yes", "No"])}
{gh_select("question_2", "Will you now or in the future require sponsorship for employment visa status?", ["Yes", "No"])}
<fieldset class="checkbox" id="question_4[]" aria-required="true"><legend class="label checkbox__description">Which locations would you consider? <span class="required">*</span></legend>{checks}</fieldset>
{gh_select("question_5", "How did you hear about this opportunity?", ["Company website", "Handshake", "LinkedIn", "Referral"])}
{gh_text("question_6", "Social Security Number", required=False)}
{gh_select("gender", "Gender", ["Male", "Female", "Decline To Self Identify"], required=False)}
<div id="errors"></div>
<button type="submit" class="btn btn--pill">Submit application</button>
</form>
<script>
document.getElementById('application-form').addEventListener('submit', function (event) {{
  var missing = [];
  this.querySelectorAll('input[aria-required="true"]').forEach(function (el) {{
    if (el.getAttribute('role') !== 'combobox' && !el.value.trim()) missing.push(document.getElementById(el.id + '-label').textContent);
  }});
  this.querySelectorAll('input[type="hidden"][data-required="1"]').forEach(function (el) {{ if (!el.value) missing.push(el.getAttribute('data-label')); }});
  if (!document.getElementById('resume').files.length) missing.push('Resume/CV');
  if (!this.querySelector('input[name="question_4[]"]:checked')) missing.push('Which locations would you consider?');
  if (missing.length) {{
    event.preventDefault();
    document.getElementById('errors').innerHTML = missing.map(function (m) {{ return '<div class="helper-text--error" role="alert">' + m + ' is required</div>'; }}).join('');
  }}
}});
</script></body></html>"""


def lever_question(label: str, inner: str, required: bool = False, wrap: bool = True) -> str:
    mark = '<span class="required">✱</span>' if required else ""
    if wrap:  # the standard questions: one label around the text and the field
        return (f'<li class="application-question"><label><div class="application-label">{label}{mark}</div>'
                f'<div class="application-field">{inner}</div></label></li>')
    return (f'<li class="application-question custom-question"><div><div class="application-label full-width">'
            f'<div class="text">{label}{mark}</div></div>'
            f'<div class="application-field full-width{" required-field" if required else ""}">{inner}</div></div></li>')


def lever_choices(kind: str, name: str, choices: list[str], required: bool = False) -> str:
    need = ' required="required"' if required and kind == "radio" else ""
    return '<ul data-qa="multiple-choice">' + "".join(
        f'<li><label><input type="{kind}" name="{name}" value="{html.escape(c, quote=True)}"{need}>'
        f'<span class="application-answer-alternative">{c}</span></label></li>' for c in choices
    ) + "</ul>"


def lever_page(action: str) -> str:
    return f"""<!doctype html><html><head><title>Acme - Firmware Intern</title>{STYLE}</head><body>
<h2>Firmware Intern</h2><h4>Submit your application</h4>
<form id="application-form" method="post" action="{action}" enctype="multipart/form-data" novalidate><ul>
<li class="application-question resume"><label><div class="application-label">Resume/CV <span class="required">✱</span></div>
<div class="application-field"><a href="#" class="postings-btn template-btn-utility">ATTACH RESUME/CV</a>
<input type="file" id="resume-upload-input" name="resume" class="application-file-input visually-hidden">
<span class="resume-upload-working hidden">Analyzing resume...</span><span class="resume-upload-success hidden">Success!</span></div></label></li>
{lever_question("Full name", '<input type="text" name="name" required>', True)}
{lever_question("Email", '<input type="email" name="email" required>', True)}
{lever_question("Phone", '<input type="text" name="phone">')}
{lever_question("Current location", '<input type="text" class="location-input" id="location-input" name="location"><input type="hidden" id="selected-location" name="selectedLocation">')}
{lever_question("Current company", '<input type="text" name="org">')}
{lever_question("LinkedIn URL", '<input type="text" name="urls[LinkedIn]">')}
{lever_question("Are you authorized to work in the United States?", lever_choices("radio", "cards[a][field0]", ["Yes", "No"], True), True, wrap=False)}
{lever_question("If yes, what type of sponsorship?", '<textarea class="card-field-input" name="cards[a][field2]"></textarea>', wrap=False)}
{lever_question("Which of the following best describes you?", lever_choices("radio", "cards[b][field0]", ["A United States Citizen", "A lawful permanent resident of the United States (i.e. Greencard holder)", "Other"], True), True, wrap=False)}
{lever_question("Are you able to work onsite for 12 weeks?", '<select name="cards[c][field0]" required><option value="">Please select an option</option><option>Yes</option><option>No</option></select>', True, wrap=False)}
{lever_question("Which offices interest you?", lever_choices("checkbox", "cards[c][field1]", ["San Diego, California", "Dallas, Texas", "Washington, DC"]), wrap=False)}
<li class="application-question custom-question"><div><div class="application-field full-width required-field">
<ul data-qa="checkboxes"><li><label><input type="checkbox" name="consent[store]" value="true">
<span class="application-answer-alternative">I agree that Acme may keep my application on file<span class="required">✱</span></span></label></li></ul></div></div></li>
<div class="application-question"><label><div class="application-label">Gender</div><div class="application-field">
<select name="eeo[gender]"><option value="">Select ...</option><option>Male</option><option>Female</option><option>Decline to self-identify</option></select></div></label></div>
</ul>
<div class="section page-centered application-form"><h4>Additional information</h4>
<div class="application-additional"><textarea name="comments" id="additional-information" class="card-field-input"
 placeholder="Add a cover letter or anything else you want to share."></textarea></div></div>
<input type="hidden" name="h-captcha-response" id="hcaptchaResponseInput">
<div id="errors"></div>
<button type="button" id="btn-submit" class="postings-btn template-btn-submit">SUBMIT APPLICATION</button>
</form>
<script>
// Like Lever, the upload reads the resume and fills in what it thinks the name is.
document.getElementById('resume-upload-input').addEventListener('change', function () {{
  var working = document.querySelector('.resume-upload-working'), ok = document.querySelector('.resume-upload-success');
  working.classList.remove('hidden');
  setTimeout(function () {{
    document.querySelector('input[name="name"]').value = 'JANE  Q DOE RESUME';
    working.classList.add('hidden'); ok.classList.remove('hidden');
  }}, 900);
}});
document.getElementById('btn-submit').addEventListener('click', function () {{
  var form = document.getElementById('application-form'), missing = [];
  ['name', 'email'].forEach(function (n) {{ if (!form.querySelector('[name="' + n + '"]').value.trim()) missing.push(n); }});
  if (!form.querySelector('[name="resume"]').files.length) missing.push('resume');
  ['cards[a][field0]', 'cards[b][field0]'].forEach(function (n) {{ if (!form.querySelector('[name="' + n + '"]:checked')) missing.push(n); }});
  if (!form.querySelector('[name="cards[c][field0]"]').value) missing.push('onsite');
  if (!form.querySelector('[name="consent[store]"]').checked) missing.push('consent');
  if (missing.length) {{ document.getElementById('errors').innerHTML = '<div class="error-message">Please fill in: ' + missing.join(', ') + '</div>'; return; }}
  form.submit();
}});
</script></body></html>"""


def ashby_entry(field_id: str, label: str, inner: str, required: bool = False) -> str:
    need = " _required_f7cvd_91" if required else ""
    return (f'<div class="_fieldEntry_1e3gg_28 ashby-application-form-field-entry" data-field-path="{field_id}">'
            f'<label class="_heading_f7cvd_52{need} _label_1e3gg_42 ashby-application-form-question-title" for="{field_id}">{label}</label>'
            f'{inner}</div>')


def ashby_yes_no(field_id: str, label: str, required: bool = True) -> str:
    return ashby_entry(field_id, label, (
        '<div class="_container_1svni_28 _yesno_1e3gg_148 ashby-application-form-input-yesno">'
        '<button class="_container_pjyt6_1 _option_1svni_32" aria-pressed="false" data-option="yes">Yes</button>'
        '<button class="_container_pjyt6_1 _option_1svni_32" aria-pressed="false" data-option="no">No</button>'
        f'<input type="checkbox" class="_input_1svni_78 visually-hidden" name="{field_id}" tabindex="-1"></div>'), required)


def ashby_radio(field_id: str, label: str, choices: list[str], required: bool = True) -> str:
    options = "".join(
        f'<div class="_option_1258i_34"><span class="_container_132c8_28"><input type="radio" id="{field_id}-labeled-radio-{n}" name="{field_id}" value="{html.escape(c, quote=True)}"></span>'
        f'<label for="{field_id}-labeled-radio-{n}" class="_label_1258i_42">{c}</label></div>' for n, c in enumerate(choices))
    return (f'<fieldset class="_container_1258i_28 _fieldEntry_1e3gg_28 ashby-application-form-input-radio-group">'
            f'<label class="_heading_f7cvd_52{" _required_f7cvd_91" if required else ""} _label_1e3gg_42 ashby-application-form-question-title" for="{field_id}">{label}&nbsp;</label>{options}</fieldset>')


def ashby_page(action: str) -> str:
    def text(field_id: str, label: str, required: bool = False, kind: str = "text") -> str:
        need = ' required=""' if required else ""
        return ashby_entry(field_id, label, f'<div><input placeholder="Type here..." name="{field_id}"{need} id="{field_id}" type="{kind}"></div>', required)

    return f"""<!doctype html><html><head><title>Embedded Intern @ Acme</title>{STYLE}</head><body>
<h1>Embedded Intern</h1>
<div class="_container_j2da7_1" id="form">
<div class="_section_101oc_37 ashby-application-form-section-container">
<div><h3>Autofill from resume</h3><p>Upload your resume here to autofill key application fields.</p><input type="file" class="visually-hidden"><button>Upload file</button></div>
{text("_systemfield_name", "Name", True)}{text("f-legal", "Legal First and Last Name", True)}{text("_systemfield_email", "Email", True, "email")}
{ashby_entry("_systemfield_resume", "Resume", '<div role="presentation" class="ashby-application-form-input-file"><input accept=".pdf" id="_systemfield_resume" type="file" class="visually-hidden"><button>Upload File</button></div>', True)}
{text("f-linkedin", "LinkedIn Profile", True)}
{ashby_radio("f-degree", "Degree Level Currently Pursuing", ["Associate's", "Bachelor's", "Master's"])}
{ashby_yes_no("f-auth", "Are you legally authorized to work in the United States?")}
{ashby_yes_no("f-family", "Are you related to any current Acme employees?")}
{ashby_entry("f-why", "Why do you want to work at Acme?", '<textarea id="f-why" name="f-why" placeholder="Type here..."></textarea>')}
</div>
<div class="_section_101oc_37 ashby-application-form-section-container"><h2>Equal opportunity questions</h2>
{ashby_radio("eeoc_gender", "Gender", ["Male", "Female", "Decline to self-identify"], required=False)}
{ashby_radio("eeoc_race", "Race", ["Hispanic or Latino", "White", "Black or African American", "Asian", "Native Hawaiian or Other Pacific Islander", "American Indian or Alaska Native", "Two or More Races", "Decline to self-identify"], required=False)}
{ashby_radio("eeoc_veteran_status", "Veteran Status", ["I am a veteran", "I am not a veteran", "Decline to self-identify"], required=False)}
</div>
<div id="errors"></div>
<button class="_button_zyh3g_28 _primary_zyh3g_97" id="send">Submit Application</button>
</div>
<script>
document.querySelectorAll('.ashby-application-form-input-yesno button').forEach(function (b) {{
  b.addEventListener('click', function () {{
    b.parentElement.querySelectorAll('button').forEach(function (x) {{ x.setAttribute('aria-pressed', x === b ? 'true' : 'false'); x.classList.toggle('_active_1svni_57', x === b); }});
  }});
}});
document.getElementById('send').addEventListener('click', function () {{
  var data = {{}}, missing = [];
  document.querySelectorAll('#form input[type="text"], #form input[type="email"], #form textarea').forEach(function (el) {{
    data[el.name] = el.value;
    if (el.required && !el.value.trim()) missing.push(el.name);
  }});
  document.querySelectorAll('.ashby-application-form-input-yesno').forEach(function (box) {{
    var on = box.querySelector('button[aria-pressed="true"]');
    var name = box.querySelector('input').name;
    if (on) data[name] = on.getAttribute('data-option'); else missing.push(name);
  }});
  var degree = document.querySelector('input[name="f-degree"]:checked');
  if (degree) data['f-degree'] = degree.value; else missing.push('f-degree');
  var resume = document.getElementById('_systemfield_resume').files[0];
  if (resume) data['__resume'] = resume.name; else missing.push('resume');
  var autofill = document.querySelector('#form input[type="file"]:not([id])').files[0];
  if (autofill) data['__autofill'] = autofill.name;
  if (missing.length) {{ document.getElementById('errors').innerHTML = '<p class="_error_x" role="alert">Missing: ' + missing.join(', ') + '</p>'; return; }}
  fetch({json.dumps(action)}, {{ method: 'POST', headers: {{ 'Content-Type': 'application/json' }}, body: JSON.stringify(data) }})
    .then(function () {{ document.getElementById('form').innerHTML = '<h2>Success</h2><p>Your application was successfully submitted. We will be in touch.</p>'; }});
}});
</script></body></html>"""


def plain_page(action: str, required_essay: bool = False, captcha: bool = False, quiz: bool = False,
               wiping: bool = False, rejecting: str = "") -> str:
    """A small company's own form: nothing this tool has seen before."""
    essay = ('<div class="form-group"><label for="why">Why do you want to work here? *</label>'
             '<textarea id="why" name="why" required></textarea></div>') if required_essay else ""
    if quiz:
        # Questions no saved answer covers: some the profile settles, some it doesn't.
        def choice(name: str, label: str, options: list[str]) -> str:
            return (f'<div class="form-group"><label for="{name}">{label} *</label><select id="{name}" name="{name}" required>'
                    '<option value="">Select...</option>' + "".join(f"<option>{o}</option>" for o in options) + "</select></div>")

        def box(name: str, label: str) -> str:
            return f'<div class="form-group"><label for="{name}">{label} *</label><input id="{name}" name="{name}" required></div>'

        essay = (box("gy", "Expected graduation year") + choice("hear", "How did you hear about us?", ["Handshake", "LinkedIn", "Other"])
                 + choice("felony", "Have you ever been convicted of a felony?", ["Yes", "No"])
                 + box("mgpa", "Master's GPA") + box("majorgpa", "GPA in your major courses only, if you know it")
                 + choice("rel", "Are you related to a current employee?", ["Yes", "No"])
                 + choice("dis", "Do you have a disability?", ["Yes", "No", "I prefer not to say"])
                 + choice("exp", "Would you need an export license under the circumstances described below?", ["Yes", "No"])
                 + box("gy2", "Graduation year (four digits), as it will appear on your transcript"))
    check = ""
    if wiping:
        # Like a site that redraws its form after reading the resume: an answer given early is emptied later.
        check = """
<script>
document.getElementById('em').addEventListener('input', function () {
  setTimeout(function () { document.getElementById('fn').value = ''; }, 300);
});
</script>"""
    if captcha:
        # Sending brings up a security check instead; it clears by itself after a
        # few seconds, the way it would once the student passed it by hand.
        check = """
<script>
var form = document.querySelector('form[method="post"]');
form.addEventListener('submit', function (event) {
  if (window.passed) return;
  event.preventDefault();
  var frame = document.createElement('iframe');
  frame.src = '/blank?hcaptcha=1#frame=challenge';
  frame.style.cssText = 'width:320px;height:240px;border:1px solid #999';
  document.body.appendChild(frame);
  setTimeout(function () { window.passed = true; frame.remove(); form.submit(); }, 3500);
});
</script>"""
    if rejecting:
        # Like a site that empties an answer after reading the resume and then turns the form down for
        # it: once ("once") the missing answer can be given again; "always" it wants something nobody has.
        check = """
<script>
var form = document.querySelector('form[method="post"]');
var turnedDown = false;
form.addEventListener('submit', function (event) {
  if (turnedDown && '%s' === 'once') return;
  turnedDown = true;
  event.preventDefault();
  if ('%s' === 'once') document.getElementById('fn').value = '';
  var old = document.querySelector('.error-box'); if (old) old.remove();
  var box = document.createElement('div');
  box.className = 'error-box error';
  box.setAttribute('role', 'alert');
  box.textContent = 'Your form needs corrections. Missing entry for required field: ' + ('%s' === 'once' ? 'First name' : 'Reference code');
  form.prepend(box);
});
</script>""" % (rejecting, rejecting, rejecting)
    return f"""<!doctype html><html><head><title>Careers - Tiny Robotics</title>{STYLE}</head><body>
<header><form role="search"><input type="search" name="q" placeholder="Search jobs"></form></header>
<h1>Robotics Intern</h1>
<form method="post" action="{action}" enctype="multipart/form-data">
<div class="form-group"><label for="fn">First name *</label><input id="fn" name="first" required></div>
<div class="form-group"><label for="ln">Last name *</label><input id="ln" name="last" required></div>
<div class="form-group"><label for="em">Email address *</label><input id="em" name="email" type="email" required></div>
<div class="form-group"><label for="cv">Resume *</label><input id="cv" name="cv" type="file" required></div>
{essay}
<button type="submit">Send application</button>
</form>{check}</body></html>"""


def parse_multipart(content_type: str, body: bytes) -> tuple[dict[str, list[str]], dict[str, str]]:
    message = email.message_from_bytes(b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body)
    fields: dict[str, list[str]] = {}
    files: dict[str, str] = {}
    for part in message.get_payload() if message.is_multipart() else []:
        name = part.get_param("name", header="content-disposition")
        filename = part.get_filename()
        if filename is not None:
            if filename:
                files[str(name)] = filename
        else:
            fields.setdefault(str(name), []).append((part.get_payload(decode=True) or b"").decode("utf-8", "replace"))
    return fields, files


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def _send(self, body: str, code: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        if path == "/gh/acme/jobs/1":
            self._send(greenhouse_page("/gh/acme/jobs/1"))
        elif path == "/gh/acme/jobs/1/confirmation":
            self._send("<html><body><h1>Thank you for applying.</h1><p>Your application has been received.</p></body></html>")
        elif path.startswith("/lever/acme/") and path.endswith("/apply"):
            self._send(lever_page(path))
        elif path.startswith("/lever/acme/") and path.endswith("/thanks"):
            self._send("<html><body><h3>Application submitted!</h3></body></html>")
        elif path.startswith("/lever/acme/"):  # the posting itself, whose form is one click away
            self._send(f"<html><body><h2>Firmware Intern</h2><p>We make firmware.</p><a class='postings-btn' href='{path}/apply'>Apply for this job</a></body></html>")
        elif path.startswith("/ashby/acme/") and path.endswith("/application"):
            self._send(ashby_page("/ashby/submit"))
        elif path == "/plain/apply":
            self._send(plain_page("/plain/apply"))
        elif path == "/essay/apply":
            self._send(plain_page("/essay/apply", required_essay=True))
        elif path == "/quiz/apply":
            self._send(plain_page("/quiz/apply", quiz=True))
        elif path == "/wiping/apply":
            self._send(plain_page("/wiping/apply", wiping=True))
        elif path == "/rejecting/apply":
            self._send(plain_page("/rejecting/apply", rejecting="once"))
        elif path == "/refusing/apply":
            self._send(plain_page("/refusing/apply", rejecting="always"))
        elif path == "/guarded/apply":
            self._send(plain_page("/guarded/apply", captcha=True))
        elif path in ("/plain/done", "/essay/done", "/guarded/done", "/quiz/done", "/wiping/done", "/rejecting/done",
                      "/refusing/done"):
            self._send("<html><body><p>Thanks for applying! We received your application.</p></body></html>")
        elif path == "/embedded":
            self._send("<html><body><h1>Careers at Acme</h1><iframe src='/gh/acme/jobs/1' style='width:900px;height:1400px'></iframe></body></html>")
        elif path == "/closed":
            self._send("<html><body><h1>Sorry, this job is no longer available.</h1></body></html>")
        elif path == "/signin":
            self._send("<html><body><h1>Sign in to apply</h1><form><input type='email' name='user'><input type='password' name='pass'><button>Sign in</button></form></body></html>")
        elif path == "/careers":
            # A big company's careers page: filters folded away and a job-alert box, no application.
            self._send("<html><body><h1>Careers at BigCo</h1><form class='filters'><div style='display:none'>"
                       "<label><input type='checkbox' name='team'>Hardware</label><label><input type='checkbox' name='team'>Software</label>"
                       "<label><input type='checkbox' name='remote'>Remote</label><label><input type='checkbox' name='email_alerts'>Alerts</label>"
                       "</div></form><form class='alerts'><label>Email me new jobs <input type='email' name='alert'></label>"
                       "<label>Keyword <input name='kw'></label><label>Location <input name='where'></label></form></body></html>")
        elif path == "/portal/job":
            self._send("<html><body><h1>Systems Intern</h1><a href='/portal/login'>Apply now</a></body></html>")
        elif path == "/portal/login":  # asks for an email first; the password comes on the next screen
            self._send("<html><body><h1>Sign in or create an account</h1><input type='email' name='user'><button>Next</button></body></html>")
        elif path == "/blank":
            self._send("<html><body>Security check</body></html>")
        else:
            self._send("<html><body>Not found</body></html>", 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path.rstrip("/")
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        content_type = self.headers.get("Content-Type", "")
        if content_type.startswith("application/json"):
            data = json.loads(body or b"{}")
            SUBMISSIONS.append({"path": path, "fields": {k: [str(v)] for k, v in data.items() if not k.startswith("__")},
                                "files": {k.strip("_"): v for k, v in data.items() if k.startswith("__")}})
            self._send("{}")
            return
        fields, files = parse_multipart(content_type, body)
        SUBMISSIONS.append({"path": path, "fields": fields, "files": files})
        if path == "/gh/acme/jobs/1":
            target = "/gh/acme/jobs/1/confirmation"
        elif path.startswith("/lever/acme/"):
            target = path[: -len("/apply")] + "/thanks"
        else:
            target = path.rsplit("/", 1)[0] + "/done"
        self.send_response(303)
        self.send_header("Location", target)
        self.send_header("Content-Length", "0")
        self.end_headers()


def serve(port: int = 8766) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


if __name__ == "__main__":
    serve()[1].join()
