"""Check load_subscribe_frame(): the newest grab wins, and within one grab the frame
that subscribes the most channels wins.

2026-10-16: three frames landed within 3 s (898, 922, 916 bytes). The newest, 916 B,
lacks the trade channel, so a whole live session recorded books and no trades."""
import _safety  # noqa: F401 -- keep first: refuses to run against the real archive
import os, tempfile, time
from pathlib import Path
from orderflow import feed
from orderflow.feed import load_subscribe_frame


def mk(d, name, fields, size, fill, mtime):
    """A frame shaped like the real thing: filler bytes, then `fields` copies of the
    0x04+ticker symbol field. `fill` marks which frame was picked (frame[0])."""
    body = b"\x04ASII" * fields
    frame = bytes([fill]) * (size - len(body)) + body
    p = d / name
    p.write_text(",".join(str(b) for b in frame))
    os.utime(p, (mtime, mtime))
    return frame


now = time.time()

# ---- 1. freshness beats size across days; junk skipped ---------------------
d = Path(tempfile.mkdtemp())
mk(d, "subscribe_922.txt", 6, 922, 1, now - 86400)    # yesterday: bigger, stale
mk(d, "subscribe_916.txt", 5, 916, 2, now)            # today: smaller, fresh
(d / "subscribe_junk.txt").write_text("this is not a frame")
os.utime(d / "subscribe_junk.txt", (now + 10,) * 2)   # newest, but unparseable

frame, name = load_subscribe_frame(directory=d)
print(f"picked: {name} ({len(frame)} bytes, first byte {frame[0]})")
assert name == "subscribe_916.txt", f"expected the fresh frame, got {name}"
assert frame[0] == 2, "picked the stale frame's bytes"
print("PASS: freshness beats size across days, junk skipped")

# explicit file still overrides
frame, name = load_subscribe_frame(subscribe_file=d / "subscribe_922.txt")
assert name == "subscribe_922.txt" and frame[0] == 1
print("PASS: explicit subscribe_file= still wins")

# ---- 2. within one grab, the richest frame wins (the 2026-10-16 session) ----
g = Path(tempfile.mkdtemp())
mk(g, "subscribe_898.txt", 2, 898, 1, now - 3)
mk(g, "subscribe_922.txt", 6, 922, 2, now - 1)
mk(g, "subscribe_916.txt", 5, 916, 3, now)            # newest, one channel short
frame, name = load_subscribe_frame(directory=g)
assert name == "subscribe_922.txt" and frame[0] == 2, \
    f"one grab: expected the 6-field frame, got {name}"
print("PASS: within one grab the frame with the most channels wins (922 over newer 916)")

# ---- 3. an empty or symbol-less newest file never wins ---------------------
e = Path(tempfile.mkdtemp())
mk(e, "subscribe_922.txt", 6, 922, 2, now - 5)
(e / "subscribe_0.txt").write_text("")                # a placeholder / aborted save
os.utime(e / "subscribe_0.txt", (now, now))
mk(e, "subscribe_900.txt", 0, 900, 3, now)            # parses, but no ticker field
frame, name = load_subscribe_frame(directory=e)
assert name == "subscribe_922.txt", f"an empty/symbol-less frame won: {name}"
print("PASS: empty and symbol-less frames are skipped")

# ---- 4. channel count -------------------------------------------------------
counts = [feed.frame_channels(feed.parse_frame_file(g / n))
          for n in ("subscribe_898.txt", "subscribe_922.txt", "subscribe_916.txt")]
assert counts == [2, 6, 5], counts
assert feed.frame_channels(b"") == 0
print("PASS: frame_channels counts the symbol fields %s" % counts)

# ---- 5. empty dir raises a useful error -------------------------------------
empty = Path(tempfile.mkdtemp())
try:
    load_subscribe_frame(directory=empty)
    raise SystemExit("FAIL: should have raised")
except FileNotFoundError as err:
    assert "browser console catcher" in str(err)
    print("PASS: empty dir raises FileNotFoundError with the right hint")
