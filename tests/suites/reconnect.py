"""Reconnect + gap ledger. The properties that matter: it heals itself, it records
every hole, and the reconnect backfill does not double up the tape."""
import asyncio
import time

import websockets

from orderflow import feed as of_feed
from orderflow.paths import GAPS_CSV

of_feed.BACKOFF_START = 0.01          # keep the test quick
FRAME = bytes(50) + b"\x04ASII" + bytes(50)


def trade(tid):
    return {"symbol": "ASII", "price": 4800.0 + tid, "qty": 100.0, "value": 0.0,
            "id": tid, "flag": None, "sec": 1_770_000_000 + tid, "ns": 0}


# Three connections: each delivers some trades then drops, so there are two
# separate outages with good data in between. Trade ids overlap deliberately --
# that is the server's reconnect backfill replaying recent prints.
ATTEMPTS = [[1, 2], [2, 3], [3, 4]]
_calls = {"n": 0}


async def fake_session(symbol, base, sink, seen_ids, idle_warn):
    i = _calls["n"]
    _calls["n"] += 1
    yield ("status", "connected (fake #%d)" % i)
    for tid in ATTEMPTS[min(i, len(ATTEMPTS) - 1)]:
        if tid in seen_ids:           # the real _session dedups the same way
            continue
        seen_ids.add(tid)
        rec = trade(tid)
        rec["recv_iso"] = "2026-08-24T09:00:%02d.000000" % tid
        rec["trade_time"] = rec["recv_iso"]
        if sink:
            sink.write_trade(rec["recv_iso"], rec)
        yield ("trade", rec)
    if i < 2:
        raise websockets.ConnectionClosed(None, None)
    while True:                        # third connection stays up
        await asyncio.sleep(0.01)


of_feed._session = fake_session


async def drive():
    evs = []
    gen = of_feed.live_feed("ASII", subscribe_file=FRAME_PATH, persist=True,
                            backoff_max=0.02)
    async for ev in gen:
        evs.append(ev)
        if sum(1 for e in evs if e[0] == "gap") >= 2 and \
           sum(1 for e in evs if e[0] == "trade") >= 4:
            break
    await gen.aclose()
    return evs


# a frame file the loader will accept
import tempfile
from pathlib import Path
FRAME_PATH = Path(tempfile.mkdtemp()) / "subscribe_922.txt"
FRAME_PATH.write_text(",".join(str(b) for b in FRAME))

evs = asyncio.run(asyncio.wait_for(drive(), timeout=30))

# ---- 1. it healed itself, twice -------------------------------------------
gaps = [e[1] for e in evs if e[0] == "gap"]
assert len(gaps) == 2, "expected 2 holes, got %d" % len(gaps)
assert all(g["kind"] == "disconnect" for g in gaps), gaps
assert all(g["seconds"] >= 0 for g in gaps), gaps
assert all(g["symbol"] == "ASII" for g in gaps)
print("PASS: recovered from 2 outages, both recorded (%s)"
      % ", ".join("%.2fs" % g["seconds"] for g in gaps))

# ---- 2. the backfill did NOT double up the tape ---------------------------
ids = [e[1]["id"] for e in evs if e[0] == "trade"]
assert ids == [1, 2, 3, 4], "seen_ids must survive reconnects; got %s" % ids
print("PASS: trade ids %s - no duplicates across reconnects" % ids)

# ---- 3. it backed off, and said so ----------------------------------------
waits = [e[1] for e in evs if e[0] == "status" and "reconnecting in" in e[1]]
assert waits, "reconnect delay should be reported"
print("PASS: backoff reported: %s" % "; ".join(waits))

# ---- 4. gaps persisted, and replay gives them back ------------------------
back = [e for e in of_feed.replay_feed() if e[0] == "gap"]
assert len(back) == 2, "gaps.csv round-trip: expected 2, got %d" % len(back)
assert back[0][1]["kind"] == "disconnect"
assert back[0][1]["seconds"] >= 0
print("PASS: %d gaps round-tripped through gaps.csv and replay_feed" % len(back))

# replay stays time-ordered with gaps merged in
kinds = [e[0] for e in of_feed.replay_feed()]
assert "gap" in kinds and "trade" in kinds, kinds
print("PASS: replay merges gaps into the event stream (%d events)" % len(kinds))

# ---- 5. an expired token is not just 'a disconnect' -----------------------
assert of_feed.is_auth_failure(b"\x08\x01\x12\x14you are not authorized")
assert not of_feed.is_auth_failure(b"ASII|BID|4800;1;100")
assert not of_feed.is_auth_failure(bytes(500))       # too big to be the reply
print("PASS: auth-failure frame recognised, book frames not mistaken for it")

# ---- 6. gap_record shape --------------------------------------------------
r = of_feed.gap_record("BBCA", "token", time.time() - 12.5, time.time(), 3, "why")
assert r["seconds"] >= 12.0 and r["kind"] == "token" and r["attempts"] == 3
assert "T" in r["started"] and "T" in r["ended"]
print("PASS: gap_record -> %s" % {k: r[k] for k in ("symbol", "kind", "seconds")})

print("\nALL PASS")
