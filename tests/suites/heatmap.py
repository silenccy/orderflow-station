"""The heatmap reads like a chart: clock time, real tick bands, honest depth, walls.

What this suite pins, each one a way the old heatmap was hard to use:
  * IDX changes tick size at 200/500/2000/5000. One global minimum tick meant
    every odd row above 200 on BUMI was a price that cannot exist — 0 % filled —
    so the field striped and smoothing halved each real level. Bands fix that.
  * "Outside the feed's visible depth" (unknown) and "nothing resting" (known)
    were the same colour. They are NaN and 0 now, and NaN paints transparent.
  * The x-axis read 0, 200, 400 — column numbers. It reads clock time.
  * Walls were a per-column argmax that zigzagged. They are levels that HELD.
  * The full array was rebuilt on every refresh (32 ms, ~23 % of a core at
    7 Hz) even when no column had arrived. Updates are incremental, and a
    refresh with nothing new does not repaint the field at all.
"""
import math
import os
import sys
from datetime import datetime

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import numpy as np
from PySide6 import QtCore, QtWidgets

from orderflow import model as of_model
from orderflow.model import HeatGrid, heat_scale, heat_unscale

T0 = 1_770_000_000.0
LADDER = {198: 10.0, 199: 20.0, 200: 30.0, 202: 40.0, 204: 50.0}


def cols_of(snaps, start=T0, step=1.0, mid=201.0):
    return [(start + j * step, s, mid) for j, s in enumerate(snaps)]


# ============================================================
#  1. tick bands: no dead rows across the 200 boundary
# ============================================================
g = HeatGrid()
g.update(cols_of([LADDER] * 3))
y0, row_h, r2l = g.rows()
painted = [g.ladder[i] if i >= 0 else None for i in r2l]
assert None not in painted, "holes in the display grid: %s" % painted
assert painted == [198, 199, 200, 202, 202, 204, 204], painted
assert row_h == 1.0 and y0 == 197.5, (row_h, y0)
disp = g.display(g.levels)
assert disp[3, 0] == 40.0, "row 201 must carry 202's size, got %s" % disp[3, 0]
hit = g.value_at(0, 201.0)
assert hit[0] == 202 and hit[1] == 40.0 and hit[2] == "ask", hit
print("PASS: tick change at 200 -> no dead rows; row 201 belongs to 202 (hover says 202)")

# a valid price nobody quoted all window is a hole, not smeared over
g_hole = HeatGrid()
g_hole.update(cols_of([{200: 1, 202: 1, 204: 1, 206: 1, 212: 1, 214: 1}] * 2, mid=207.0))
lad = [g_hole.ladder[i] if i >= 0 else None for i in g_hole.rows()[2]]
assert None in lad, "a 6-tick hole should stay unpainted: %s" % lad
print("PASS: a never-quoted stretch stays empty instead of borrowing a neighbour")

# ============================================================
#  2. depth honesty: unknown is NaN, known-empty is 0
# ============================================================
g2 = HeatGrid()
g2.update(cols_of([LADDER, {200: 5.0, 204: 7.0}], mid=202.0))
col = dict(zip(g2.ladder, g2.levels[:, 1]))
assert math.isnan(col[198]) and math.isnan(col[199]), col
assert col[202] == 0.0, "inside the visible depth with nothing there is 0, got %s" % col[202]
assert col[200] == 5.0 and col[204] == 7.0, col
print("PASS: beyond visible depth = NaN, inside-but-empty = 0, never confused")

# ============================================================
#  3. incremental == from-scratch, and only new prices rebuild
# ============================================================
rng = np.random.default_rng(7)
snaps = [{p: float(rng.integers(1, 100)) for p in LADDER} for _ in range(60)]
full = cols_of(snaps)
inc = HeatGrid()
inc.update(full[:40])
for n in range(41, 61):                         # model-style: append one, trim one
    assert inc.update(full[n - 40:n]) in ("appended", "trimmed"), n
