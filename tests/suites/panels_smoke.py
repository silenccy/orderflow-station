"""Every panel kind refreshes without error; visible-range VAP and measure work."""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets
from orderflow import app as of_app, panels as of_panels

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _synth import events_for  # noqa: E402  (reuse the synthetic feed)

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
S = QtCore.QSettings("orderflow-test", "smoke")
S.clear(); S.sync()

EVENTS = {"ASII": events_for("ASII"), "BBCA": events_for("BBCA", px=9200)}
win = of_app.MainWindow(EVENTS, "time", 60, ["ASII", "BBCA"], live=False, settings=S)
win.show(); app.processEvents()

# ---- 1. every registered kind builds and refreshes -------------------------
for kind in sorted(of_panels.PANEL_TYPES):
    existing = next((p for p in win.panels if p.kind == kind), None)
    p = existing or win.add_panel(kind, group="A")
    p.setVisible(True)
    app.processEvents()
    p.refresh()          # must not raise
    print("  ok: %-10s %s" % (kind, type(p).__name__))
app.processEvents()
print("PASS: all %d panel kinds refresh" % len(of_panels.PANEL_TYPES))

# ---- 2. visible-range volume profile --------------------------------------
fp = next(p for p in win.panels if p.kind == "footprint")
vap = next(p for p in win.panels if p.kind == "vap")
m = fp.model()
ids = m.bar_ids()
assert len(ids) > 4, "need several bars"

vap.mode.setCurrentText("session")
sess_rows, note = vap._rows(m)
assert note == "session", note

vap.mode.setCurrentText("visible")   # now follow the linked footprint's zoom
fp.p.setXRange(0, 2, padding=0)      # show only the first ~3 bars
app.processEvents()
vis_rows, note = vap._rows(m)
assert note.startswith("visible"), note
assert sum(r[3] for r in vis_rows) < sum(r[3] for r in sess_rows), \
    "visible profile should hold less volume than the session"
assert sum(r[3] for r in vis_rows) > 0, "visible profile is empty"

fp.p.setXRange(-1, len(ids) + 1, padding=0)    # zoom back out to everything
app.processEvents()
all_rows, _ = vap._rows(m)
assert abs(sum(r[3] for r in all_rows) - sum(r[3] for r in sess_rows)) < 1e-6, \
    "fully zoomed out, visible profile must equal the session profile"
vap.refresh()                                   # and it draws
print("PASS: visible-range profile tracks the zoom and matches session when zoomed out")

# ---- 3. an unlinked VAP has no footprint to follow, falls back safely ------
lone = win.add_panel("vap", group=None)
lone.mode.setCurrentText("visible")
app.processEvents()
_rows, note = lone._rows(lone.model())
assert "no linked footprint" in note, note
lone.refresh()
print("PASS: unlinked Vol@Price falls back to the session profile")

# ---- 4. measure tool -------------------------------------------------------
win.measure_btn.setChecked(True)
assert win.measure_active()
lo, hi = ids[0], ids[3]
y0 = m.bar_meta[lo]["o"]
y1 = m.bar_meta[hi]["c"] + 20
fp._draw_measure(0.0, y0, 3.0, y1)
assert fp._meas_rect.isVisible() and fp._meas_txt.isVisible()
txt = fp._meas_txt.toPlainText()
assert "ticks" in txt and "bars" in txt and "vol" in txt, txt
print("PASS: measure ->", txt)
win.measure_btn.setChecked(False)
assert not fp._meas_rect.isVisible(), "unchecking measure should clear the box"
print("PASS: measure clears when the tool is switched off")

# ---- 5. signal log reads what the footprint already computed ---------------
sig = next(p for p in win.panels if p.kind == "signals")
sig.refresh()
print("PASS: signal log rows =", sig.tbl.rowCount())

# ---- 6. watchlist lists both symbols ---------------------------------------
wl = next(p for p in win.panels if p.kind == "watchlist")
wl.refresh()
syms = [wl.tbl.item(r, 0).text() for r in range(wl.tbl.rowCount())]
assert syms == ["ASII", "BBCA"], syms
wl._clicked(1, 0)                                # click BBCA
assert win.groups[wl.group]["symbol"] == "BBCA", "watchlist click did not retarget"
print("PASS: watchlist lists %s and click retargets the group" % syms)

print("\nALL PASS")
