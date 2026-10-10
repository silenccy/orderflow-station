"""The watchlist is what we subscribe to; panels only say what is charted.

Before 3.1 the two were the same thing, which cost you symbols twice over:
MainWindow.__init__ seeded three link groups and dropped syms[3:] on the floor,
then _restore_panels overwrote even those from the saved roster. Picking six
tickers streamed one.

Retention is tiered and that is what makes a long watchlist affordable. Measured
against the 2026-08-31 archive, self.events costs ~36 MB per symbol-hour and is
never trimmed -- 233 MB for a 6.5 h session, each. So only charted symbols get a
buffer; watched-only symbols get _tally, which is three counters.
"""
import _safety  # noqa: F401 -- keep first: refuses to run against the real archive
import json
import sys

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod

SIX = ["ASII", "BBRI", "BMRI", "BUMI", "CUAN", "TLKM"]


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


def fresh(name, symbols=SIX, roster=None):
    s = QtCore.QSettings("orderflow-test", name)
    s.clear()
    if roster is not None:
        s.setValue("panels/roster", json.dumps(roster))
    w = app_mod.MainWindow({}, "time", 60, symbols, live=True, persist=False, settings=s)
    w.start_live()          # feeds start here, never during construction (3.2.2)
    return w, s


def trade(sym, px=100.0, qty=500.0):
    return ("trade", {"symbol": sym, "price": px, "qty": qty, "value": px * qty,
                      "id": 1, "flag": None, "sec": 0, "ns": 0,
                      "recv_iso": "2026-10-03T09:00:00",
                      "trade_time": "2026-10-03T09:00:00"})


# ---- 1. six symbols in, six watched -- none silently dropped --------------
win, _ = fresh("wl_basic")
assert win.watchlist == SIX, win.watchlist
assert set(win._wanted_symbols()) == set(SIX), win._wanted_symbols()
assert set(win.feeds) == set(SIX), sorted(win.feeds)
print("PASS: 6 chosen -> 6 watched, 6 feeds (was 1 before 3.1)")

# only the first three may seed the link groups; the rest are watched, not charted
assert {g["symbol"] for g in win.groups.values()} == {"ASII", "BBRI", "BMRI"}, win.groups
print("PASS: groups still seeded from the first three")

# ---- 2. a saved ASII-only roster must not eat the new picks ---------------
old_roster = {
    "panels": [{"kind": "footprint", "uid": 1, "group": "A", "visible": True,
                "spec": {"symbol": "ASII", "bar_kind": "time", "bar_size": 60}}],
    "groups": {g: {"symbol": "ASII", "bar_kind": "time", "bar_size": 60}
               for g in ("A", "B", "C")},
    "next_uid": 2,
}
win2, s2 = fresh("wl_restore", roster=old_roster)
assert set(win2.watchlist) >= set(SIX), win2.watchlist
assert set(win2._wanted_symbols()) >= set(SIX), win2._wanted_symbols()
charted = win2._charted_symbols()
assert charted == {"ASII"}, charted
print("PASS: ASII-only roster + 6 picks -> watches 6, charts %s" % sorted(charted))

# ---- 3. watched-but-uncharted buffers NOTHING ----------------------------
win2._on_live_batch("BUMI", [trade("BUMI")])
assert "BUMI" not in win2.events or not win2.events["BUMI"], (
    "a symbol nothing charts must not accumulate a replay buffer")
assert win2._derived["BUMI"]["trades"] == 1, win2._derived.get("BUMI")
assert win2._derived["BUMI"]["last"] == 100.0, win2._derived.get("BUMI")
print("PASS: uncharted BUMI -> 0 buffered events, but stats tick")

# charted symbol still buffers, exactly as before
win2._on_live_batch("ASII", [trade("ASII")])
assert len(win2.events.get("ASII") or []) == 1, win2.events.get("ASII")
print("PASS: charted ASII still buffers (models rebuild from it)")

