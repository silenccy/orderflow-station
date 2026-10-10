"""Recording records: rows reach disk, and the right process writes them.

2026-10-09: 'Also record to disk' was ticked and nothing was recorded --
book.csv and trades.csv were untouched since 31 Aug. Reproduced offline:

  * the dialog launched the recorder, then start_live() ran at once, found the
    lock free (the recorder needs a second or two to start) and claimed it as
    'chart'; the recorder then refused to run
  * the chart's feeds had been started while its window was being built,
    before start_live() created the sink, so they carried sink=None and the
    chart wrote nothing either
  * the recorder's refusal went to stderr only -- nowhere, under pythonw

Earlier suites checked lock ownership. None checked that a single row landed
on disk, which is the only thing recording is for. This one does, end to end
through the real feed thread, parser and CSV sink, with only the websocket
faked.
"""
import _safety  # noqa: F401 -- keep first: refuses to run against the real archive
import argparse
import os
import struct
import subprocess
import sys
import time

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import websockets
from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod, capture, feed as of_feed  # noqa: E402
from orderflow.paths import CAPTURE_LOG, DATA_DIR, TRADES_CSV  # noqa: E402

N = 12
T0 = 1_770_000_000


# ---- a few lines of protobuf, in the shape feed.parse_trades reads ----------
def varint(n):
    out = bytearray()
    while True:
        b, n = n & 0x7F, n >> 7
        out.append(b | 0x80 if n else b)
        if not n:
            return bytes(out)


def key(f, wt):
    return varint((f << 3) | wt)


def ld(f, payload):
    return key(f, 2) + varint(len(payload)) + payload


def f64(f, x):
    return key(f, 1) + struct.pack("<d", float(x))


def vi(f, n):
    return key(f, 0) + varint(n)


def trade_frame(i):
    px, qty = 4800 + 5 * (i % 3), 100 * (1 + i % 4)
    rec = (ld(1, vi(1, T0 + i) + vi(2, 0)) + ld(2, b"ASII") + f64(3, px) + f64(4, qty)
           + vi(5, 1 + i % 2) + vi(9, 900_000 + i) + f64(11, px * qty))
    return ld(8, ld(1, rec))


FRAMES = [trade_frame(i) for i in range(N)]
assert len(of_feed.parse_trades(FRAMES[0])) == 1, "the encoder must speak the real format"


class FakeWS:
    subprotocol = "web"

    def __init__(self):
        self.left = list(FRAMES)

    async def send(self, data):
        pass

    async def recv(self):
        if self.left:
            return self.left.pop(0)
        raise websockets.ConnectionClosed(None, None)    # then reconnect -> replay


class FakeConnect:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return FakeWS()
    async def __aexit__(self, *a): return False


of_feed.websockets.connect = FakeConnect
(DATA_DIR / "subscribe_922.txt").write_text(
    ",".join(str(b) for b in (b"\x00" * 50 + b"\x04ASII" + b"\x00" * 50)))
args = argparse.Namespace(live=True, shot=None, view_only=False)


def rows(path):
    try:
        with open(path, encoding="utf-8") as f:
            return max(0, sum(1 for _ in f) - 1)          # minus the header
    except OSError:
        return 0


# ---- 1. who writes: never this window when the dialog asked to record ------
assert app_mod.chart_writes(args, want_record=True) is False
assert app_mod.chart_writes(args, want_record=False) is True
assert app_mod.chart_writes(argparse.Namespace(live=True, shot=None, view_only=True), False) is False
assert app_mod.chart_writes(argparse.Namespace(live=True, shot="x.png", view_only=False), False) is False
print("PASS: the Start dialog's record leaves writing to the recorder, never the chart")

# ---- 2. feeds wait for start_live(), so they carry the sink ---------------
S = QtCore.QSettings("orderflow-test", "recording")
S.clear()
capture.release_lock()
win = app_mod.MainWindow({}, "time", 60, ["ASII"], live=True,
                         persist=app_mod.chart_writes(args, False), settings=S)
assert not win.feeds, "no feed may start before start_live() decides who writes"
win.show()
win.start_live()
assert win.feeds and all(th.sink is win._sink and win._sink is not None
                         for th in win.feeds.values()), "every feed must carry the sink"
print("PASS: feeds start in start_live() and all carry the window's sink")

# ---- 3. rows land on disk, end to end, exactly once across reconnects -----
m = win.model_for("ASII", "time", 60)
deadline = time.monotonic() + 15
while len(m.trades) < N and time.monotonic() < deadline:
    app.processEvents()
    time.sleep(0.02)
assert len(m.trades) == N, "the chart saw %d of %d trades" % (len(m.trades), N)
win.close()                                   # stops + joins feeds, closes the sink
app.processEvents()
on_disk = rows(TRADES_CSV)
assert on_disk == N, "trades.csv holds %d rows, expected %d" % (on_disk, N)
print("PASS: %d trades through the real feed -> %d rows in trades.csv (replays deduped)"
      % (N, on_disk))
capture.release_lock()

# ---- 4. the dialog path leaves the lock for the recorder ------------------
QtCore.QProcess.startDetached = staticmethod(lambda *a, **k: (True, 4242))
w2 = app_mod.MainWindow({}, "time", 60, ["ASII"], live=True,
                        persist=app_mod.chart_writes(args, True), settings=S)
w2._start_daemon()
w2.start_live()
assert not capture.writer_status()[0], "the chart claimed the lock the recorder needs"
assert w2._sink is None and not w2._holds_lock
w2.close()
print("PASS: with record ticked the chart claims nothing; the recorder's lock is free")

# ---- 5. a refusal is written where the app can read it, and shown ---------
capture.take_lock(["ASII"], "chart")
before = CAPTURE_LOG.read_text(encoding="utf-8") if CAPTURE_LOG.exists() else ""
r = subprocess.run([sys.executable, "-m", "orderflow.capture", "ASII"],
                   capture_output=True, text=True, timeout=60)
log = CAPTURE_LOG.read_text(encoding="utf-8") if CAPTURE_LOG.exists() else ""
assert r.returncode == 3, r.returncode
assert "refusing to start" in log[len(before):], "the refusal must reach capture.log"
capture.release_lock()
print("PASS: the recorder's refusal reaches capture.log, not only a lost stderr")

w3 = app_mod.MainWindow({}, "time", 60, ["ASII"], live=False, settings=S)
w3._rec_asked_t = time.monotonic() - 10       # Record pressed 10 s ago, nothing holds the lock
w3._refresh_session()
assert "refusing to start" in w3.status_lbl.text(), w3.status_lbl.text()
w3.close()
print("PASS: the window says why: %r" % w3.status_lbl.text().strip()[:72])

S.clear()
print()
print("ALL PASS")
