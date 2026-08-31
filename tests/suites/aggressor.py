"""Aggressor side: prefer the exchange's tag, infer only when it is absent.

Every footprint, delta and CVD number depends on this one decision, so it is worth
pinning down: which source decided, and that the fallback still behaves.
"""
from orderflow import model as of_model

BID, ASK = 190, 191


def rec(price, flag=None, tid=1, qty=100.0):
    return {"symbol": "BUMI", "price": float(price), "qty": qty, "value": 0.0,
            "id": tid, "flag": flag, "sec": 1_772_000_000 + tid, "ns": 0}


# ---- 1. the tag wins, even against the book ------------------------------
# price sits ON THE BID, which the quote rule would call a sell; flag 1 says buy.
side, src = of_model.aggressor(rec(BID, flag=1), BID, ASK, None, "sell")
assert (side, src) == ("buy", "flag"), (side, src)
side, src = of_model.aggressor(rec(ASK, flag=2), BID, ASK, None, "buy")
assert (side, src) == ("sell", "flag"), (side, src)
print("PASS: flag 1 -> buy and flag 2 -> sell, overriding the quote rule")

# ---- 2. no tag -> Lee-Ready, and we report which rule fired --------------
side, src = of_model.aggressor(rec(ASK, flag=None), BID, ASK, None, "sell")
assert (side, src) == ("buy", "quote"), (side, src)
side, src = of_model.aggressor(rec(BID, flag=None), BID, ASK, None, "buy")
assert (side, src) == ("sell", "quote"), (side, src)
print("PASS: untagged prints fall through to the quote rule")

# inside the spread -> tick rule, then carry on a zero tick
side, src = of_model.aggressor(rec(190.5, flag=None), BID, ASK, 190.0, "sell")
assert (side, src) == ("buy", "tick"), (side, src)
side, src = of_model.aggressor(rec(190.5, flag=None), BID, ASK, 190.5, "sell")
assert (side, src) == ("sell", "carry"), (side, src)
print("PASS: inside the spread -> tick rule, zero tick -> carry")

# an unknown tag value must not be trusted
side, src = of_model.aggressor(rec(ASK, flag=7), BID, ASK, None, "sell")
assert src == "quote", "an unrecognised flag must fall through, got %r" % src
print("PASS: an unrecognised flag falls through instead of being guessed at")

# ---- 3. blank tag = auction/negotiated, still inferred -------------------
# (the closing auction and block trades arrive with no tag; they must not be
#  silently dropped, just inferred like any other untagged print)
side, src = of_model.aggressor(rec(183, flag=None), BID, ASK, None, "buy")
assert src == "quote" and side == "sell", (side, src)
print("PASS: blank-tag prints are inferred, not discarded")

# ---- 4. diag attributes each trade to its source -------------------------
m = of_model.OrderflowModel(of_model.make_bars("time", 60))
m.on_event(("book", "BUMI", "BID", [(BID, 2, 10000)], "2026-08-31T14:00:00.000000"))
m.on_event(("book", "BUMI", "OFFER", [(ASK, 2, 10000)], "2026-08-31T14:00:00.000000"))
m.on_event(("trade", rec(ASK, flag=1, tid=1)))
m.on_event(("trade", rec(BID, flag=2, tid=2)))
m.on_event(("trade", rec(ASK, flag=None, tid=3)))
d = m.diag()
assert d["cls_flag"] == 2, d["cls_flag"]
assert d["cls_quote"] == 1, d["cls_quote"]
assert d["cls_tick"] == 0 and d["cls_carry"] == 0
print("PASS: diag() -> cls_flag=%d cls_quote=%d cls_tick=%d cls_carry=%d"
      % (d["cls_flag"], d["cls_quote"], d["cls_tick"], d["cls_carry"]))

# the aggregation invariant still holds
assert abs(d["vap_sh"] - d["fp_sh"]) < 0.5, (d["vap_sh"], d["fp_sh"])
assert d["buys"] + d["sells"] == 3
print("PASS: volume-at-price still agrees with the footprint (%d sh)" % d["vap_sh"])

print("\nALL PASS")
