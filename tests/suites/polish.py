"""Visual polish that has to keep working, not just look right once.

  * an icon at every size, and a taskbar identity call that can never stop startup
  * a window title that says what the window is doing
  * tick flash: DOM levels and new prints flash, fade, restore what was under
    them, stay quiet in replay and when turned off, and stop their own timer
  * the depth-bar painter draws background, then bar, then text -- a big-print
    highlight used to paint straight over the bar
  * footprint and regime axes read clock time like the heatmap and CVD
  * the tape speaks header_units like every other panel
  * the README preview no longer flattens the regime chart
"""
import os
import sys
from datetime import datetime

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtGui, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod, brand, panels as of_panels  # noqa: E402
from orderflow.chart_items import DepthBarDelegate  # noqa: E402
from orderflow.theme import BULL, BEAR  # noqa: E402

T0 = 1_770_000_000.0


class _Sig:
    def connect(self, *a, **kw): pass
    def disconnect(self, *a, **kw): pass


class StubFeed:                      # never open a real websocket in a test
    def __init__(self, symbol, sink, **kw):
        self.symbol, self.sink = symbol, sink
        self.batch, self.status = _Sig(), _Sig()

    def start(self): pass
    def stop(self): pass
    def wait(self, ms=0): return True


app_mod.FeedThread = StubFeed


def iso(ep):
    return datetime.fromtimestamp(ep).isoformat(timespec="microseconds")


def book(side, levels, ep):
    return ("book", "ASII", side, [(float(p), 3, float(v)) for p, v in levels], iso(ep))


def trade(px, qty, ep, side_flag=1):
    return ("trade", {"symbol": "ASII", "price": float(px), "qty": float(qty),
                      "value": float(px) * qty, "id": int(ep * 1000) & 0xFFFFFF,
                      "flag": side_flag, "sec": int(ep), "ns": 0,
                      "trade_time": iso(ep), "recv_iso": iso(ep)})


def window(name, live):
    s = QtCore.QSettings("orderflow-test", name)
    s.clear()
    w = app_mod.MainWindow({}, "time", 60, ["ASII"], live=live, persist=False, settings=s)
    w.show()
    app.processEvents()
    return w, s


def panel(win, kind):
    p = next((x for x in win.panels if x.kind == kind), None) or win.add_panel(kind, group="A")
    p.setVisible(True)
    app.processEvents()
    return p


# ============================================================
#  1. identity
# ============================================================
icon = brand.make_icon()
for px in brand.SIZES:
    pm = icon.pixmap(px, px)
    assert not pm.isNull() and pm.width() == px, (px, pm.width())
img = icon.pixmap(48, 48).toImage()
assert img.pixelColor(24, 24).alpha() > 0 and img.pixelColor(0, 0).alpha() == 0, \
    "the tile is drawn inside transparent rounded corners"
assert brand.set_app_id() in (True, False) and brand.set_app_id() in (True, False)
print("PASS: icon at %s px; taskbar identity call is safe to repeat" % (brand.SIZES,))

live, S1 = window("polish_live", live=True)
assert live._title_text(False) == "Orderflow Station — ASII · Live", live._title_text(False)
assert live._title_text(True).endswith("· ● Recording"), live._title_text(True)
live._refresh_session()
assert live.windowTitle().startswith("Orderflow Station — ASII · Live"), live.windowTitle()
rep, S2 = window("polish_replay", live=False)
assert rep._title_text(False) == "Orderflow Station — ASII · Replay", rep._title_text(False)
print("PASS: title reads %r" % live.windowTitle())

# ============================================================
#  2. DOM tick flash
# ============================================================
dom = panel(live, "dom")
m = dom.model()
m.on_event(book("BID", [(100, 10000), (99, 20000)], T0))
m.on_event(book("OFFER", [(102, 10000), (103, 20000)], T0))
dom.refresh()
assert not dom.flash.active, "the first look at a book is not a change"

