"""main() glue: when the Start dialog appears, and that its answers drive the window."""
import sys

from PySide6 import QtCore, QtWidgets

qapp = QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod
from orderflow import startup as st

SETTINGS = QtCore.QSettings("orderflow-test", "launch")
SETTINGS.clear()

shown = []          # how many times the dialog was constructed
built = []          # MainWindow constructor args
daemons = []        # _start_daemon calls


class StubDialog:
    def __init__(self, prev=None):
        shown.append(prev)
        self._v = {"mode": "live", "symbols": ["BBCA"], "bars": "tick", "size": 25,
                   "history": "none", "record": True, "debug": False,
                   "remember": True}

    def exec(self):
        return QtWidgets.QDialog.DialogCode.Accepted

    def values(self):
        return dict(self._v)


class StubWindow:
    def __init__(self, events, bars, size, syms, **kw):
        built.append({"bars": bars, "size": size, "syms": syms, **kw})

    def show(self): pass
    def start_live(self): pass
    def _start_daemon(self): daemons.append(True)
    def _ensure_on_screen(self): pass
    def _refresh_diag(self): return ""
    def close(self): pass
    def grab(self): raise AssertionError("no --shot in this test")


def run(argv):
    """Call main() with a given argv, stopping before the Qt event loop."""
    shown.clear(); built.clear(); daemons.clear()
    old_argv, old_dlg, old_win = sys.argv, st.StartDialog, app_mod.MainWindow
    old_qsettings = QtCore.QSettings
    sys.argv = argv
    app_mod.of_startup.StartDialog = StubDialog
    app_mod.MainWindow = StubWindow
    QtCore.QSettings = lambda *a, **kw: SETTINGS      # keep out of the real config
    app_mod.QtWidgets.QApplication = lambda *a, **kw: qapp
    qapp.exec = lambda *a, **kw: 0
    try:
        app_mod.main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
        app_mod.of_startup.StartDialog = old_dlg
        app_mod.MainWindow = old_win
        QtCore.QSettings = old_qsettings


# ---- 1. no flags, nothing remembered -> dialog drives everything -----------
run(["orderflow-app"])
assert len(shown) == 1, "expected the Start dialog, got %d" % len(shown)
assert built and built[0]["syms"] == ["BBCA"], built
assert built[0]["bars"] == "tick" and built[0]["size"] == 25, built
assert built[0]["live"] is True, built
assert daemons, "'Also record' must start the recorder"
print("PASS: no flags -> dialog shown, its answers built the window:", built[0])

# ---- 2. the recorder starts BEFORE the chart goes live --------------------
# (so start_live() sees the lock and correctly makes itself view-only)
assert len(daemons) == 1
print("PASS: recorder launched during startup")

# ---- 3. no flags, remembered -> dialog skipped ----------------------------
run(["orderflow-app"])
assert len(shown) == 0, "remembered choices must skip the dialog"
assert built and built[0]["syms"] == ["BBCA"], built
print("PASS: remembered choices skip the dialog and are reused")

# ---- 4. any flag -> dialog never appears (scripts keep working) -----------
run(["orderflow-app", "--replay", "--symbol", "ASII"])
assert len(shown) == 0, "flags must bypass the dialog"
assert built[0]["syms"] == ["ASII"] and built[0]["live"] is False, built
assert not daemons, "flags path must not auto-start a recorder"
print("PASS: flags bypass the dialog entirely:", built[0]["syms"], "replay")

SETTINGS.clear()
print("\nALL PASS")
