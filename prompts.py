"""Asking the student something without stopping the run forever.

An application sometimes asks a question no saved answer covers. If nobody is
at the keyboard, waiting on input() would freeze the run for good, so these
give up after a while and the posting goes on the apply-yourself list instead.
"""

from __future__ import annotations

import os
import sys
import threading
import time

TIMED_OUT = object()  # nothing was typed in time


def ask(prompt: str, timeout: float | None = None) -> str | None:
    """Read one typed line.

    Returns the text, "" for a bare Enter, or None when the student gave no
    answer: no keyboard at all (EOF), Ctrl+C, or nothing typed within
    `timeout` seconds. A timeout of 0 or None waits as long as it takes.
    """
    if not timeout or timeout <= 0:
        return _plain(prompt)
    if os.name == "nt":
        if _console():
            return _windows_console(prompt, timeout)
        return _thread(prompt, timeout)
    return _select(prompt, timeout)


def _plain(prompt: str) -> str | None:
    try:
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None


def _console() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, ValueError):
        return False


def _windows_console(prompt: str, timeout: float) -> str | None:
    """Collect keystrokes so the wait can be given up on, unlike input()."""
    import msvcrt

    sys.stdout.write(prompt)
    sys.stdout.flush()
    typed: list[str] = []
    deadline = time.monotonic() + timeout
    while True:
        if msvcrt.kbhit():
            char = msvcrt.getwch()
            if char in ("\r", "\n"):
                sys.stdout.write("\n")
                sys.stdout.flush()
                return "".join(typed)
            if char == "\x03":  # Ctrl+C
                sys.stdout.write("\n")
                return None
            if char in ("\b", "\x7f"):
                if typed:
                    typed.pop()
                    sys.stdout.write("\b \b")
                    sys.stdout.flush()
                continue
            if char == "\x00" or char == "\xe0":  # arrow and function keys
                msvcrt.getwch()
                continue
            typed.append(char)
            sys.stdout.write(char)
            sys.stdout.flush()
            deadline = time.monotonic() + timeout  # typing keeps it open
            continue
        if time.monotonic() >= deadline:
            sys.stdout.write("\n")
            sys.stdout.flush()
            return None
        time.sleep(0.05)


def _select(prompt: str, timeout: float) -> str | None:
    import select

    sys.stdout.write(prompt)
    sys.stdout.flush()
    try:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
    except (OSError, ValueError):  # not something select can watch
        return _thread(prompt, timeout, echo_prompt=False)
    if not ready:
        sys.stdout.write("\n")
        return None
    line = sys.stdin.readline()
    if not line:  # end of input
        return None
    return line.rstrip("\n")


def _thread(prompt: str, timeout: float, echo_prompt: bool = True) -> str | None:
    """Last resort: read in the background and walk away after the timeout.

    The reader is left behind on a timeout, so this is only used where a
    keystroke can't be watched directly (a piped stdin on Windows).
    """
    if echo_prompt:
        sys.stdout.write(prompt)
        sys.stdout.flush()
    box: list[str | None] = []

    def read() -> None:
        try:
            line = sys.stdin.readline()
        except Exception:
            box.append(None)
            return
        box.append(None if line == "" else line.rstrip("\n"))  # "" means end of input

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    reader.join(timeout)
    if not box:
        sys.stdout.write("\n")
        sys.stdout.flush()
        return None
    return box[0]
