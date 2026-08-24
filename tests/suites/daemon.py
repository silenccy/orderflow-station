"""Daemon lifecycle: cooperative stop, clean sink close, one-writer refusal."""
import asyncio, os, subprocess, sys, time
from pathlib import Path

from orderflow import capture as cap
from orderflow import feed as of_feed
from orderflow.paths import CAPTURE_LOCK, CAPTURE_STOP, DATA_DIR, BOOK_CSV

print("archive:", DATA_DIR)

# ---- fake feed: a couple of events, then idle forever -----------------------
async def fake_live_feed(symbol, persist=True, sink=None, **kw):
    yield ("status", "connected (fake)")
    for i in range(3):
        yield ("book", symbol, "BID", [(4800 + i, 2, 20000), (4795 + i, 1, 10000)],
               "2026-08-09T09:%02d:00.000000" % i)
        if sink:
            sink.write_book("2026-08-09T09:%02d:00.000000" % i, symbol, "BID",
                            [(4800 + i, 2, 20000), (4795 + i, 1, 10000)])
    while True:
        await asyncio.sleep(0.05)

of_feed.live_feed = fake_live_feed

# ---- 1. _run holds the lock, then stops cleanly on the flag -----------------
async def drive():
    task = asyncio.ensure_future(cap._run(["ASII", "BBCA"]))
    await asyncio.sleep(0.4)
    alive, info = cap.writer_status()
    assert alive, "daemon should hold the lock while running"
    assert info["symbols"] == ["ASII", "BBCA"], info
    print("PASS: running ->", cap.describe_status())

    cap.request_stop()                        # cooperative shutdown
    t0 = time.time()
    await asyncio.wait_for(task, timeout=15)
    print("PASS: stopped cleanly in %.1fs" % (time.time() - t0))

asyncio.run(drive())

assert not CAPTURE_LOCK.exists(), "lock must be released on exit"
assert not CAPTURE_STOP.exists(), "stop flag must be cleared on exit"
print("PASS: lock released and stop flag cleared")

# the sink was closed properly, so the rows it wrote are intact and replayable
rows = [ev for ev in of_feed.replay_feed() if ev[0] == "book"]
assert len(rows) == 6, "expected 6 snapshots, got %d" % len(rows)
assert all(len(ev[3]) == 2 for ev in rows), "a snapshot got shredded"
assert {ev[1] for ev in rows} == {"ASII", "BBCA"}
print("PASS: %d snapshots survived the clean shutdown, both symbols intact" % len(rows))

# ---- 2. a second daemon refuses while a fresh lock exists -------------------
cap.take_lock(["ASII"], "daemon")
env = dict(os.environ, ORDERFLOW_DATA=str(DATA_DIR))
r = subprocess.run([sys.executable, "-m", "orderflow.capture", "TLKM"],
                   capture_output=True, text=True, env=env, timeout=60)
assert r.returncode == 3, "expected refusal exit 3, got %d" % r.returncode
assert "refusing to start" in r.stderr, r.stderr
print("PASS: second daemon refused ->", r.stderr.strip().splitlines()[0])

# --status sees it
r = subprocess.run([sys.executable, "-m", "orderflow.capture", "--status"],
                   capture_output=True, text=True, env=env, timeout=60)
assert "Recording ASII" in r.stdout, r.stdout
print("PASS: --status ->", r.stdout.strip())

# ---- 3. once the heartbeat goes stale, a new daemon may start ---------------
old = time.time() - (cap.STALE_AFTER_SEC + 5)
os.utime(CAPTURE_LOCK, (old, old))
assert not cap.writer_status()[0]
r = subprocess.run([sys.executable, "-c",
                    "from orderflow import capture as c;"
                    "print('would start:', not c.writer_status()[0])"],
                   capture_output=True, text=True, env=env, timeout=60)
assert "would start: True" in r.stdout, r.stdout
print("PASS: a stale lock does not block a new writer")
cap.release_lock()

print("\nALL PASS")
