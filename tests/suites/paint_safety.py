"""A paint bug must never take the window down again -- and the one that did is gone.

2026-10-09, live: the footprint opened parked on pyqtgraph's empty-view default
(a unit square near zero), so the chart was not on screen. Center kept that
1-unit span, giving a 2-bar close-up deep enough to draw every cell number, and
the cell-number pass read `sc` -- a local of a different method. The NameError
escaped paint(); PySide turned it into an access violation in QtCore.pyd and
the window vanished. Same signature as 2026-09-08. Reproduced live, then
offline from the recorded session.

This pins: the cell-number pass runs clean at deep zoom; safe_paint contains a
raising painter and logs it once; an untouched footprint frames itself and
Center gives it a readable frame; and the Record button, whose update line sat
unreachable from 3.1.0 to 3.2.0, says Stop again while recording.
"""
import _safety  # noqa: F401 -- keep first: refuses to run against the real archive
import os
import sys
from datetime import datetime

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod, chart_items as ci, diagnostics  # noqa: E402

T0 = 1_770_000_000.0


def iso(ep):
    return datetime.fromtimestamp(ep).isoformat(timespec="microseconds")


def trade(px, qty, ep, flag):
    return ("trade", {"symbol": "ASII", "price": float(px), "qty": float(qty),
                      "value": float(px) * qty, "id": int(ep * 10) & 0xFFFFFF,
                      "flag": flag, "sec": int(ep), "ns": 0,
                      "trade_time": iso(ep), "recv_iso": iso(ep)})


evs = [trade(4790 + 5 * (i % 3), 1000 + 300 * (i % 4), T0 + i * 4, 1 + i % 2)
       for i in range(60)]                                  # 4 one-minute bars
S = QtCore.QSettings("orderflow-test", "paint_safety")
S.clear()
win = app_mod.MainWindow({"ASII": evs}, "time", 60, ["ASII"], live=False, settings=S)
for p in list(win.panels):
    if p.kind != "footprint":
        win.remove_panel(p)
win.resize(1200, 700)
win.show()
app.processEvents()
fp = win.panels[0]
win.refresh()
app.processEvents()
item = fp.fp_item
tick = item._tickval


def frames(n=8):
    for _ in range(n):
        app.processEvents()


# ---- 1. the cell-number pass runs clean where it used to raise -------------
img = QtGui.QImage(1200, 700, QtGui.QImage.Format.Format_ARGB32)
img.fill(0)
pt = QtGui.QPainter(img)
pt.setWorldTransform(QtGui.QTransform(700.0, 0, 0, -15.0, 50.0, 15.0 * 4810))
assert item._cells, "need cells to draw"
assert tick * 15.0 >= 10 and 0.94 * 700 >= 34, "the transform must reach the text pass"
try:
    ci.FootprintItem.paint.__wrapped__(item, pt)     # raw, without the safety net
finally:
    pt.end()
print("PASS: cell numbers draw at deep zoom (%d cells, %.0f px per cell)"
      % (len(item._cells), tick * 15.0))

# ---- 2. and through the real view, nothing is even caught -----------------
fp.p.setRange(xRange=(0.5, 2.5), yRange=(4780, 4810), padding=0)
frames()
assert not getattr(item, "_paint_error_logged", False), "the footprint paint raised"
print("PASS: the live crash's 2-bar close-up paints with no error")

# ---- 3. the safety net: a raising painter is contained and logged once ----
log = diagnostics.CRASH_LOG


def contained_count():
    try:
        return log.read_text(encoding="utf-8").count("Boom.paint -- contained")
    except OSError:
        return 0


before = contained_count()


class Boom(pg.GraphicsObject):
    def boundingRect(self):
        return QtCore.QRectF(1.0, 4790.0, 1.0, 10.0)

    @ci.safe_paint
    def paint(self, p, *args):
        p.setPen(QtGui.QPen(QtGui.QColor("red"), 9))      # state the net must undo
        raise RuntimeError("a bug in a custom painter")


boom = Boom()
fp.p.addItem(boom)
for _ in range(5):
    boom.update()
    frames(3)
assert contained_count() - before == 1, "logged %d times" % (contained_count() - before)
fp.p.removeItem(boom)
print("PASS: a raising painter is contained -- window alive, logged once over 5 repaints")

# ---- 4. an untouched footprint frames itself; a zoomed one is left alone ----
fp.p.setRange(xRange=(-0.5, 0.5), yRange=(-0.5, 0.5), padding=0)
frames()
assert win._view_untouched(fp)
win.refresh()
(x0, x1), (y0, y1) = fp.p.viewRange()
assert 12 - 1e-6 <= x1 - x0 <= win.FRAME_BARS + 1e-6, x1 - x0
assert abs((y1 - y0) - tick * win.FRAME_TICKS) < 1e-6, (y1 - y0, tick)
print("PASS: untouched footprint frames itself to %.0f bars x %.0f ticks"
      % (x1 - x0, (y1 - y0) / tick))

fp.p.setRange(xRange=(1.0, 4.0), yRange=(4785.0, 4795.0), padding=0)
frames()
zoomed = fp.p.viewRange()
win.refresh()
assert fp.p.viewRange() == zoomed, "a view the user set must never be reframed"
print("PASS: a deliberately zoomed view is left alone")

fp.p.setRange(xRange=(-0.5, 0.5), yRange=(-0.5, 0.5), padding=0)
frames()
win._center_latest()
frames()
(x0, x1), _y = fp.p.viewRange()
assert x1 - x0 >= 12 - 1e-6, "Center from untouched gave a %.1f-bar close-up" % (x1 - x0)
print("PASS: Center from an untouched view gives %.0f bars, not the 2-bar close-up"
      % (x1 - x0))

# ---- 5. the Record button follows the recorder again ---------------------
real = app_mod.of_capture.writer_status
try:
    app_mod.of_capture.writer_status = lambda: (True, {"symbols": ["ASII"], "owner": "recorder"})
    win._refresh_session()
    assert win.rec_btn.text().strip() == "■ Stop", win.rec_btn.text()
    app_mod.of_capture.writer_status = lambda: (False, {})
    win._refresh_session()
    assert win.rec_btn.text().strip() == "● Record", win.rec_btn.text()
finally:
    app_mod.of_capture.writer_status = real
print("PASS: Record button reads Stop while recording, Record when not")

S.clear()
print()
print("ALL PASS")
