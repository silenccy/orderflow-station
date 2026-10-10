"""The replay buffer is bounded, and a native crash lands under a dated header.

Two things that bit this project:

  * self.events was never trimmed. Measured on the 2026-08-31 archive a liquid
    symbol costs ~36 MB per hour, so a 6.5 h session is ~233 MB EACH. Book
    frames are 92 % of that at ~5.5 KB apiece; a whole session of trades is
    8.7 MB. Nothing shows an old book frame — the DOM shows the newest, the
    heatmap keeps its own window — but the footprint, CVD, VAP and tape need
    every trade. So books are capped and trades are not.
  * faulthandler writes its dump with no timestamp. On 2026-09-08 that left an
    undated blob and the crash could only be placed in time from the Windows
    event log.
"""
import _safety  # noqa: F401 -- keep first: refuses to run against the real archive
import os
import subprocess
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod
from orderflow.paths import DATA_DIR


class _Sig:
    def connect(self, *a, **kw): pass
    def disconnect(self, *a, **kw): pass


class StubFeed:
    def __init__(self, symbol, sink, **kw):
        self.symbol, self.sink = symbol, sink
        self.batch, self.status = _Sig(), _Sig()

    def start(self): pass
    def stop(self): pass
    def wait(self, ms=0): return True


app_mod.FeedThread = StubFeed
S = QtCore.QSettings("orderflow-test", "retention")
S.clear()


def book(sym, px):
    return ("book", sym, "bid", [(px, 3, 3000.0)], "2026-10-03T09:00:00")


def trade(sym, px):
    return ("trade", {"symbol": sym, "price": px, "qty": 100.0, "value": px * 100,
                      "id": px, "flag": None, "sec": 0, "ns": 0,
                      "recv_iso": "2026-10-03T09:00:00",
                      "trade_time": "2026-10-03T09:00:00"})


win = app_mod.MainWindow({}, "time", 60, ["ASII"], live=True, persist=False,
                         settings=S)
win.show()
CAP = 500
win.cfg["book_buffer"] = CAP
assert "ASII" in win._charted_symbols(), win._charted_symbols()

# ---- 1. books are capped, trades are not --------------------------------
for i in range(3000):
    win._on_live_batch("ASII", [book("ASII", 100.0 + i)])
    if i % 3 == 0:
        win._on_live_batch("ASII", [trade("ASII", 100.0 + i)])

buf = win.events["ASII"]
books = [e for e in buf if e[0] == "book"]
trades = [e for e in buf if e[0] == "trade"]
assert len(books) <= CAP * 1.25 + 1, "book frames unbounded: %d" % len(books)
assert len(trades) == 1000, "trades must never be dropped, kept %d" % len(trades)
print("PASS: 3000 books -> %d kept (cap %d); all %d trades kept"
      % (len(books), CAP, len(trades)))

# ---- 2. it is the OLDEST books that go ----------------------------------
newest = books[-1][3][0][0]
oldest = books[0][3][0][0]
assert newest == 100.0 + 2999, newest
assert oldest > 100.0, "trimming must drop from the front, kept %s" % oldest
print("PASS: dropped from the front — oldest kept is %.0f, newest %.0f"
      % (oldest, newest))

# ---- 3. order is preserved (replay depends on it) ------------------------
pxs = [e[3][0][0] for e in books]
assert pxs == sorted(pxs), "trim must not reorder the buffer"
print("PASS: chronological order preserved")

# ---- 4. the trim is visible, not silent ----------------------------------
assert win._books_trimmed > 0, "trimming must be counted"
txt = win._refresh_diag()
assert "books_trimmed" in (txt or ""), txt
print("PASS: --debug reports books_trimmed=%d" % win._books_trimmed)

# ---- 5. an uncharted symbol still buffers nothing at all -----------------
win.add_to_watchlist("BBCA")
for i in range(50):
    win._on_live_batch("BBCA", [book("BBCA", 500.0 + i), trade("BBCA", 500.0 + i)])
assert not win.events.get("BBCA"), "watched-only symbols hold no buffer"
assert win._derived["BBCA"]["trades"] == 50, win._derived["BBCA"]
print("PASS: watched-but-uncharted still holds 0 events, stats intact")

# ---- 6. memory actually falls ---------------------------------------------
import sys as _s


def deep(o, seen=None):
    if seen is None: seen = set()
    if id(o) in seen: return 0
    seen.add(id(o))
    n = _s.getsizeof(o)
    if isinstance(o, dict):
        for k, v in o.items(): n += deep(k, seen) + deep(v, seen)
    elif isinstance(o, (list, tuple, set)):
        for v in o: n += deep(v, seen)
    return n


held = deep(win.events["ASII"])
untrimmed = held + deep(books[0]) * win._books_trimmed
assert untrimmed > held * 2, (held, untrimmed)
print("PASS: buffer holds %.1f MB; untrimmed it would be ~%.1f MB"
      % (held / 1e6, untrimmed / 1e6))

# ---- 7. the crash log gets a dated header --------------------------------
log = DATA_DIR / "crash.log"
# Cannot unlink it: this process armed faulthandler on import and still holds
# the handle open (that is the point of keeping it open for the process life).
# Read only what the child appends.
before = log.stat().st_size if log.exists() else 0
code = (
    "from orderflow import diagnostics;"
    "diagnostics.show_dialogs = False;"
    "diagnostics.install();"
    "print('armed')"
)
subprocess.run([sys.executable, "-c", code], check=True,
               capture_output=True, text=True)
with open(log, "r", encoding="utf-8") as fh:
    fh.seek(before)
    text = fh.read()
assert "faulthandler armed" in text, text[:400]
import re
assert re.search(r"\[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\] faulthandler armed", text), \
    "the header must carry an ISO timestamp:\n%s" % text[:400]
assert "pid" in text, text[:400]
print("PASS: crash.log opens with a dated, pid-stamped header")

S.clear()
print()
print("ALL PASS")
