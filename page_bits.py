"""Pieces shared by the ranked pages: the page links and the Applied button.

Served by list_server.py, "Applied" saves to data/applied_myself.json so the
posting stays hidden and is left out of later rankings. Opened straight from
the folder, it can only hide the row in that browser.
"""

from __future__ import annotations

from html import escape

CSS = """
  .nav { display:none; gap:14px; margin:0 0 12px; font-size:14px; }
  body.served .nav { display:flex; }
  .nav a.here { color:var(--fg); }
  .tools { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin:0 0 12px; color:var(--muted); font-size:14px; }
  .note { font-size:14px; color:var(--muted); }
  button.applied { font:inherit; font-size:13px; color:var(--fg); background:transparent; border:1px solid var(--line); border-radius:6px; padding:3px 9px; cursor:pointer; white-space:nowrap; }
  button.applied:hover { border-color:var(--muted); }
  button.linkish { background:none; border:none; padding:0; color:var(--accent); font:inherit; font-weight:600; cursor:pointer; }
  tr.done { display:none; }
  body.show-done tr.done { display:table-row; opacity:.5; }
"""


def nav(here: str) -> str:
    links = [("/", "Apply-yourself list"), ("/ranked", "All internships, ranked"), ("/elsewhere", "Found elsewhere")]
    items = "".join(
        f"<a href='{href}'{' class=here' if href == here else ''}>{escape(label)}</a>" for href, label in links
    )
    return f"<nav class='nav'>{items}</nav>"


def tools() -> str:
    return ("<div class='tools'><span id='done-count'></span>"
            "<button type='button' class='linkish' id='done-toggle' hidden>Show applied</button>"
            "<span class='note' id='done-note'></span></div>")


def button(job_id: str, title: str, employer: str, url: str, page: str) -> str:
    return (f"<button type='button' class='applied' data-id='{escape(job_id, quote=True)}' "
            f"data-title='{escape(title, quote=True)}' data-employer='{escape(employer, quote=True)}' "
            f"data-url='{escape(url, quote=True)}' data-page='{escape(page, quote=True)}'>Applied</button>")


SCRIPT = """
<script>
(function () {
  var SERVED = location.protocol.indexOf('http') === 0;
  if (SERVED) document.body.classList.add('served');
  var KEY = 'hsbot-applied-myself';
  var local = {};
  try { local = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (e) { local = {}; }
  var done = {};
  var buttons = Array.prototype.slice.call(document.querySelectorAll('button.applied'));
  var toggle = document.getElementById('done-toggle');
  var count = document.getElementById('done-count');
  var note = document.getElementById('done-note');

  function render() {
    var hidden = 0;
    buttons.forEach(function (b) {
      var row = b.closest('tr');
      var gone = !!done[b.getAttribute('data-id')];
      row.classList.toggle('done', gone);
      b.textContent = gone ? 'Undo' : 'Applied';
      if (gone) hidden++;
    });
    count.textContent = hidden ? hidden + ' marked applied' : '';
    toggle.hidden = hidden === 0;
    toggle.textContent = document.body.classList.contains('show-done') ? 'Hide applied' : 'Show applied';
  }
  function post(b, undo) {
    return fetch('/api/applied', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Hsbot': '1' },
      body: JSON.stringify({ id: b.getAttribute('data-id'), title: b.getAttribute('data-title'),
        employer: b.getAttribute('data-employer'), url: b.getAttribute('data-url'),
        page: b.getAttribute('data-page'), undo: undo })
    }).then(function (r) { if (!r.ok) throw new Error(r.status); });
  }
  buttons.forEach(function (b) {
    b.addEventListener('click', function () {
      var id = b.getAttribute('data-id');
      var undo = !!done[id];
      if (!SERVED) {
        if (undo) { delete local[id]; delete done[id]; } else { local[id] = 1; done[id] = 1; }
        try { localStorage.setItem(KEY, JSON.stringify(local)); } catch (e) {}
        render();
        return;
      }
      b.disabled = true;
      post(b, undo).then(function () {
        if (undo) { delete done[id]; } else { done[id] = 1; }
        note.textContent = '';
        render();
      }).catch(function () {
        note.textContent = 'The list closed after sitting idle. Open it again to mark postings.';
      }).then(function () { b.disabled = false; });
    });
  });
  toggle.addEventListener('click', function () { document.body.classList.toggle('show-done'); render(); });

  if (SERVED) {
    fetch('/api/applied', { method: 'GET', headers: { 'X-Hsbot': '1' } })
      .then(function (r) { return r.json(); })
      .then(function (ids) { ids.forEach(function (id) { done[id] = 1; }); render(); })
      .catch(render);
    setInterval(function () { fetch('/api/ping', { method: 'POST', headers: { 'X-Hsbot': '1' } }).catch(function () {}); }, 20000);
  } else {
    done = local;
    note.textContent = 'Opened from the folder, "Applied" only hides a posting in this browser. Open it with "Open apply-yourself list" to mark it for good.';
    render();
  }
})();
</script>
"""
