"""
diagnostics.py — make failures visible when there is nowhere to print them.

The launcher runs the GUI under `pythonw.exe`, where `sys.stdout` and
`sys.stderr` are **None**. Without the handlers installed here, any exception —
at startup, inside a Qt slot, or on a feed thread — vanishes and the process
dies with no window and no message. Every possible bug collapses into the same
symptom: "nothing happens".

So: no Qt at import time. This module has to be importable and installable
*before* PySide6 is imported, or a failure in the Qt import itself is invisible
too. Qt is pulled in lazily, only to show the message box.

    from . import diagnostics
    diagnostics.install()          # do this first, before importing PySide6
"""

import faulthandler
import io
import os
import platform
import sys
import threading
import traceback
from datetime import datetime

from .paths import CAPTURE_LOCK, DATA_DIR

CRASH_LOG = DATA_DIR / "crash.log"
LAUNCH_LOG = DATA_DIR / "launch.log"

_fault_fp = None          # kept open for the life of the process for faulthandler
_stream_fp = None         # stdout/stderr replacement when running without a console
_installed = False
_dialog_shown = False     # one popup per run; a crash loop must not spam
LOG_MAX_BYTES = 256_000

# A modal box needs someone to click it. Headless runs (--shot, tests, CI) have
# nobody, and exec() would block forever -- turning a crash into a hang. Set
# False to force logging only.
show_dialogs = True


def _write(text):
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(text)
    except OSError:
        pass


def record(exc_type, exc, tb, where="main"):
    """Append a traceback to data/crash.log. Never raises — a crash handler that
    crashes is worse than useless."""
    try:
        body = "".join(traceback.format_exception(exc_type, exc, tb))
    except Exception:
        body = "%s: %s\n" % (exc_type, exc)
    _write("\n%s\n[%s] unhandled exception in %s (pid %s)\n%s%s\n"
           % ("=" * 72, datetime.now().isoformat(timespec="seconds"), where,
              os.getpid(), "-" * 72 + "\n", body))
    return body


def _show_dialog(body):
    """Best-effort popup. Only if a QApplication is already up — constructing one
    from inside a crash handler is a good way to crash again — and only if there
    is a human to dismiss it."""
    global _dialog_shown
    if _dialog_shown or not show_dialogs:
        return
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return                      # nobody to click OK; exec() would hang
    try:
        from PySide6 import QtWidgets
    except Exception:
        return
    if QtWidgets.QApplication.instance() is None:
        return
    _dialog_shown = True
    try:
        lines = [ln for ln in body.strip().splitlines() if ln.strip()]
        tail = "\n".join(lines[-3:]) if lines else "(no detail)"
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        box.setWindowTitle("Orderflow Station - error")
        box.setText("Something went wrong.")
        box.setInformativeText("%s\n\nFull traceback:\n%s" % (tail, CRASH_LOG))
        box.setDetailedText(body)
        box.exec()
    except Exception:
        pass


def _excepthook(exc_type, exc, tb):
    body = record(exc_type, exc, tb, where="main thread")
    _show_dialog(body)
    if sys.stderr is not None:          # a console run should still print
        try:
            sys.stderr.write(body)
        except (OSError, ValueError):
            pass


def _thread_excepthook(args):
    if args.exc_type is SystemExit:
        return
    name = getattr(args.thread, "name", "?")
    body = record(args.exc_type, args.exc_value, args.exc_traceback,
                  where="thread %s" % name)
    if sys.stderr is not None:
        try:
            sys.stderr.write(body)
        except (OSError, ValueError):
            pass


def _adopt_streams():
    """pythonw.exe gives us sys.stdout/stderr = None, which is why a failed launch
    produced no window and no message. Give the process real streams pointed at
    data/launch.log, so Python's own traceback printing, Qt's warnings and any
    stray print() all land somewhere readable — whatever launched us."""
    global _stream_fp
    if sys.stdout is not None and sys.stderr is not None:
        return                      # a console run: leave the real streams alone
    try:
        if LAUNCH_LOG.exists() and LAUNCH_LOG.stat().st_size > LOG_MAX_BYTES:
            tail = LAUNCH_LOG.read_text(encoding="utf-8",
                                        errors="replace").splitlines()[-200:]
            LAUNCH_LOG.write_text("\n".join(tail) + "\n", encoding="utf-8")
        _stream_fp = open(LAUNCH_LOG, "a", encoding="utf-8", buffering=1,
                          errors="replace")
        _stream_fp.write("\n[%s] launch (pid %s) %s\n"
                         % (datetime.now().isoformat(timespec="seconds"),
                            os.getpid(), sys.executable))
    except OSError:
        return
    if sys.stdout is None:
        sys.stdout = _stream_fp
    if sys.stderr is None:
        sys.stderr = _stream_fp


