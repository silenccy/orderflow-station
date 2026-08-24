"""Session wiring: the app must never become a second writer, and must never
leave a lock behind. This is the --view-only flag you used to have to remember."""
import os
import sys

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod
from orderflow import capture as cap
from orderflow import startup as st
from orderflow.paths import CAPTURE_LOCK, DATA_DIR


class _Sig:                         # stands in for a Qt signal
    def connect(self, *a, **kw): pass
    def disconnect(self, *a, **kw): pass


class StubFeed:                     # never open a real websocket in a test
    def __init__(self, symbol, sink, **kw):   # **kw: reconnect/backoff_max etc.
        self.symbol, self.sink = symbol, sink
        self.batch, self.status = _Sig(), _Sig()

    def start(self): pass
    def stop(self): pass
    def wait(self, ms=0): return True


app_mod.FeedThread = StubFeed
SETTINGS = QtCore.QSettings("orderflow-test", "session")


def win(persist):
    w = app_mod.MainWindow({}, "time", 60, ["ASII"], live=True, persist=persist,
                           settings=SETTINGS)
    return w


print("archive:", DATA_DIR)
cap.release_lock(); cap.clear_stop()

# ---- 1. free archive -> the chart takes the lock and writes ----------------
w = win(True)
w.start_live()
alive, info = cap.writer_status()
assert alive and info["owner"] == "chart", info
assert info["pid"] == os.getpid()
assert w._sink is not None and w._persist is True
print("PASS: free archive -> chart records,", cap.describe_status())

# ---- 2. closing releases the lock -----------------------------------------
w.close()
assert not cap.writer_status()[0], "close must release the lock"
assert not CAPTURE_LOCK.exists()
print("PASS: closing the window released the lock")

# ---- 3. someone else is recording -> chart goes view-only, does NOT write --
cap.take_lock(["ASII", "BBCA"], "daemon")       # pretend a daemon owns it
w2 = win(True)
w2.start_live()
assert w2._persist is False, "chart must not write while a daemon holds the lock"
assert w2._sink is None, "chart must not open a second CsvSink"
alive, info = cap.writer_status()
assert info["owner"] == "daemon", "the daemon's lock must be left intact"
print("PASS: daemon recording -> chart forced view-only, daemon lock untouched")
print("      status line:", w2.status_lbl.text().strip())

# ---- 4. a foreign lock must survive our close ------------------------------
w2.close()
alive, info = cap.writer_status()
assert alive and info["owner"] == "daemon", "we must not release someone else's lock"
print("PASS: closing a view-only window left the daemon's lock alone")
cap.release_lock()

# ---- 5. _release_writer hands the archive back ----------------------------
w3 = win(True)
w3.start_live()
assert cap.writer_status()[0] and w3._sink is not None
w3._release_writer()
assert not cap.writer_status()[0], "release_writer must drop the lock"
assert w3._sink is None and w3._persist is False
print("PASS: _release_writer closed the sink and dropped the lock")
w3.close()

# ---- 6. StartDialog output matches what main() consumes -------------------
d = st.StartDialog({"mode": "live", "symbols": ["ASII"], "size": 30,
                    "bars": "tick", "history": "all", "debug": True})
v = d.values()
for key in ("mode", "symbols", "bars", "size", "history", "record", "debug"):
    assert key in v, "main() reads %r" % key
assert v["bars"] == "tick" and v["size"] == 30 and v["history"] == "all"
assert v["debug"] is True
print("PASS: StartDialog returns every key main() reads:", sorted(v))

SETTINGS.clear()
print("\nALL PASS")