m.on_event(book("BID", [(100, 30000), (99, 5000)], T0 + 1))     # 100 grew, 99 shrank
dom.refresh()
act = dom.flash.active
assert ("sz", 100.0) in act and act[("sz", 100.0)][1] == QtGui.QColor(BULL), act
assert ("sz", 99.0) in act and act[("sz", 99.0)][1] == QtGui.QColor(BEAR), act
assert dom.flash.timer.isActive(), "a fade needs its timer running"
r, c = dom.flash.cells[("sz", 100.0)][0]
assert dom.dom.item(r, c).background().color().alpha() > 0, "the grown level is tinted"
print("PASS: DOM level that grew flashes green, one that shrank flashes red")

m.on_event(book("BID", [(101, 1000), (100, 30000), (99, 5000)], T0 + 2))   # touch moves up
dom.refresh()
assert ("px", 101.0) in dom.flash.active, "a new best bid pulses its price cell"
print("PASS: best bid moving 100 -> 101 pulses the price cell")

# expiry restores what was underneath, then the timer stops itself
for k, (t0, col) in list(dom.flash.active.items()):
    dom.flash.active[k] = (t0 - 10.0, col)
dom.flash.tick()
assert not dom.flash.active and not dom.flash.timer.isActive(), "faded out and stopped"
pr, pc = next((rr, cc) for rr in range(dom.dom.rowCount()) for cc in (1,)
              if dom.dom.item(rr, cc) and dom.dom.item(rr, cc).text() == "101")
base = dom.dom.item(pr, pc).data(of_panels.BASE_ROLE)
assert base is not None and dom.dom.item(pr, pc).background().color() == QtGui.QBrush(base).color()
print("PASS: after the fade the lit best-bid cell is back to its own colour; timer stopped")

live.cfg["tick_flash"] = False
m.on_event(book("BID", [(101, 9000), (100, 30000)], T0 + 3))
dom.refresh()
assert not dom.flash.active, "tick_flash off must flash nothing"
live.cfg["tick_flash"] = True
print("PASS: tick_flash off -> nothing flashes")

live.cfg["dom_pro"] = False                                     # classic layout too
dom.refresh()
m.on_event(book("OFFER", [(102, 50000), (103, 20000)], T0 + 4))
dom.refresh()
assert ("sz", 102.0) in dom.flash.active, dom.flash.active
r, c = dom.flash.cells[("sz", 102.0)][0]
assert c == 4, "classic layout: ask size is column 4, got %d" % c
live.cfg["dom_pro"] = True
dom.refresh()
print("PASS: classic layout flashes too, in its own size column")

rdom = panel(rep, "dom")
rm = rdom.model()
rm.on_event(book("BID", [(100, 10000)], T0))
rdom.refresh()
rm.on_event(book("BID", [(100, 90000)], T0 + 1))
rdom.refresh()
assert not rdom.flash.active, "replay is a finished day: nothing flashes"
print("PASS: replay does not flash")

# ============================================================
#  3. tape: flash, units, size bar
# ============================================================
tm = m                              # the tape shares the DOM's model (same spec)
for i in range(5):                  # history: there BEFORE this tape first looks
    tm.on_event(trade(100, 2000, T0 + 10 + i))
# A tape opened mid-session -- a NEW panel, not the default one, which already
# took its first look at an empty list when the window was built.
tape = live.add_panel("tape", group="A")
tape.setVisible(True)
app.processEvents()
assert tape.model() is tm
tape.refresh()
assert not tape.flash.active, "opening a tape mid-session must not flash its history"
tm.on_event(trade(101, 9000, T0 + 20, side_flag=1))
tm.on_event(trade(100, 300, T0 + 21, side_flag=2))
tape.refresh()
assert len(tape.flash.active) == 2, "exactly the two new prints flash: %d" % len(tape.flash.active)
assert {k[1] for k in tape.flash.active} == {id(tm.trades[-1]), id(tm.trades[-2])}
print("PASS: a tape opened mid-session flashes only the two prints after it opened")

