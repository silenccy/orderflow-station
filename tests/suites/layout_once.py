"""Startup must arrange the docks at most once.

Qt corrupts its dock layout when already-placed docks are rearranged. On a real
(non-offscreen) platform, startup used to run _default_layout THREE times --
once from _default_panels, once when restoreState failed, once from the
all-hidden safety net -- and aborted roughly nine launches in ten, the fault
landing inside whichever call did the moving.

The offscreen platform never reproduces the crash, so no other suite here can
see it. This one asserts the invariant that prevents it instead.
"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets
from orderflow import app as of_app

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)


def startup(settings_key, seed=None):
    """Construct a window, counting arrangement passes."""
    S = QtCore.QSettings("orderflow-test", settings_key)
    S.clear(); S.sync()
    if seed:
        for k, v in seed.items():
            S.setValue(k, v)
    calls = []
    orig = of_app.MainWindow._default_layout
    of_app.MainWindow._default_layout = lambda self: (calls.append(1), orig(self))[1]
    try:
        win = of_app.MainWindow({}, "time", 60, ["ASII"], live=False, settings=S)
        app.processEvents()
    finally:
        of_app.MainWindow._default_layout = orig
    return win, len(calls), S


# ---- 1. fresh profile: exactly one arrangement ---------------------------
win, n, S = startup("layout_fresh")
assert n <= 1, "startup arranged the docks %d times; >1 corrupts Qt's dock layout" % n
print("PASS: fresh startup arranged the docks %d time(s)" % n)

NO = QtCore.Qt.DockWidgetArea.NoDockWidgetArea
unplaced = [p.kind for p in win.panels if win.dockWidgetArea(p) == NO and not p.isFloating()]
assert win.panels, "no panels created"
assert not unplaced, "panels left unplaced: %s" % unplaced
print("PASS: all %d panels ended up docked" % len(win.panels))

# ---- 2. a saved layout must still be honoured ----------------------------
# (regression guard: an earlier fix created panels undocked, which silently made
# restoreState a no-op and left every panel floating as a top-level window)
state = win.saveState()
roster = win.settings.value("panels/roster")
S.clear()

win2, n2, S2 = startup("layout_saved", seed={"winstate": state, "panels/roster": roster})
assert n2 <= 1, "restore path arranged the docks %d times" % n2
unplaced2 = [p.kind for p in win2.panels if win2.dockWidgetArea(p) == NO and not p.isFloating()]
assert not unplaced2, ("restoring a saved layout left panels floating: %s" % unplaced2)
print("PASS: saved layout restored, %d panels docked, %d arrangement pass(es)"
      % (len(win2.panels), n2))

S2.clear()
print("\nALL PASS")