def install():
    """Idempotent. Call before importing PySide6."""
    global _fault_fp, _installed
    if _installed:
        return
    _installed = True
    _adopt_streams()
    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook
    try:                     # interpreter-level crashes that never reach Python
        _fault_fp = open(CRASH_LOG, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_fp)
    except (OSError, ValueError, RuntimeError):
        _fault_fp = None


# ============================================================
#  --doctor
# ============================================================
def _tail(path, n=8):
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ["(none)"]
    return lines[-n:] or ["(empty)"]


def doctor():
    """Everything worth knowing when it will not start. Returns text; every probe
    is guarded so one broken thing still lets the rest report."""
    o = io.StringIO()

    def line(k, v):
        o.write("  %-22s %s\n" % (k, v))

    o.write("Orderflow Station - doctor\n\n[interpreter]\n")
    line("executable", sys.executable)
    line("python", sys.version.split()[0])
    line("platform", platform.platform())
    line("argv", sys.argv)
    line("stdout/stderr", "%s / %s" % (
        "None (pythonw)" if sys.stdout is None else "ok",
        "None (pythonw)" if sys.stderr is None else "ok"))

    o.write("\n[paths]\n")
    line("DATA_DIR", DATA_DIR)
    line("ORDERFLOW_DATA", os.environ.get("ORDERFLOW_DATA") or "(unset, using default)")
    try:
        files = sorted(p.name for p in DATA_DIR.iterdir())
    except OSError as e:
        files = ["(unreadable: %s)" % e]
    line("contents", ", ".join(files) or "(empty)")

    o.write("\n[session]\n")
    try:
        from . import capture as of_capture
        line("recorder", of_capture.describe_status())
        line("lock file", "present" if CAPTURE_LOCK.exists() else "absent")
        line("stop flag", of_capture.stop_requested())
    except Exception as e:
        line("recorder", "probe failed: %r" % e)
    try:
        from . import startup as of_startup
        state, text = of_startup.token_status()
        line("token", "%s (%s)" % (text, state))
        line("frame file", of_startup.newest_frame() or "(none)")
    except Exception as e:
        line("token", "probe failed: %r" % e)

    o.write("\n[qt]\n")
    line("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM") or "(unset, native)")
    try:
        from PySide6 import QtCore, QtWidgets
        line("PySide6", QtCore.__version__)
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        line("platform plugin", app.platformName())
        for i, sc in enumerate(app.screens()):
            line("screen %d" % i, "%s available=%s" % (sc.name(), sc.availableGeometry().getRect()))
        # does the saved geometry land on a screen that still exists?
        s = QtCore.QSettings("orderflow", "of_app")
        geo = s.value("geometry")
        line("saved geometry", "%d bytes" % len(geo) if geo is not None else "(none)")
        line("saved winstate", "%d bytes" % len(s.value("winstate"))
             if s.value("winstate") is not None else "(none)")
        line("startup/remember", repr(s.value("startup/remember")))
        line("startup/values", repr(s.value("startup/values"))[:160])
        if geo is not None:
            w = QtWidgets.QMainWindow()
            ok = w.restoreGeometry(geo)
            fg = w.frameGeometry()
            on_screen = any(sc.availableGeometry().intersects(fg) for sc in app.screens())
            line("geometry restores", "%s -> %s" % (ok, fg.getRect()))
            line("lands on a screen", on_screen)
            if not on_screen:
                o.write("  !! saved window position is off every connected screen;\n"
                        "     run with --reset-layout\n")
    except Exception as e:
        line("qt", "probe failed: %r" % e)

    o.write("\n[crash.log]\n")
    for ln in _tail(CRASH_LOG):
        o.write("  %s\n" % ln)
    o.write("\n[launch.log]\n")
    for ln in _tail(LAUNCH_LOG):
        o.write("  %s\n" % ln)
    return o.getvalue()
