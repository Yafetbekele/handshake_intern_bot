"""Double-click front end for the internship assistant.

Started by "Start Internship Assistant.bat". It shows a small form for the
resume, major and run mode, remembers those choices for next time, installs
missing dependencies on first use, and then runs the normal command line flow
in the console window so confirmations still work.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Callable

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
SETTINGS_PATH = DATA / "launcher_settings.json"
SETUP_MARKER = DATA / ".setup_complete"

MODES: list[tuple[str, str]] = [
    ("search", "See my matches only (applies to nothing)"),
    ("dry_run", "Practice run (fills in forms, submits nothing)"),
    ("confirm", "Apply, asking me before each one"),
    ("auto", "Apply automatically"),
]
MODE_KEYS = {key for key, _ in MODES}


@dataclass
class LaunchSettings:
    resume: str = ""
    major: str = ""
    mode: str = "search"
    max_applications: int = 10
    cover_letter: str = ""
    transcript: str = ""
    locations: str = ""
    base_url: str = ""
    tailor_resume: bool = False


# ------------------------------------------------------------------ settings


def load_settings(path: Path = SETTINGS_PATH) -> LaunchSettings:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return LaunchSettings()
    if not isinstance(raw, dict):
        return LaunchSettings()
    known = {f.name for f in fields(LaunchSettings)}
    settings = LaunchSettings(**{k: v for k, v in raw.items() if k in known})
    if settings.mode not in MODE_KEYS:
        settings.mode = "search"
    try:
        settings.max_applications = int(settings.max_applications)
    except (TypeError, ValueError):
        settings.max_applications = 10
    return settings


def save_settings(settings: LaunchSettings, path: Path = SETTINGS_PATH) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    except OSError:
        pass  # remembering choices is a convenience, never a blocker


def validate(settings: LaunchSettings) -> list[str]:
    """Human-readable problems with the form, empty when it is good to go."""
    problems: list[str] = []
    if not settings.resume.strip():
        problems.append("Choose your resume file.")
    elif not Path(settings.resume).expanduser().is_file():
        problems.append(f"The resume file can't be found:\n{settings.resume}")
    elif Path(settings.resume).suffix.lower() not in {".pdf", ".docx", ".doc", ".txt"}:
        problems.append("The resume must be a PDF, Word or text file.")

    if not settings.major.strip():
        problems.append("Choose or type your major.")

    if settings.mode not in MODE_KEYS:
        problems.append("Choose what the assistant should do.")

    for label, value in (("cover letter", settings.cover_letter), ("transcript", settings.transcript)):
        if value.strip() and not Path(value).expanduser().is_file():
            problems.append(f"The {label} file can't be found:\n{value}")

    if settings.mode != "search":
        if not 1 <= int(settings.max_applications) <= 50:
            problems.append("Max applications must be between 1 and 50.")

    url = settings.base_url.strip()
    if url and not url.lower().startswith(("http://", "https://")):
        problems.append("The school Handshake address must start with https://")
    return problems


def build_args(settings: LaunchSettings) -> list[str]:
    """Translate the form into arguments for main.py."""
    command = "search" if settings.mode == "search" else "apply"
    args = [command, "--resume", settings.resume.strip(), "--major", settings.major.strip()]

    if settings.cover_letter.strip():
        args += ["--cover-letter", settings.cover_letter.strip()]
    if settings.transcript.strip():
        args += ["--transcript", settings.transcript.strip()]
    if settings.base_url.strip():
        args += ["--base-url", settings.base_url.strip().rstrip("/")]
    for location in settings.locations.split(","):
        if location.strip():
            args += ["--location", location.strip()]

    if settings.tailor_resume:
        args.append("--tailor-resume")

    if command == "apply":
        args += ["--max", str(int(settings.max_applications))]
        if settings.mode == "dry_run":
            args.append("--dry-run")
        elif settings.mode == "auto":
            args.append("--auto-submit")
    return args


def result_file(mode: str) -> Path:
    return DATA / ("matches.csv" if mode == "search" else "follow_up.csv")


# -------------------------------------------------------------- dependencies


def missing_dependencies() -> list[str]:
    missing = []
    for module, package in (("playwright", "playwright"), ("pdfplumber", "pdfplumber"), ("docx", "python-docx")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    return missing


def install_dependencies() -> bool:
    print("\nInstalling what the assistant needs. This takes a few minutes once.\n")
    steps = [
        [sys.executable, "-m", "pip", "install", "-r", str(HERE / "requirements.txt")],
        [sys.executable, "-m", "playwright", "install", "chromium"],
    ]
    for step in steps:
        if subprocess.call(step, cwd=str(HERE)) != 0:
            print("\nSetup failed. Check your internet connection and try again.")
            return False
    DATA.mkdir(parents=True, exist_ok=True)
    SETUP_MARKER.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    print("\nSetup complete.\n")
    return True


# ----------------------------------------------------------------------- GUI


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _message(kind: str, title: str, text: str) -> bool:
    """Show a messagebox without a stray empty window. Returns yes/no answers."""
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "yesno":
            return bool(messagebox.askyesno(title, text, parent=root))
        if kind == "error":
            messagebox.showerror(title, text, parent=root)
        else:
            messagebox.showinfo(title, text, parent=root)
        return True
    finally:
        root.destroy()


def ask_settings(initial: LaunchSettings, self_test: bool = False) -> LaunchSettings | None:
    """Show the form. Returns the chosen settings, or None if cancelled."""
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    import majors

    _enable_dpi_awareness()
    root = tk.Tk()
    root.title("Handshake Internship Assistant")
    root.resizable(False, False)

    result: dict[str, LaunchSettings | None] = {"value": None}

    resume_var = tk.StringVar(value=initial.resume)
    major_var = tk.StringVar(value=initial.major)
    mode_var = tk.StringVar(value=initial.mode)
    max_var = tk.StringVar(value=str(initial.max_applications))
    cover_var = tk.StringVar(value=initial.cover_letter)
    transcript_var = tk.StringVar(value=initial.transcript)
    locations_var = tk.StringVar(value=initial.locations)
    base_url_var = tk.StringVar(value=initial.base_url)

    frame = ttk.Frame(root, padding=18)
    frame.grid(sticky="nsew")
    frame.columnconfigure(1, weight=1)

    heading = ttk.Label(frame, text="Find and apply to summer internships", font=("Segoe UI", 13, "bold"))
    heading.grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))

    def browse(var: tk.StringVar, title: str) -> None:
        start = Path(var.get()).parent if var.get() else Path.home() / "Documents"
        chosen = filedialog.askopenfilename(
            parent=root,
            title=title,
            initialdir=str(start if start.exists() else Path.home()),
            filetypes=[("Documents", "*.pdf *.docx *.doc *.txt"), ("All files", "*.*")],
        )
        if chosen:
            var.set(str(Path(chosen)))

    def file_row(row: int, label: str, var: tk.StringVar, title: str) -> None:
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=4)
        ttk.Entry(frame, textvariable=var, width=46).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(frame, text="Browse...", command=lambda: browse(var, title)).grid(row=row, column=2)

    file_row(1, "Resume", resume_var, "Choose your resume")

    ttk.Label(frame, text="Major").grid(row=2, column=0, sticky="w", pady=4)
    major_box = ttk.Combobox(frame, textvariable=major_var, values=majors.list_majors(), width=44)
    major_box.grid(row=2, column=1, sticky="ew", padx=8)
    ttk.Label(frame, text="Pick one or type your own", foreground="#666").grid(row=3, column=1, sticky="w", padx=8)

    modes_box = ttk.LabelFrame(frame, text="What should it do?", padding=10)
    modes_box.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 6))
    for index, (key, label) in enumerate(MODES):
        ttk.Radiobutton(modes_box, text=label, value=key, variable=mode_var).grid(row=index, column=0, sticky="w", pady=1)

    max_row = ttk.Frame(modes_box)
    max_row.grid(row=len(MODES), column=0, sticky="w", pady=(8, 0))
    ttk.Label(max_row, text="Most applications this run:").grid(row=0, column=0, sticky="w")
    max_spin = ttk.Spinbox(max_row, from_=1, to=50, textvariable=max_var, width=5)
    max_spin.grid(row=0, column=1, padx=6)

    def sync_max_state(*_: object) -> None:
        max_spin.configure(state="disabled" if mode_var.get() == "search" else "normal")

    mode_var.trace_add("write", sync_max_state)
    sync_max_state()

    tailor_var = tk.BooleanVar(value=bool(initial.tailor_resume))
    ttk.Checkbutton(
        modes_box,
        text="Tailor my resume to each job (uses your Claude plan, only true facts from your profile)",
        variable=tailor_var,
    ).grid(row=len(MODES) + 1, column=0, sticky="w", pady=(8, 0))

    optional = ttk.LabelFrame(frame, text="Optional", padding=10)
    optional.grid(row=5, column=0, columnspan=3, sticky="ew", pady=6)
    optional.columnconfigure(1, weight=1)

    def optional_file(row: int, label: str, var: tk.StringVar, title: str) -> None:
        ttk.Label(optional, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(optional, textvariable=var, width=40).grid(row=row, column=1, sticky="ew", padx=8)
        ttk.Button(optional, text="Browse...", command=lambda: browse(var, title)).grid(row=row, column=2)

    optional_file(0, "Cover letter", cover_var, "Choose a cover letter")
    optional_file(1, "Transcript", transcript_var, "Choose a transcript")
    ttk.Label(optional, text="Locations").grid(row=2, column=0, sticky="w", pady=3)
    ttk.Entry(optional, textvariable=locations_var, width=40).grid(row=2, column=1, sticky="ew", padx=8)
    ttk.Label(optional, text="For example: Chicago, Texas. Remote jobs always count.", foreground="#666").grid(
        row=3, column=1, sticky="w", padx=8
    )
    ttk.Label(optional, text="School Handshake").grid(row=4, column=0, sticky="w", pady=3)
    ttk.Entry(optional, textvariable=base_url_var, width=40).grid(row=4, column=1, sticky="ew", padx=8)
    ttk.Label(optional, text="Only if your school has its own Handshake address", foreground="#666").grid(
        row=5, column=1, sticky="w", padx=8
    )

    def collect() -> LaunchSettings:
        try:
            max_apps = int(max_var.get())
        except ValueError:
            max_apps = 0
        return LaunchSettings(
            resume=resume_var.get().strip(),
            major=major_var.get().strip(),
            mode=mode_var.get(),
            max_applications=max_apps,
            cover_letter=cover_var.get().strip(),
            transcript=transcript_var.get().strip(),
            locations=locations_var.get().strip(),
            base_url=base_url_var.get().strip(),
            tailor_resume=bool(tailor_var.get()),
        )

    def start() -> None:
        settings = collect()
        problems = validate(settings)
        if problems:
            messagebox.showerror("Almost there", "\n\n".join(problems), parent=root)
            return
        if settings.mode == "auto":
            ok = messagebox.askyesno(
                "Apply automatically?",
                "Applications will be sent in your name without asking first.\n\n"
                "Handshake does not allow automated applying, and schools can "
                "suspend accounts for it. Try a practice run first if you haven't.\n\n"
                "Continue?",
                icon="warning",
                parent=root,
            )
            if not ok:
                return
        result["value"] = settings
        root.destroy()

    buttons = ttk.Frame(frame)
    buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(12, 0))
    ttk.Button(buttons, text="Cancel", command=root.destroy).grid(row=0, column=0, padx=6)
    start_button = ttk.Button(buttons, text="Start", command=start)
    start_button.grid(row=0, column=1)

    root.bind("<Return>", lambda _event: start())
    root.bind("<Escape>", lambda _event: root.destroy())

    # Bring the window in front of the console that launched it.
    root.update_idletasks()
    width, height = root.winfo_reqwidth(), root.winfo_reqheight()
    x = max((root.winfo_screenwidth() - width) // 2, 0)
    y = max((root.winfo_screenheight() - height) // 3, 0)
    root.geometry(f"+{x}+{y}")
    root.lift()
    root.attributes("-topmost", True)
    root.after(400, lambda: root.attributes("-topmost", False))
    root.focus_force()
    (major_box if initial.resume else start_button).focus_set()

    if self_test:
        root.after(1200, root.destroy)
    root.mainloop()
    return result["value"]


# ----------------------------------------------------------------------- run


def offer_claude_sign_in() -> None:
    """If Claude Code isn't signed in, offer to open its sign-in, then wait for it."""
    import tailor

    ready, _why = tailor.claude_ready()
    if ready:
        return
    exe = tailor.find_claude()
    if not exe:
        _message(
            "info",
            "Tailoring without Claude",
            "Claude Code wasn't found, so resumes will be tailored with rules from "
            "your profile instead. That still only uses true facts.",
        )
        return
    wants = _message(
        "yesno",
        "Sign in to Claude for tailoring",
        "Resume tailoring uses Claude through your existing Claude plan, at no extra cost.\n\n"
        "Claude Code needs to be signed in once. Open the sign-in now?\n\n"
        "A window will open and your browser will ask you to log in to Claude. "
        "If you choose No, resumes are tailored with rules from your profile instead.",
    )
    if not wants:
        return
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    try:
        subprocess.Popen([exe, "auth", "login"], creationflags=flags)
    except OSError as exc:
        print(f"Could not open Claude sign-in: {exc}")
        return
    _message(
        "info",
        "Finish signing in",
        "Finish signing in to Claude in your browser, then click OK here to continue.",
    )
    ready, why = tailor.claude_ready()
    print("Claude sign-in: " + ("done." if ready else f"not finished, using rules instead. {why}"))