# ---- 4. promotion: point a panel at it and buffering starts --------------
p = win2.panels[0]
p.set_source(symbol="BUMI")
assert "BUMI" in win2._charted_symbols(), win2._charted_symbols()
win2._on_live_batch("BUMI", [trade("BUMI", 101.0)])
assert len(win2.events.get("BUMI") or []) == 1, win2.events.get("BUMI")
print("PASS: charting BUMI starts its buffer from that moment")

# ---- 5. removal is refused while charted, allowed once it is not ---------
assert win2.remove_from_watchlist("BUMI") is False, "charted symbols cannot be dropped"
print("PASS: refused to unwatch a symbol still on screen")

# ---- 6. adding is cheap and reaches the feeds ----------------------------
win3, _ = fresh("wl_add", symbols=["ASII"])
assert win3.add_to_watchlist("bbca") is True
assert "BBCA" in win3.watchlist, win3.watchlist
assert "BBCA" in win3.feeds, sorted(win3.feeds)
assert win3.add_to_watchlist("BBCA") is False, "no duplicates"
win3._on_live_batch("BBCA", [trade("BBCA")])
assert not win3.events.get("BBCA"), "added symbol must not buffer until charted"
print("PASS: add_to_watchlist subscribes, dedupes, and buffers nothing")

assert win3.remove_from_watchlist("BBCA") is True
assert "BBCA" not in win3.watchlist and "BBCA" not in win3.feeds
print("PASS: remove_from_watchlist unsubscribes")

# ---- 7. migration: pre-3.1 settings have no watchlist key ----------------
legacy = {
    "panels": [{"kind": "footprint", "uid": 1, "group": "A", "visible": True,
                "spec": {"symbol": "TLKM", "bar_kind": "time", "bar_size": 60}}],
    "groups": {g: {"symbol": "BBCA", "bar_kind": "time", "bar_size": 60}
               for g in ("A", "B", "C")},
    "next_uid": 2,
}
win4, s4 = fresh("wl_migrate", symbols=["ASII"], roster=legacy)
assert "TLKM" in win4.watchlist and "BBCA" in win4.watchlist, win4.watchlist
assert "ASII" in win4.watchlist, win4.watchlist
print("PASS: pre-3.1 roster migrates to a watchlist of %s" % sorted(win4.watchlist))

win4._save_roster()
saved = json.loads(s4.value("panels/roster"))
assert sorted(saved["watchlist"]) == sorted(win4.watchlist), saved.get("watchlist")
print("PASS: watchlist persists into panels/roster")

# ---- 8. the counter tells the truth --------------------------------------
chip = win2._watch_chip()
assert "watching" in chip and "charting" in chip, chip
win_one, _ = fresh("wl_chip", symbols=["ASII"])
assert win_one._watch_chip() == "", "no chip when nothing is hidden from you"
print("PASS: chip shows %r when they differ, nothing when they do not"
      % ("watching/charting",))

# ---- 9. the + / - buttons actually show their glyphs ------------------------
# DARK_QSS pads every QPushButton 14 px a side; at 26 px wide that left the
# content box empty and both buttons rendered as blank squares.
win.show()
app.processEvents()
wl = (next((p for p in win.panels if p.kind == "watchlist"), None)
      or win.add_panel("watchlist", group="A"))
wl.setVisible(True)
app.processEvents()
for b in (wl.add_btn, wl.rm_btn):
    b.ensurePolished()
    opt = QtWidgets.QStyleOptionButton()
    opt.initFrom(b)
    opt.text = b.text()
    room = b.style().subElementRect(QtWidgets.QStyle.SubElement.SE_PushButtonContents,
                                    opt, b).width()
    need = b.fontMetrics().horizontalAdvance(b.text())
    assert room >= need, "%r button has %d px of room for a %d px glyph" % (b.text(), room, need)
print("PASS: + and - buttons have room for their glyphs")

for nm in ("wl_basic", "wl_restore", "wl_add", "wl_migrate", "wl_chip"):
    QtCore.QSettings("orderflow-test", nm).clear()
print()
print("ALL PASS")
