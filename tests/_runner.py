"""Shared plumbing for running a suite in its own process.

Every suite is executed as a SUBPROCESS rather than imported. That is not
laziness: the suites monkeypatch module globals (`feed._session`,
`feed.websockets.connect`, `app.FeedThread`) and most build a QApplication,
which is a process-wide singleton. Run them in one interpreter and state leaks
between them, so a failure would depend on collection order rather than on the
code under test. One process per suite is what makes a green run mean something.

Each also gets a throwaway ORDERFLOW_DATA, so a test can never read or write
your real capture archive.
"""
import os
import subprocess
import sys
from pathlib import Path

SUITES_DIR = Path(__file__).resolve().parent / "suites"
TIMEOUT_SEC = 300


def suite_names():
    return sorted(p.stem for p in SUITES_DIR.glob("*.py")
                  if not p.stem.startswith("_"))


def suite_env(data_dir):
    env = dict(os.environ)
    env["ORDERFLOW_DATA"] = str(data_dir)          # never touch the real archive
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # The offscreen plugin ships no font backend on Windows and loads zero
    # families without this, which turns every label into a tofu box.
    if sys.platform == "win32" and "QT_QPA_FONTDIR" not in env:
        fonts = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "Fonts"
        if fonts.is_dir():
            env["QT_QPA_FONTDIR"] = str(fonts)
    return env


def run_suite(name, data_dir):
    """(returncode, combined_output)."""
    proc = subprocess.run(
        [sys.executable, str(SUITES_DIR / ("%s.py" % name))],
        capture_output=True, text=True, env=suite_env(data_dir),
        timeout=TIMEOUT_SEC)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
