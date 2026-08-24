"""Gap ledger downstream: coverage maths, the integrity panel, CVD line breaks."""
import math
import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from datetime import datetime

from PySide6 import QtCore, QtWidgets

from orderflow import app as of_app, feed as of_feed, model as of_model

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _synth import events_for  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
S = QtCore.QSettings("orderflow-test", "gaps")
S.clear(); S.sync()

T0 = 1_772_000_000.0          # arbitrary session start


def iso(ep):
    return datetime.fromtimestamp(ep).isoformat()


def tr(ep, tid, px=4800.0):
    return ("trade", {"symbol": "ASII", "price": px, "qty": 100.0, "value": 0.0,
                      "id": tid, "flag": None, "sec": int(ep), "ns": 0,
                      "recv_iso": iso(ep), "trade_time": iso(ep)})


def gap(kind, a, b, attempts=1, detail=""):
    return ("gap", of_feed.gap_record("ASII", kind, a, b, attempts, detail))


# ---- 1. coverage over the traded span, minus in-span gaps -----------------
m = of_model.OrderflowModel(of_model.make_bars("time", 60))
m.on_event(tr(T0, 1))
m.on_event(tr(T0 + 600, 2))                       # 10-minute session
m.on_event(gap("disconnect", T0 + 100, T0 + 160))
frac, lost, span = m.coverage()
assert abs(span - 600) < 1e-6, span
assert abs(lost - 60) < 1e-6, lost
assert abs(frac - 0.9) < 1e-6, frac
print("PASS: 60s hole in a 600s session -> %.1f%% captured" % (100 * frac))

# ---- 2. a hole outside the traded span is not counted --------------------
m2 = of_model.OrderflowModel(of_model.make_bars("time", 60))
m2.on_event(tr(T0, 1)); m2.on_event(tr(T0 + 600, 2))
m2.on_event(gap("disconnect", T0 - 40000, T0 - 10))     # feed down overnight
frac2, lost2, _ = m2.coverage()
assert lost2 == 0.0 and frac2 == 1.0, (frac2, lost2)
print("PASS: overnight outage ignored -> 100% captured, no market calendar needed")

m3 = of_model.OrderflowModel(of_model.make_bars("time", 60))
m3.on_event(tr(T0, 1)); m3.on_event(tr(T0 + 600, 2))
m3.on_event(gap("disconnect", T0 - 300, T0 + 120))      # straddles the open
_f, lost3, _s = m3.coverage()
assert abs(lost3 - 120) < 1e-6, lost3
print("PASS: gap straddling the first trade clipped to %.0fs" % lost3)

# ---- 3. diag reports it ---------------------------------------------------
d = m.diag()
assert d["gaps"] == 1 and abs(d["coverage"] - 90.0) < 1e-6 and abs(d["gap_sec"] - 60) < 1e-6
print("PASS: diag() -> gaps=%d coverage=%.1f%% lost=%.0fs"
      % (d["gaps"], d["coverage"], d["gap_sec"]))

# ---- 4. the integrity panel renders it ------------------------------------
win = of_app.MainWindow({"ASII": events_for("ASII")}, "time", 60, ["ASII"],
                        live=False, settings=S)
win.show(); app.processEvents()

panel = next((p for p in win.panels if p.kind == "integrity"), None) \
    or win.add_panel("integrity", group="A")
panel.setVisible(True)
mm = panel.model()
assert mm is not None, "integrity panel should bind to the group's model"
base = mm.cvd_x[0] if mm.cvd_x else T0
mm.on_event(gap("disconnect", base + 5, base + 35, 2, "ConnectionClosed"))
mm.on_event(gap("token", base + 60, base + 90, 1, "server rejected the session token"))
panel.refresh(); app.processEvents()

assert panel.tbl.rowCount() == 2, panel.tbl.rowCount()
causes = {panel.tbl.item(r, 2).text() for r in range(2)}
assert causes == {"disconnect", "token"}, causes
assert "captured" in panel.summary.text(), panel.summary.text()
print("PASS: integrity panel lists %d gaps (%s) with a coverage summary"
      % (panel.tbl.rowCount(), ", ".join(sorted(causes))))

# ---- 5. CVD breaks the line on a recorded gap -----------------------------
cvd = next((p for p in win.panels if p.kind == "cvd"), None) or win.add_panel("cvd", group="A")
cvd.setVisible(True)
cvd.refresh(); app.processEvents()
_xs, ys = cvd.cvd_curve.getData()
nans = sum(1 for v in ys if v is None or (isinstance(v, float) and math.isnan(v)))
assert nans >= 1, "a recorded gap must break the CVD line (found %d NaNs)" % nans
print("PASS: CVD line broken at %d recorded gap(s)" % nans)

# ---- 6. the session chip dedupes across models ---------------------------
# The same hole reaches every model on that symbol, so a second model (different
# bar size) must not make the chip report four gaps.
chip = win._integrity_chip()
assert "2 gaps" in chip, chip
second = win.model_for("ASII", "time", 30)
assert second is not mm, "expected a distinct model for a different bar size"
for g in list(mm.gaps):
    second.on_event(("gap", g))
assert len(win.models) >= 2, list(win.models)
chip2 = win._integrity_chip()
assert "2 gaps" in chip2, "deduping failed across %d models: %s" % (len(win.models), chip2)
print("PASS: chip still reports 2 gaps across %d models (not multiplied)" % len(win.models))

S.clear()
print("\nALL PASS")
