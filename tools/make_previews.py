"""Render the README preview images from a synthetic session.

The archive on this machine is empty, so these are generated from a random-walk
feed rather than real IDX tape. The shapes are realistic (60-level book, walls
that build and pull, size-varied prints, a couple of recorded gaps) but the
numbers are invented -- the README says so next to the images.
"""
import math
import os
import random
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
# the offscreen plugin loads zero font families without this -> tofu boxes
os.environ.setdefault("QT_QPA_FONTDIR",
                      os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts"))

from datetime import datetime

from PySide6 import QtCore, QtWidgets

from orderflow import app as of_app, feed as of_feed

OUT = os.path.join(os.getcwd(), "docs", "img")
BASE = 1_772_002_800.0          # 09:00-ish
TICK = 5


def iso(ep):
    return datetime.fromtimestamp(ep).isoformat(timespec="microseconds")


OUTAGES = [(640.0, 703.0, "disconnect", 3, "ConnectionClosed"),
           (1490.0, 1512.0, "token", 1, "server rejected the session token")]


def synth(sym, px0, n=1100, seed=7, drift=0.035):
    """A session that looks like one: trending random walk, a deep book with
    walls that persist then pull, and prints whose size distribution is skewed."""
    rng = random.Random(seed)
    evs = []
    px = float(px0)
    vel = 0.0
    wall_bid = wall_ask = None
    wall_life = 0
    tid = 0
    for i in range(n):
        ep = BASE + i * 2.0                      # ~37 min of tape
        # a recorded outage means the tape really is missing here, not just
        # annotated -- otherwise the CVD break has nothing to break across
        if any(a <= (ep - BASE) <= b for a, b, _k, _n, _d in OUTAGES):
            continue
        stamp = iso(ep)

        vel = vel * 0.93 + rng.gauss(0, 1.0) + drift * math.sin(i / 190.0) * 3
        px += vel * TICK * 0.16
        px = max(px0 * 0.97, min(px0 * 1.03, px))
        mid = round(px / TICK) * TICK

        # a wall appears, sits for a while, then gets pulled
        if wall_life <= 0 and rng.random() < 0.02:
            side = rng.choice(("bid", "ask"))
            off = rng.randint(2, 6) * TICK
            if side == "bid":
                wall_bid, wall_ask = mid - off, None
            else:
                wall_bid, wall_ask = None, mid + off
            wall_life = rng.randint(20, 70)
        wall_life -= 1
        if wall_life <= 0:
            wall_bid = wall_ask = None

        def ladder(sign, wall):
            out = []
            for k in range(1, 31):               # ~30 levels a side
                p = int(mid + sign * k * TICK)
                base = 14000 * math.exp(-k / 11.0) * (0.55 + rng.random())
                if wall is not None and p == wall:
                    base *= rng.uniform(7, 13)   # the wall
                out.append((p, max(1, int(base / 900)), int(base / 100) * 100))
            return out

        evs.append(("book", sym, "BID", ladder(-1, wall_bid), stamp))
        evs.append(("book", sym, "OFFER", ladder(+1, wall_ask), stamp))

        for _ in range(rng.randint(1, 3)):
            tid += 1
            r = rng.random()
            qty = 100 * (rng.randint(1, 6) if r < 0.72 else
                         rng.randint(8, 40) if r < 0.96 else rng.randint(60, 260))
            aggr = rng.random() < (0.5 + 0.16 * (1 if vel > 0 else -1))
            tp = mid + (TICK if aggr else -TICK) * (1 if rng.random() < 0.75 else 0)
            evs.append(("trade", {"symbol": sym, "price": float(int(tp)),
                                  "qty": float(qty), "value": float(tp) * qty,
                                  "id": tid, "flag": None, "sec": int(ep),
                                  "ns": int((ep % 1) * 1e9), "trade_time": stamp,
                                  "recv_iso": stamp}))

    last = evs[-1][1]["price"]
    prices = [e[1]["price"] for e in evs if e[0] == "trade"]
    vol = sum(e[1]["qty"] for e in evs if e[0] == "trade")
    evs.append(("summary", {"symbol": sym, "last": last, "open": float(px0),
                            "prev": float(px0), "high": max(prices),
                            "low": min(prices), "avg": sum(prices) / len(prices),
                            "vol_sh": vol, "val": vol * last,
                            "trades": float(tid)}))
    for a, b, kind, tries, why in OUTAGES:
        evs.append(("gap", of_feed.gap_record(sym, kind, BASE + a, BASE + b, tries, why)))
    return evs


def frame_data(win, app):
    """Refresh, then frame each plot on its data so nothing renders blank."""
    win.refresh()
    for _ in range(4):
        app.processEvents()
    for p in win.panels:
        plot = getattr(p, "p", None)
        vb = plot.getViewBox() if plot is not None and hasattr(plot, "getViewBox") else None
        if vb is not None:
            try:
                vb.enableAutoRange()
                vb.autoRange()
            except Exception:
                pass
    for _ in range(4):
        app.processEvents()


def shot(win, app, name, secs=6):
    frame_data(win, app)
    for _ in range(secs):
        app.processEvents()
    path = os.path.join(OUT, name)
    ok = win.grab().save(path)
    print("  %-28s %s" % (name, "ok" if ok else "FAILED"))
    return ok


def build(events, syms, size=(1680, 960), settings_key="prev"):
    S = QtCore.QSettings("orderflow-preview", settings_key)
    S.clear(); S.sync()
    win = of_app.MainWindow(events, "time", 60, syms, live=False, settings=S)
    win.resize(*size)
    win.show()
    return win, S


def layout(win, left, right=None, weights=None, rweights=None, group="A"):
    """Rebuild the window as a left column (and optional right column), so the
    sub-previews are not one tall stack of squashed panels."""
    Q = QtCore.Qt
    for p in list(win.panels):
        win.remove_panel(p)
    lp = [win.add_panel(k, group=group, area=Q.DockWidgetArea.LeftDockWidgetArea)
          for k in left]
    for a, b in zip(lp, lp[1:]):
        win.splitDockWidget(a, b, Q.Orientation.Vertical)
    rp = []
    if right:
        rp = [win.add_panel(k, group=group, area=Q.DockWidgetArea.RightDockWidgetArea)
              for k in right]
        for a, b in zip(rp, rp[1:]):
            win.splitDockWidget(a, b, Q.Orientation.Vertical)
    for p in lp + rp:
        p.setVisible(True)
    if weights and len(lp) > 1:
        win.resizeDocks(lp, weights, Q.Orientation.Vertical)
    if rweights and len(rp) > 1:
        win.resizeDocks(rp, rweights, Q.Orientation.Vertical)
    if lp and rp:
        win.resizeDocks([lp[0], rp[0]], [66, 34], Q.Orientation.Horizontal)
    return lp + rp


def main():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    os.makedirs(OUT, exist_ok=True)
    events = {"ASII": synth("ASII", 4800, seed=11),
              "BBCA": synth("BBCA", 9200, seed=29, drift=-0.03)}

    # 1. the whole workstation, default layout
    win, _S = build(events, ["ASII", "BBCA"])
    shot(win, app, "preview.png", secs=10)

    # 2. orderflow core: footprint over its delta footer, profile beside it
    win.resize(1440, 840)
    layout(win, ["footprint", "delta"], ["vap"], weights=[78, 22])
    shot(win, app, "preview-footprint.png")

    # 3. liquidity: heatmap with the resting-depth curve beside it
    win.resize(1440, 720)
    layout(win, ["heatmap"], ["depth"])
    shot(win, app, "preview-heatmap.png")

    # 4. execution: DOM ladder, tape and watchlist
    win.resize(1320, 820)
    layout(win, ["dom", "watchlist"], ["tape"], weights=[70, 30])
    shot(win, app, "preview-dom-tape.png")

    # 5. session health: what was captured, and what the flow did
    win.resize(1320, 760)
    layout(win, ["integrity", "cvd"], ["regime"], weights=[22, 78])
    shot(win, app, "preview-integrity.png")

    win.close()
    print("done ->", OUT)


if __name__ == "__main__":
    main()