def manual_list_page() -> Path:
    folder = os.environ.get("HSBOT_MANUAL_DIR") or str(HERE / "Internships to apply to yourself")
    return Path(folder) / "apply_yourself.html"


def _open(path: Path) -> None:
    try:
        os.startfile(str(path))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        print(f"Open this file: {path}")


def offer_results(mode: str, started_at: float) -> None:
    page = manual_list_page()
    if page.exists() and page.stat().st_mtime >= started_at:
        if _message(
            "yesno",
            "Done",
            "Open your list of internships to apply to yourself?\n\n"
            "These are good matches the assistant couldn't apply to for you. "
            "The list opens in your web browser with a link to each posting.",
        ):
            _open(page)
        return

    path = result_file(mode)
    if not path.exists() or path.stat().st_mtime < started_at:
        return
    label = "your ranked matches" if mode == "search" else "the jobs to finish by hand"
    if _message("yesno", "Done", f"Open {label}?\n\n{path.name} opens in Excel or your spreadsheet app."):
        _open(path)


def run(
    argv: list[str] | None = None,
    ask: Callable[[LaunchSettings], LaunchSettings | None] = ask_settings,
    after_run: Callable[[str, float], None] = offer_results,
    settings_path: Path = SETTINGS_PATH,
    check_setup: bool = True,
    extra_args: tuple[str, ...] = (),
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    if "--self-test" in argv:
        ask_settings(load_settings(settings_path), self_test=True)
        print("launcher self-test ok")
        return 0

    print("=" * 60)
    print(" Handshake Internship Assistant")
    print("=" * 60)

    missing = missing_dependencies() if check_setup else []
    if check_setup and (missing or not SETUP_MARKER.exists()):
        if missing:
            wants = _message(
                "yesno",
                "First-time setup",
                "The assistant needs to download a few things before its first run "
                "(about 300 MB, a few minutes).\n\nInstall them now?",
            )
            if not wants:
                print("Setup skipped. The assistant can't run without it.")
                return 1
        if not install_dependencies():
            _message("error", "Setup failed", "Setup didn't finish. See the black window for details.")
            return 1

    settings = ask(load_settings(settings_path))
    if settings is None:
        print("Cancelled.")
        return 0
    save_settings(settings, settings_path)

    if settings.tailor_resume and check_setup:
        offer_claude_sign_in()

    print("\nA browser window will open. If Handshake asks you to sign in, do it")
    print("there with your school login. You only need to do this once.\n")

    import main as cli

    started_at = time.time()
    try:
        code = int(cli.main(build_args(settings) + list(extra_args)))
    except SystemExit as exc:
        # main.py reports problems such as an unreadable resume this way.
        if isinstance(exc.code, str):
            print(f"\n{exc.code}")
            code = 1
        else:
            code = int(exc.code or 0)

    after_run(settings.mode, started_at)
    return code


if __name__ == "__main__":
    sys.exit(run())
