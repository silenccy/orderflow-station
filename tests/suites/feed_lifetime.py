"""Feed threads must be JOINED before their last reference goes away.

FeedThread.stop() only *requests* cancellation (call_soon_threadsafe) and returns
immediately, so the thread is still inside run() when stop() comes back. If the
only Python reference is dropped at that moment, the C++ QThread destructor runs
on a live thread and Windows kills the process with an access violation inside
QtCore.pyd -- no Python traceback, just a dead window.

That is exactly what happened on 2026-09-08: two crashes, same fault bucket,
faulting module QtCore.pyd, whenever a symbol left the on-screen set.
"""
import sys
import threading
import time

from PySide6 import QtCore, QtWidgets

app = QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod

SETTINGS = QtCore.QSettings("orderflow-test", "feed_lifetime")
SETTINGS.clear()


class SlowFeed(QtCore.QThread):
    """A feed that, like the real one, keeps running briefly after stop()."""
    batch = QtCore.Signal(str, list)
    status = QtCore.Signal(str, str)
    live = []                       # strong refs so the test itself cannot crash

    def __init__(self, symbol, sink, **kw):
        super().__init__()
        self.symbol, self.sink = symbol, sink
        self._cancel = threading.Event()
        SlowFeed.live.append(self)

    def run(self):
        self._cancel.wait(5.0)
        time.sleep(0.25)            # the tail of _pump after the task is cancelled

    def stop(self):                 # request only -- mirrors the real stop()
        self._cancel.set()


RealFeed = app_mod.FeedThread       # kept for section 4
app_mod.FeedThread = SlowFeed

win = app_mod.MainWindow({}, "time", 60, ["ASII"], live=True, persist=False,
                         settings=SETTINGS)
win.start_live()            # feeds start here, never during construction (3.2.2)

# ---- 1. a symbol leaving the wanted set must be joined, not just asked to stop
win._wanted_symbols = lambda: ["ASII", "BBCA"]
win._sync_feeds()
assert set(win.feeds) == {"ASII", "BBCA"}, list(win.feeds)
dropped = win.feeds["BBCA"]
assert dropped.isRunning(), "stub feed should be running before removal"

win._wanted_symbols = lambda: ["ASII"]
win._sync_feeds()
assert "BBCA" not in win.feeds, list(win.feeds)
assert not dropped.isRunning(), (
    "_sync_feeds dropped a still-running QThread: its last reference dies with "
    "the statement, so the C++ destructor runs on a live thread -> access "
    "violation in QtCore.pyd")
print("PASS: removed feed was joined before its reference was dropped")

# ---- 2. the surviving feed is untouched ----------------------------------
assert win.feeds["ASII"].isRunning(), "the wanted symbol must keep streaming"
print("PASS: surviving feed still running")

# ---- 3. shutdown joins everything ----------------------------------------
win.close()
for th in SlowFeed.live:
    th.stop()
    th.wait(3000)
    assert not th.isRunning(), "%s outlived shutdown" % th.symbol
print("PASS: every feed joined at shutdown")

# ---- 4. stop() in the first instant after start() is honoured -------------
# It used to cancel only if the thread's loop was already running and silently
# did nothing otherwise; the thread ran on and Qt aborted at exit with
# "QThread: Destroyed while thread is still running". Real FeedThread here,
# with a websocket that drops at once so the feed sits in its reconnect loop.
import websockets  # noqa: E402
from orderflow import feed as of_feed  # noqa: E402

app_mod.FeedThread = RealFeed   # the real class again


class _DropWS:
    subprotocol = "web"
    async def send(self, d): pass
    async def recv(self): raise websockets.ConnectionClosed(None, None)


class _DropConnect:
    def __init__(self, *a, **k): pass
    async def __aenter__(self): return _DropWS()
    async def __aexit__(self, *a): return False


of_feed.websockets.connect = _DropConnect
finished = 0
for _ in range(25):
    th = RealFeed("ASII", None, reconnect=True)
    th.start()
    th.stop()                                    # before its loop can exist
    finished += bool(th.wait(3000))
assert finished == 25, "stop() right after start() was ignored %d of 25 times" % (25 - finished)
print("PASS: stop() immediately after start() honoured 25/25 times")

SETTINGS.clear()
print()
print("ALL PASS")
