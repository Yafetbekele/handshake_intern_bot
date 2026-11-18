"""Checks for the limit on applications per company. Uses made-up records in a
temporary folder, never the real ones.

Run from the project root:
    python tests/company_cap_test.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import company_cap  # noqa: E402

failures = 0


def check(what: str, ok: bool, detail: object = "") -> None:
    global failures
    print(f"  [{'ok ' if ok else 'FAIL'}] {what}" + ("" if ok else f"   {detail}"))
    failures += 0 if ok else 1


work = Path(tempfile.mkdtemp(prefix="hsbot_cap_"))
(work / "applied.json").write_text(json.dumps({
    "1": {"status": "applied", "employer": "Acme Robotics, Inc."},
    "2": {"status": "skipped_external", "employer": "Acme Robotics"},  # not sent
    "3": {"status": "applied", "employer": "Volt"},
}), encoding="utf-8")
(work / "employer_applications.json").write_text(json.dumps({
    "gh-acme-4": {"status": "uncertain", "employer": "Acme Robotics"},
    "gh-acme-5": {"status": "needs_manual", "note": "still empty: GPA", "employer": "Acme Robotics"},  # not sent
    "gh-quad-6": {"status": "needs_manual", "note": "the site wanted a code it emails you", "employer": "quadric.io"},
    "gh-quad-7": {"status": "applied", "employer": "Quadric"},
}), encoding="utf-8")
(work / "applied_myself.json").write_text(json.dumps({
    "gh-acme-4": {"employer": "Acme Robotics"},  # the same posting, counted once
    "gh-acme-8": {"employer": "ACME ROBOTICS LLC"},
}), encoding="utf-8")

for long, short in (("Anduril Industries", "Anduril"), ("MACOM Technology Solutions", "MACOM"),
                    ("Qorvo, now part of Skyworks", "Qorvo"), ("Southwest Research Institute (SwRI)",
                    "Southwest Research Institute"), ("Persona.Ai", "Persona AI"), ("Astera Labs", "Astera")):
    check(f"'{long}' is '{short}'", company_cap.key(long) == company_cap.key(short), (company_cap.key(long), company_cap.key(short)))
for one, other in (("General Motors", "General Dynamics"), ("Samsung Research", "Samsung Semiconductor"),
                   ("Scout AI", "Scout Motors")):
    check(f"'{one}' isn't '{other}'", company_cap.key(one) != company_cap.key(other))

cap = company_cap.CompanyCap(work, 3)
check("sends are counted across Handshake, employers' sites and the student's own, each posting once",
      cap.count("Acme Robotics") == 3, cap.counts)
check("ones never sent don't count", cap.count("Volt") == 1, cap.counts)
check("a form waiting on an emailed code counts, and quadric.io is Quadric", cap.count("Quadric") == 2, cap.counts)
check("a company with 3 is full", cap.full("Acme Robotics Inc"))
check("one with fewer isn't", not cap.full("Quadric") and not cap.full("Volt"))
check("a company it has never seen isn't", not cap.full("Brand New Co"))
check("nor a posting with no employer named", not cap.full(""))
cap.add("Quadric", "gh-quad-9")
check("a send this run counts", cap.full("quadric.io"))
cap.add("Volt", "3")
check("but the same posting again doesn't", cap.count("Volt") == 1)
check("0 turns the limit off", not company_cap.CompanyCap(work, 0).full("Acme Robotics"))
check("the settings set it", company_cap.CompanyCap.from_config({"most_applications_per_company": 2}, work).full("Quadric"))
check("and a broken setting falls back to 3", company_cap.CompanyCap.from_config(
    {"most_applications_per_company": "lots"}, work).most == company_cap.DEFAULT_MOST)

print("\n" + ("ALL COMPANY-LIMIT CHECKS PASSED" if not failures else f"{failures} CHECK(S) FAILED"))
sys.exit(1 if failures else 0)
