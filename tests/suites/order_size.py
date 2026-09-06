"""Order-size analytics: average size per level, and reload detection.

The rule is NOT "the level got bigger". Book snapshots arrive about once a second,
so a level hit and topped back up inside one second shows no net change at all.
It compares against what should have been left:

    expected = old_value - consumed_by_trades
    reload   = new_value - expected
"""
from orderflow import model as of_model

P = 190
ISO = "2026-08-31T14:00:%02d.000000"


def book(m, sec, freq, value, side="BID"):
    m.on_event(("book", "BUMI", side, [(P, freq, value)], ISO % sec))


def trade(m, sec, qty, tid):
    m.on_event(("trade", {"symbol": "BUMI", "price": float(P), "qty": float(qty),
                          "value": 0.0, "id": tid, "flag": 2,
                          "sec": 1_772_000_000 + sec, "ns": 0,
                          "recv_iso": ISO % sec, "trade_time": ISO % sec}))


def run(seq):
    """seq of (sec, freq, value, traded_before_next_snapshot)."""
    m = of_model.OrderflowModel(of_model.make_bars("time", 60))
    tid = 0
    for sec, freq, value, traded in seq:
        book(m, sec, freq, value)
        if traded:
            tid += 1
            trade(m, sec, traded, tid)
    return m


# ---- 1. topped back up after being hit -> a reload ------------------------
m = run([(0, 5, 100000, 10000), (1, 5, 100000, 0)])
rows = m.reload_rows(min_count=1, window_sec=0)
assert len(rows) == 1, rows
price, count, lots, rate, _ep = rows[0]
assert price == P and count == 1 and abs(lots - 100) < 1e-6, rows[0]
print("PASS: hit for 10,000 sh and still full -> reload of %.0f lots" % lots)

# ---- 2. ordinary consumption -> NOT a reload -----------------------------
m = run([(0, 5, 100000, 10000), (1, 5, 90000, 0)])
assert m.reload_rows(min_count=1, window_sec=0) == [], m.reload_rows(min_count=1, window_sec=0)
print("PASS: level shrank by exactly what traded -> no reload")

# ---- 3. order count jumped -> a crowd arriving, not one order reloading ---
m = run([(0, 5, 100000, 10000), (1, 40, 100000, 0)])
assert m.reload_rows(min_count=1, freq_tol=3, window_sec=0) == []
# with the tolerance opened up it does show, proving freq is what excluded it
assert len(m.reload_rows(min_count=1, freq_tol=100, window_sec=0)) == 1
print("PASS: order count 5 -> 40 excluded by freq_tol, admitted when relaxed")

# ---- 4. size added with no trades -> not a reload ------------------------
m = run([(0, 5, 100000, 0), (1, 5, 150000, 0)])
assert m.reload_rows(min_count=1, window_sec=0) == []
print("PASS: size added with nothing consumed -> not a reload")

# ---- 5. min_frac gates a partial top-up ---------------------------------
m = run([(0, 5, 100000, 10000), (1, 5, 92000, 0)])     # only 2,000 of 10,000 back
assert m.reload_rows(min_count=1, min_frac=0.5, window_sec=0) == []
assert len(m.reload_rows(min_count=1, min_frac=0.1, window_sec=0)) == 1
print("PASS: a 20%% top-up fails min_frac=0.5 and passes min_frac=0.1")

# ---- 6. average order size ----------------------------------------------
m = of_model.OrderflowModel(of_model.make_bars("time", 60))
m.on_event(("book", "BUMI", "BID", [(190, 4, 400000)], ISO % 0))     # 4 orders, 4,000 lots
m.on_event(("book", "BUMI", "OFFER", [(191, 400, 400000)], ISO % 0))  # 400 orders, same size
rows = {(p, s): a for p, s, a, _f, _l in m.order_size_rows(10)}
assert abs(rows[(190, "bid")] - 1000) < 1e-6, rows
assert abs(rows[(191, "ask")] - 10) < 1e-6, rows
print("PASS: same 4,000 lots -> avg 1,000 across 4 orders vs 10 across 400")

# freq 0 must not divide by zero
m.on_event(("book", "BUMI", "BID", [(189, 0, 50000)], ISO % 1))
sizes = {(p, s): a for p, s, a, _f, _l in m.order_size_rows(10)}
assert sizes[(189, "bid")] == 500, sizes[(189, "bid")]
print("PASS: freq of 0 falls back to the level total instead of dividing by zero")

# ---- 7. diag reports it --------------------------------------------------
m = run([(0, 5, 100000, 10000), (1, 5, 100000, 5000), (2, 5, 100000, 0)])
assert m.diag()["reloads"] >= 1, m.diag()["reloads"]
print("PASS: diag() reports %d replenishing level(s)" % m.diag()["reloads"])

print("\nALL PASS")
