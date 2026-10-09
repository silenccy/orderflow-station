"""'Big' means big for THIS stock.

The tape's gold highlight used one number for every stock -- 50 lots. BUMI's
prints nearly all clear that, so its whole tape went gold and the highlight
said nothing; ASII's mostly do not, so almost nothing lit up. Now each stock
gets its own threshold: your number if you set one for it, otherwise auto, its
own 95th-percentile print -- roughly its top 5 %.
"""
import json
import os
import random
import sys
from datetime import datetime

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod, panels as of_panels  # noqa: E402

T0 = 1_770_000_000.0
rng = random.Random(7)


def iso(ep):
    return datetime.fromtimestamp(ep).isoformat(timespec="microseconds")


def prints(sym, n, lo_lots, hi_lots, px):
    out = []
    for i in range(n):
        ep = T0 + i * 2
        lots = rng.randint(lo_lots, hi_lots)
        out.append(("trade", {"symbol": sym, "price": float(px), "qty": float(lots * 100),
                              "value": 0.0, "id": hash((sym, i)) & 0xFFFFFF, "flag": 1,
                              "sec": int(ep), "ns": 0, "trade_time": iso(ep),
                              "recv_iso": iso(ep)}))
    return out


EVENTS = {"BUMI": prints("BUMI", 240, 300, 3000, 230),     # huge prints
          "ASII": prints("ASII", 240, 1, 40, 4800),        # small prints
          "TLKM": prints("TLKM", 10, 1, 40, 3000)}         # too few to judge
S = QtCore.QSettings("orderflow-test", "big_prints")
S.clear()
win = app_mod.MainWindow(EVENTS, "time", 60, ["BUMI", "ASII", "TLKM"], live=False, settings=S)
win.show()
app.processEvents()
mb, ma = win.model_for("BUMI", "time", 60), win.model_for("ASII", "time", 60)


def share_big(model, lots):
    return sum(r["qty"] >= lots * 100 for r in model.trades) / len(model.trades)


# ---- 1. each stock gets its own auto threshold ----------------------------
bb, ba = win.big_lots("BUMI", mb), win.big_lots("ASII", ma)
assert bb and ba and bb > 10 * ba, (bb, ba)
for sym, model, lots in (("BUMI", mb, bb), ("ASII", ma, ba)):
    s = share_big(model, lots)
    assert 0.02 <= s <= 0.08, "%s: auto %s lots highlights %.1f%% of prints" % (sym, lots, 100 * s)
print("PASS: auto BUMI %s lots, ASII %s lots -- each highlights ~5%% of its own prints"
      % (format(bb, ","), ba))
print("      (one global 50 lots highlighted %.0f%% of BUMI and %.0f%% of ASII)"
      % (100 * share_big(mb, 50), 100 * share_big(ma, 50)))

# ---- 2. too few prints: no highlight rather than a guess ------------------
assert win.big_lots("TLKM", win.model_for("TLKM", "time", 60)) is None
print("PASS: under 30 prints auto stays quiet instead of guessing")

# ---- 3. the box shows the active stock's auto number ---------------------
win.groups["A"]["symbol"] = "BUMI"
win.group_combo.setCurrentText("A")
win._sync_group_controls()
assert win.big_spin.value() == 0
assert win.big_spin.specialValueText() == "auto · %s" % format(bb, ","), \
    win.big_spin.specialValueText()
print("PASS: box reads %r for BUMI" % win.big_spin.specialValueText())

# ---- 4. an override is per stock, and survives a restart -----------------
win.big_spin.setValue(800)
win._big_edited()
assert win.big_lots("BUMI", mb) == 800
assert win.big_lots("ASII", ma) == ba, "a BUMI override must not touch ASII"
win.groups["A"]["symbol"] = "ASII"
win._sync_group_controls()
assert win.big_spin.value() == 0 and win.big_spin.specialValueText().startswith("auto · ")
print("PASS: BUMI set to 800 lots; switching to ASII shows ASII's own auto")

win._save_settings()
saved = json.loads(S.value("big_lots_by_symbol"))
assert saved == {"BUMI": 800}, saved
assert S.value("big_lots") is None, "the old one-number-for-all key must be gone"
win2 = app_mod.MainWindow(EVENTS, "time", 60, ["BUMI"], live=False, settings=S)
assert win2.big_lots("BUMI", win2.model_for("BUMI", "time", 60)) == 800
print("PASS: the override persists per stock across a restart; old global key removed")

win.groups["A"]["symbol"] = "BUMI"
win._sync_group_controls()
assert win.big_spin.value() == 800
win.big_spin.setValue(0)
win._big_edited()
assert win.big_lots("BUMI", mb) == bb, "0 must hand BUMI back to auto"
print("PASS: typing 0 hands the stock back to auto")

# ---- 5. the tape itself: gold on its own big prints, not on everything ---
tape = next((p for p in win.panels if p.kind == "tape"), None) or win.add_panel("tape", group="A")
tape.setVisible(True)
app.processEvents()
tape.refresh()
rows = tape.tape.rowCount()
gold = sum(tape.tape.item(r, 0).data(of_panels.BASE_ROLE) is not None for r in range(rows))
assert 0 < gold < rows * 0.15, "BUMI tape: %d of %d rows gold" % (gold, rows)
print("PASS: BUMI tape has %d of %d rows gold, not all of them" % (gold, rows))

S.clear()
print()
print("ALL PASS")
