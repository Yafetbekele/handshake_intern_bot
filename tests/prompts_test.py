"""Checks that an unanswered question gives up instead of freezing the run.

Run from the project root:
    python tests/prompts_test.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import prompts  # noqa: E402  (checks the import works)

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    flag = "ok " if condition else "BAD"
    print(f"  [{flag}] {name}" + (f"  ({detail})" if detail else ""))
    if not condition:
        failures.append(name)


READER = (
    "import sys; sys.path.insert(0, %r); import prompts;"
    "reply = prompts.ask('Your answer: ', float(sys.argv[1]));"
    "print('REPLY=' + ('<none>' if reply is None else repr(reply)))" % str(ROOT)
)


def run(typed: bytes | None, timeout: float) -> tuple[str, float]:
    started = time.monotonic()
    done = subprocess.run(
        [sys.executable, "-c", READER, str(timeout)],
        input=typed if typed is not None else b"",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout + 30,
    )
    return done.stdout.decode(errors="replace"), time.monotonic() - started


print("=" * 70)
print("1. NO ANSWER")
print("=" * 70)
# Nothing is ever typed: stdin stays open but silent, like a run left alone.
started = time.monotonic()
waiting = subprocess.Popen(
    [sys.executable, "-c", READER, "2"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
)
try:  # stdin stays open and silent, so this is a real wait, not end of input
    waiting.wait(timeout=40)
    out = waiting.stdout.read().decode(errors="replace")
except subprocess.TimeoutExpired:
    waiting.kill()
    out = "<never finished>"
finally:
    waiting.stdin.close()
took = time.monotonic() - started
check("gives up instead of waiting for ever", "REPLY=<none>" in out, out.strip()[-60:])
check("gives up close to the timeout", 1.5 < took < 25, f"{took:.1f}s")

print("=" * 70)
print("2. AN ANSWER STILL GETS THROUGH")
print("=" * 70)
out, _ = run(b"Because I love chips\n", 10)
check("typed answer is returned", "REPLY='Because I love chips'" in out, out.strip()[-60:])
out, _ = run(b"\n", 10)
check("a bare Enter means blank, not skipped", "REPLY=''" in out, out.strip()[-60:])

print("=" * 70)
print("3. WAITING FOR EVER IS STILL POSSIBLE")
print("=" * 70)
out, _ = run(b"typed late\n", 0)
check("a timeout of 0 waits for the answer", "REPLY='typed late'" in out, out.strip()[-60:])

print("=" * 70)
print("4. THE RUN SKIPS THE POSTING")
print("=" * 70)
import main  # noqa: E402

asked = main.make_question_asker(interactive=True, timeout=0.001)
check("no answer reads as unanswered", asked("Why us?") is None)
check("questions are skipped entirely when not interactive", main.make_question_asker(False) is None)
check("timeout comes from the settings", main.DEFAULT_CONFIG["answer_timeout"] == 60)

print()
print("=" * 70)
if failures:
    print(f"{len(failures)} CHECK(S) FAILED: {', '.join(failures)}")
    print("=" * 70)
    sys.exit(1)
print("ALL PROMPT TESTS PASSED")
print("=" * 70)
