"""Multi-symbol capture: sink ownership + no snapshot shredding under concurrency."""
import asyncio, tempfile
from pathlib import Path

import websockets
from orderflow import feed as of_feed

tmp = Path(tempfile.mkdtemp())
frame_path = tmp / "subscribe_922.txt"
# a frame the loader accepts: needs 0x04 + 4 uppercase ASCII for frame_symbol()
frame_path.write_text(",".join(str(b) for b in (b"\x00" * 50 + b"\x04ASII" + b"\x00" * 50)))


class FakeWS:
    subprotocol = "web"
    async def send(self, data): pass
    async def recv(self): raise websockets.ConnectionClosed(None, None)


class FakeConnect:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return FakeWS()
    async def __aexit__(self, *a): return False


of_feed.websockets.connect = FakeConnect


async def drain(**kw):
    # reconnect=False: live_feed now retries forever by design, so a one-shot
    # drain has to opt out or it never returns
    kw.setdefault("reconnect", False)
    async for _ in of_feed.live_feed("ASII", subscribe_file=frame_path, **kw):
        pass


# --- 1. a shared sink is the caller's; live_feed must NOT close it -------------
shared = of_feed.CsvSink(book_csv=str(tmp / "b.csv"), trades_csv=str(tmp / "t.csv"),
                         raw_log=None, summary_csv=str(tmp / "s.csv"))
asyncio.run(drain(sink=shared))
assert not shared._book_f.closed, "live_feed closed a sink it does not own"
print("PASS: shared sink survives live_feed exit")
asyncio.run(drain(sink=shared))
assert not shared._book_f.closed, "second symbol closed the shared sink"
print("PASS: shared sink survives a second symbol")
shared.close()
assert shared._book_f.closed
print("PASS: caller can still close it")

# --- 2. a sink live_feed created itself must be closed -------------------------
made = {}
real_sink_cls = of_feed.CsvSink
def spy(*a, **k):
    s = real_sink_cls(book_csv=str(tmp / "b2.csv"), trades_csv=str(tmp / "t2.csv"),
                      raw_log=None, summary_csv=str(tmp / "s2.csv"))
    made["s"] = s
    return s
of_feed.CsvSink = spy
asyncio.run(drain(persist=True))
of_feed.CsvSink = real_sink_cls
assert made["s"]._book_f.closed, "live_feed leaked the sink it created"
print("PASS: self-created sink is closed on exit")

# --- 3. the real payoff: concurrent symbols must not shred book snapshots ------
# Two symbols writing 6-level snapshots into ONE sink, with awaits between them to
# force task switching. replay_feed regroups with itertools.groupby, which only
# groups ADJACENT rows -- if writes interleave mid-snapshot, levels get split.
d2 = Path(tempfile.mkdtemp())
book, trades, summ = str(d2 / "book.csv"), str(d2 / "trades.csv"), str(d2 / "summary.csv")
sink = of_feed.CsvSink(book_csv=book, trades_csv=trades, raw_log=None, summary_csv=summ)
LEVELS = [(4800 + i * 5, i + 1, (i + 1) * 100) for i in range(6)]
N = 40

async def writer(sym, side):
    for i in range(N):
        sink.write_book(f"2026-08-09T09:{i//60:02d}:{i%60:02d}.{sym}", sym, side, LEVELS)
        await asyncio.sleep(0)          # yield control mid-stream

async def race():
    await asyncio.gather(writer("ASII", "BID"), writer("BBCA", "OFFER"),
                         writer("TLKM", "BID"))

asyncio.run(race())
sink.close()

snaps = [ev for ev in of_feed.replay_feed(book, trades, summ) if ev[0] == "book"]
assert len(snaps) == 3 * N, f"expected {3*N} snapshots, got {len(snaps)}"
bad = [s for s in snaps if len(s[3]) != len(LEVELS)]
assert not bad, f"{len(bad)} shredded snapshots (first has {len(bad[0][3])} levels)"
syms = {s[1] for s in snaps}
assert syms == {"ASII", "BBCA", "TLKM"}, syms
print(f"PASS: {len(snaps)} snapshots across {sorted(syms)} all intact, 6 levels each")
print("\nALL PASS")