ref = HeatGrid()
ref.update(full[20:60])
assert inc.levels.shape == ref.levels.shape, (inc.levels.shape, ref.levels.shape)
assert np.array_equal(np.isnan(inc.levels), np.isnan(ref.levels))
assert np.allclose(np.nan_to_num(inc.levels), np.nan_to_num(ref.levels))
assert inc.eps == ref.eps and inc.rebuilds == 1, inc.rebuilds
assert inc.update(full[20:60]) == "unchanged"
v = inc.version
inc.update(full[21:60] + [(full[-1][0] + 1, {**LADDER, 206: 9.0}, 201.0)])
assert inc.rebuilds == 2 and inc.version > v, "a new price must rebuild exactly once"
print("PASS: 20 incremental steps == a full build; a new price rebuilds once")

# ============================================================
#  4. persistent walls
# ============================================================
def wall_grid(held, blink_every=None):
    snaps = []
    for j in range(60):
        s = {198: 10.0, 199: 10.0, 200: 10.0, 202: 10.0, 204: 10.0}
        on = j < held and not (blink_every and j % blink_every == 0 and j)
        if on:
            s[204] = 50.0                           # 5x the median
        snaps.append(s)
    gw = HeatGrid()
    gw.update(cols_of(snaps))
    return gw.persistent_walls(mult=3.0, min_secs=30.0, blink=2)

w40 = wall_grid(40)
assert len(w40) == 1 and w40[0]["price"] == 204 and w40[0]["side"] == "ask", w40
assert not w40[0]["alive"], "a wall that ended at col 39 is not standing at col 59"
assert wall_grid(3) == [], "3 s is a flicker, not a wall"
assert len(wall_grid(60, blink_every=7)) == 1, "1-column blinks must not split a wall"
assert wall_grid(60)[0]["alive"], "a wall to the right edge is standing"
# a tick-2 level paints two rows but is ONE wall
assert len([w for w in wall_grid(60) if w["price"] == 204]) == 1
print("PASS: walls need size AND time; blinks tolerated; tick-2 wall counted once")

# ============================================================
#  5. time: gaps found; the axis speaks clock time
# ============================================================
eps_gap = [T0 + j for j in range(30)] + [T0 + 29 + 420 + j for j in range(30)]
gg = HeatGrid()
gg.update([(e, LADDER, 201.0) for e in eps_gap])
gaps = gg.gaps(10.0)
assert gaps == [(29, 420.0)], gaps
print("PASS: a 7-minute jump between columns is reported as a gap")

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
from orderflow.chart_items import TimeAxis  # noqa: E402

ax = TimeAxis(orientation="bottom")
base = math.floor(T0 / 3600) * 3600 + 9 * 60      # a round minute
eps = [base + 7 + j * 2.0 for j in range(900)]       # 30 minutes, 2 s columns
ax.set_epochs(eps, gap_sec=10.0)
(step, xs), = ax.tickValues(0, len(eps) - 1, 700)
times = [ax.epoch_at(x) for x in xs]
off = datetime.fromtimestamp(times[0]).astimezone().utcoffset().total_seconds()

def off_grid(t):
    r = (t + off) % step
    return min(r, step - r)                         # 299.9999 is ON a 300 s mark


