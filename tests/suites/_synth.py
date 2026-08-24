"""Synthetic book + trade stream shared by several suites.

Deterministic on purpose: a test that fails should fail every time,
not one run in ten."""
from PySide6 import QtCore


BASE = 1770000000.0


def events_for(sym, n=120, px=4800):
    """Synthetic book + trade stream: n trades, walking price, both sides."""
    evs = []
    for i in range(n):
        ep = BASE + i * 10
        iso = QtCore.QDateTime.fromSecsSinceEpoch(int(ep)).toString(
            "yyyy-MM-ddTHH:mm:ss") + ".000000"
        p = px + (i % 7) * 5
        evs.append(("book", sym, "BID", [(p - 5, 3, 30000), (p - 10, 2, 20000)], iso))
        evs.append(("book", sym, "OFFER", [(p + 5, 4, 40000), (p + 10, 1, 10000)], iso))
        evs.append(("trade", {"symbol": sym, "price": float(p), "qty": 1000.0,
                              "value": float(p) * 1000, "id": hash((sym, i)) & 0xFFFFFF,
                              "flag": None, "sec": int(ep), "ns": 0,
                              "trade_time": iso, "recv_iso": iso}))
    return evs