live.cfg["header_units"] = "lots"
tape.refresh()
assert tape.tape.horizontalHeaderItem(2).text() == "Lots"
newest = tm.trades[-1]
q = tape.tape.item(0, 2)
assert q.text() == format(newest["qty"] / 100, ",.0f"), (q.text(), newest["qty"])
big = live.big_lots() * 100
UR = QtCore.Qt.ItemDataRole.UserRole
shown = tm.trades[-live.cfg["tape_rows"]:]
qs = sorted(r["qty"] for r in shown)
ref = qs[int(0.95 * (len(qs) - 1))]
assert abs(q.data(UR) - min(newest["qty"] / ref, 1.0)) < 1e-9, "bar = size vs the p95 print"
assert bool(q.data(UR + 1)) == (newest["qty"] >= big), "Big>= still drives the highlight"
fracs = [tape.tape.item(i, 2).data(UR) for i in range(tape.tape.rowCount())]
assert len({round(f, 3) for f in fracs}) > 1, "bars must vary, not all saturate"
assert QtGui.QColor(q.data(DepthBarDelegate.COLOR_ROLE)) in (QtGui.QColor(BULL), QtGui.QColor(BEAR))
assert tape.tape.item(0, 3).text() in ("▲ buy", "▼ sell"), tape.tape.item(0, 3).text()
print("PASS: tape shows %s lots with a side-coloured size bar and %r"
      % (q.text(), tape.tape.item(0, 3).text()))

# ============================================================
#  4. the bar is painted OVER a cell background, not under it
# ============================================================
t = QtWidgets.QTableWidget(1, 1)
t.setItemDelegateForColumn(0, DepthBarDelegate("bid", BULL, t))
t.horizontalHeader().setVisible(False)
t.verticalHeader().setVisible(False)
t.setColumnWidth(0, 120)
t.setRowHeight(0, 24)
t.resize(140, 40)
it = QtWidgets.QTableWidgetItem("")
gold = QtGui.QColor(74, 62, 30)
it.setBackground(gold)
it.setData(UR, 1.0)
it.setData(UR + 1, True)
it.setData(DepthBarDelegate.COLOR_ROLE, QtGui.QColor(BULL))
t.setItem(0, 0, it)
t.show()
app.processEvents()
shot = t.viewport().grab().toImage()
px = shot.pixelColor(20, 12)
assert px.green() > gold.green() + 60, \
    "the size bar must show over a big-print background; got %s" % px.name()
print("PASS: size bar visible over a highlighted cell (pixel %s, background %s)"
      % (px.name(), gold.name()))

# ============================================================
#  5. clock-time axes on footprint and regime
# ============================================================
fp_win, S3 = window("polish_axes", live=False)
fm = fp_win.model_for("ASII", "time", 60)
for i in range(40):                                  # 40 one-minute bars
    fm.on_event(trade(100 + (i % 5), 1000, T0 + i * 60 + 5))
fp = panel(fp_win, "footprint")
fp.refresh()
ids = fm.bar_ids()
assert len(fp.taxis._eps) == len(ids) and fp.taxis._gap == 300.0, (len(fp.taxis._eps), fp.taxis._gap)
(step, xs), = fp.taxis.tickValues(0, len(ids) - 1, 600)
labels = fp.taxis.tickStrings(xs, 1.0, step)
assert labels and all(len(s) == 5 and s[2] == ":" for s in labels), labels
print("PASS: footprint axis reads %s ... instead of bar numbers" % labels[:3])

rg = panel(fp_win, "regime")
rg._label_bar_times(fm)
assert rg.taxis._eps is not None and len(rg.taxis._eps) == len(ids)
real_spec = rg.spec
rg.spec = lambda: dict(real_spec(), bar_kind="tick", bar_size=50)
rg._taxis_key = None
rg._label_bar_times(fm)
assert rg.taxis._gap is None, "tick bars are irregular in time: no gap rule"
rg.spec = real_spec
print("PASS: regime axis reads clock time; tick bars skip the gap rule")

# ============================================================
#  6. the preview script no longer flattens the regime chart
# ============================================================
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools"))
import make_previews  # noqa: E402

make_previews.frame_data(fp_win, app)
lo, hi = rg.p.vb.viewRange()[1]
assert lo <= 0.0 and hi >= 1.0, "regime y must stay 0..1 after frame_data, got %s" % [lo, hi]
print("PASS: preview framing keeps regime at %.2f..%.2f with its TREND/CHOP lines" % (lo, hi))

for s in (S1, S2, S3):
    s.clear()
print()
print("ALL PASS")