assert step >= 60 and all(off_grid(t) < 1e-3 for t in times), (step, times[:3])
labels = ax.tickStrings(xs, 1.0, step)
assert all(len(s) == 5 and s[2] == ":" for s in labels), labels
assert labels[0] == datetime.fromtimestamp(times[0]).strftime("%H:%M")
print("PASS: ticks on round %d-min marks, labelled %s ..." % (step // 60, labels[:3]))

ax.set_epochs(eps_gap, gap_sec=10.0)
(_s, gxs), = ax.tickValues(0, len(eps_gap) - 1, 700)
inside = [x for x in gxs if 29 < x < 30]
assert not inside, "no tick may land inside the 7-minute hole: %s" % inside
print("PASS: no axis tick drawn inside a time gap")

# ============================================================
#  6. the legend's lots are the real sizes
# ============================================================
lv = np.array([[100.0, 400.0, np.nan], [900.0, 0.0, 1600.0]])
sc, hi, order = heat_scale(lv, "equalize", 3.0, 97)
assert hi == 1.0 and math.isnan(sc[0, 2]) and sc[1, 1] == 0.0, sc
assert heat_unscale(1.0, "equalize", 3.0, order) == 1600.0
assert heat_unscale(0.0, "equalize", 3.0, order) == 100.0
sc2, _hi, _o = heat_scale(lv, "sqrt", 3.0, 97)
assert abs(heat_unscale(sc2[1, 2], "sqrt") - 1600.0) < 1e-6
assert abs(heat_unscale(math.log1p(900.0), "log") - 900.0) < 1e-6
print("PASS: legend inverts equalize/sqrt/log back to resting size")

# ============================================================
#  7. readout words
# ============================================================
from orderflow import panels as of_panels  # noqa: E402

# a trending market makes walls by the hundred (the preview session: 201, median
# life 46 s). The overlay must stay bounded and keep the ones that matter.
eps_w = [T0 + j for j in range(400)]
many = [{"price": 100 + k, "j0": k, "j1": k + 35, "size": 10.0 + k, "side": "ask",
         "alive": False} for k in range(40)]
many += [{"price": 300 + k, "j0": 300, "j1": 399, "size": s, "side": "bid", "alive": True}
         for k, s in enumerate((5.0, 50.0, 20.0))]
alive, shown = of_panels.pick_walls(many, eps_w)
assert [w["size"] for w in alive] == [50.0, 20.0, 5.0], "standing walls, biggest first"
assert len(shown) == of_panels.WALLS_MAX, len(shown)
assert all(w in shown for w in alive), "every standing wall is outlined"
ended = [w for w in shown if not w["alive"]]
assert min(w["size"] for w in ended) > max(w["size"] for w in many[:40] if w not in ended), \
    "the ended walls kept must be the strongest"
print("PASS: 43 walls -> %d outlined: all 3 standing + the 9 strongest ended" % len(shown))

lots = {"header_units": "lots"}
hhmmss = datetime.fromtimestamp(T0).strftime("%H:%M:%S")
assert of_panels.heat_readout((202, 18_240_000.0, "bid", T0), T0, lots) == \
    "%s · 202 · bid 182,400 lots" % hhmmss
assert "beyond the visible depth" in of_panels.heat_readout((198, float("nan"), "bid", T0), T0, lots)
assert "nothing resting" in of_panels.heat_readout((202, 0.0, "ask", T0), T0, lots)
assert of_panels.heat_readout((202, 500.0, "ask", T0), T0, {"header_units": "shares"}).endswith("ask 500 sh")
assert of_panels._compact(1_839_686) == "1.8M" and of_panels._compact(220_318) == "220.3k"
print("PASS: readout reads '%s · 202 · bid 182,400 lots'" % hhmmss)

# ============================================================
#  8. the real panel: transparency, legend, walls, no wasted repaint
# ============================================================
from orderflow import app as of_app  # noqa: E402


def book_events(sym, n=90):
    """Bids 190..200 (tick 1), asks 202..216 (tick 2): crosses the 200 boundary.
    A 5x ask wall sits at 210 the whole time."""
    evs = []
    for i in range(n):
        ep = T0 + i
        iso = datetime.fromtimestamp(ep).isoformat(timespec="microseconds")
        bids = [(float(p), 3, 100_000.0) for p in range(200, 189, -1)]
        asks = [(float(p), 3, 500_000.0 if p == 210 else 100_000.0) for p in range(202, 217, 2)]
        evs.append(("book", sym, "BID", bids, iso))
        evs.append(("book", sym, "OFFER", asks, iso))
    return evs


S = QtCore.QSettings("orderflow-test", "heatmap")
S.clear()
S.sync()
win = of_app.MainWindow({"BUMI": book_events("BUMI")}, "time", 60, ["BUMI"],
                        live=False, settings=S)
win.show()
app.processEvents()
hp = next((p for p in win.panels if p.kind == "heatmap"), None) or win.add_panel("heatmap", group="A")
hp.setVisible(True)
app.processEvents()
hp.refresh()
app.processEvents()

g = hp.grid
assert g.ladder and 201.0 not in g.ladder, g.ladder

# without matplotlib, source="matplotlib" returns None instead of raising; that
# once made a saved 'viridis' silently render as the bookmap fallback
cm = hp.cbar.colorMap()
assert cm is not None and cm.name == win.cfg["colormap"], (getattr(cm, "name", None),
                                                          win.cfg["colormap"])
print("PASS: the colour bar uses the configured colormap (%s)" % cm.name)
img = hp.hm_img.image
assert img is not None and img.shape[0] == len(g.rows()[2]), (None if img is None else img.shape)
y0, row_h, r2l = g.rows()
r201 = int(round((201.0 - (y0 + row_h / 2)) / row_h))
assert not np.isnan(img[r201]).all(), "row 201 must be painted by 202, not empty"
print("PASS: panel image has no dead row at 201 (%d rows x %d columns)" % img.shape)

# NaN really is transparent: put an unknown cell in and look at the pixel
hp.hm_img.setImage(np.array([[0.5, np.nan]]), autoLevels=False)
hp.hm_img.render()
q = hp.hm_img.qimage
assert q.pixelColor(0, 0).alpha() == 255 and q.pixelColor(1, 0).alpha() == 0, \
    (q.pixelColor(0, 0).alpha(), q.pixelColor(1, 0).alpha())
hp._drawn = None
hp.refresh()
print("PASS: an unknown cell renders fully transparent (alpha 0)")

assert hp.taxis._eps is not None and len(hp.taxis._eps) == len(g.eps)
lab = hp._legend_strings([1.0], 1.0, 1.0)[0]
assert lab == of_panels._compact(5000.0), lab              # 500,000 sh = 5,000 lots
print("PASS: legend's top colour is labelled %s lots = the biggest level" % lab)

walls = g.persistent_walls(win.cfg["wall_mult"], of_panels.WALL_MIN_SECS, of_panels.WALL_BLINK)
assert any(w["price"] == 210 and w["alive"] for w in walls), walls
shown = [t for t in hp._wall_labels if t.isVisible()]
assert shown and any(t.toPlainText() == "5k" for t in shown), [t.toPlainText() for t in shown]
print("PASS: the 210 ask wall is found, standing, and labelled %s" % shown[0].toPlainText())

calls = {"n": 0}
orig = hp._draw_field


def counting(c):
    calls["n"] += 1
    orig(c)


hp._draw_field = counting
for _ in range(5):
    hp.refresh()
assert calls["n"] == 0, "nothing changed, yet the field was repainted %d times" % calls["n"]
win.cfg["hm_scale"] = "sqrt"
hp.refresh()
assert calls["n"] == 1, "a scale change must repaint once, got %d" % calls["n"]
print("PASS: 5 idle refreshes repaint nothing; a setting change repaints once")

# pre-open: a full book and no trades. The y-axis follows an empty footprint,
# which used to leave the whole field off-screen -- a blank heatmap.
assert not hp.model().bar_ids(), "this synthetic book has no trades, like pre-open"
vlo, vhi = hp.p.vb.viewRange()[1]
assert vlo <= 192 and vhi >= 214, "the book must be framed with no trades: %s" % [vlo, vhi]
print("PASS: pre-open (book, no trades) still frames the book: y %.0f..%.0f" % (vlo, vhi))

# hover: the readout appears and says the level, the side and the size
j = len(g.eps) - 5
sp = hp.p.vb.mapViewToScene(QtCore.QPointF(j, 210.0))
inside = hp.p.vb.sceneBoundingRect().contains(sp)
hp._mouse_moved((sp,))
txt = hp.readout.toPlainText()
assert hp.readout.isVisible() and " · 210 · ask 5,000 lots" in txt, (inside, txt)
print("PASS: hovering the wall reads %r" % txt)

S.clear()
print()
print("ALL PASS")
