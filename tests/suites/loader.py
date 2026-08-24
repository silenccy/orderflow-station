"""Check load_subscribe_frame() now picks the NEWEST parseable frame, not the largest."""
import os, tempfile, time
from pathlib import Path
from orderflow.feed import load_subscribe_frame

d = Path(tempfile.mkdtemp())

# yesterday's frame: BIGGER (922 bytes) but stale
(d / "subscribe_922.txt").write_text(",".join(["1"] * 922))
# today's frame: SMALLER (916 bytes) but fresh
(d / "subscribe_916.txt").write_text(",".join(["2"] * 916))
# a non-decimal file that must be skipped, newest of all
(d / "subscribe_junk.txt").write_text("this is not a frame")

old = time.time() - 86400
os.utime(d / "subscribe_922.txt", (old, old))          # yesterday
os.utime(d / "subscribe_916.txt", (time.time(), time.time()))
os.utime(d / "subscribe_junk.txt", (time.time() + 10,) * 2)  # newest, but unparseable

frame, name = load_subscribe_frame(directory=d)
print(f"picked: {name} ({len(frame)} bytes, first byte {frame[0]})")
assert name == "subscribe_916.txt", f"expected the fresh frame, got {name}"
assert frame[0] == 2, "picked the stale frame's bytes"
print("PASS: freshness beats size, junk skipped")

# explicit file still overrides
frame, name = load_subscribe_frame(subscribe_file=d / "subscribe_922.txt")
assert name == "subscribe_922.txt" and frame[0] == 1
print("PASS: explicit subscribe_file= still wins")

# empty dir raises a useful error
empty = Path(tempfile.mkdtemp())
try:
    load_subscribe_frame(directory=empty)
    raise SystemExit("FAIL: should have raised")
except FileNotFoundError as e:
    assert "browser console catcher" in str(e)
    print("PASS: empty dir raises FileNotFoundError with the right hint")
