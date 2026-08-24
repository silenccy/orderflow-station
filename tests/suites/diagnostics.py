"""Failures must be visible even with no console. This is the bug that made every
problem look like 'nothing happens'."""
import sys
import time

from PySide6 import QtCore, QtWidgets

qapp = QtWidgets.QApplication(sys.argv)

from orderflow import app as app_mod
from orderflow import diagnostics as diag
from orderflow import feed as of_feed

CRASH, LAUNCH = diag.CRASH_LOG, diag.LAUNCH_LOG
# truncate rather than unlink: importing app ran install(), which holds crash.log
# open for faulthandler for the life of the process
for f in (CRASH, LAUNCH):
    f.write_text("", encoding="utf-8")

diag.show_dialogs = False        # no human here to dismiss a modal box

# ---- 1. no console: the process adopts real streams ------------------------
real_out, real_err = sys.stdout, sys.stderr
sys.stdout = sys.stderr = None          # exactly what pythonw.exe hands us
diag._installed = False                 # allow a re-install for the test
diag._stream_fp = None
diag.install()
adopted = (sys.stdout is not None and sys.stderr is not None)
print_worked = False
try:
    print("a stray print that would otherwise vanish")
    print_worked = True
except Exception:
    pass
sys.stdout, sys.stderr = real_out, real_err

assert adopted, "install() must give a console-less process real streams"
assert LAUNCH.exists(), "launch.log must be created when there is no console"
body = LAUNCH.read_text(encoding="utf-8")
assert "launch (pid" in body, body
assert "a stray print" in body, "stray output must land in the log"
print("PASS: no console -> streams adopted, output captured in launch.log")

# ---- 2. an unhandled exception is recorded, not lost -----------------------
try:
    raise ValueError("deliberate startup failure")
except ValueError:
    sys.stdout = sys.stderr = None       # handler must survive None streams
    try:
        diag._excepthook(*sys.exc_info())
    finally:
        sys.stdout, sys.stderr = real_out, real_err

txt = CRASH.read_text(encoding="utf-8")
assert "deliberate startup failure" in txt, txt
assert "ValueError" in txt and "Traceback" in txt
assert "main thread" in txt
print("PASS: unhandled exception written to crash.log with a full traceback")

# ---- 3. a crashing thread is recorded too ---------------------------------
class Args:
    exc_type, exc_value, exc_traceback, thread = None, None, None, None

try:
    raise RuntimeError("thread blew up")
except RuntimeError as e:
    Args.exc_type, Args.exc_value, Args.exc_traceback = type(e), e, e.__traceback__
    Args.thread = type("T", (), {"name": "worker-1"})()
diag._thread_excepthook(Args)
txt = CRASH.read_text(encoding="utf-8")
assert "thread blew up" in txt and "thread worker-1" in txt
print("PASS: thread exception recorded with the thread name")

# ---- 4. a feed with no token reports instead of dying silently ------------
async def broken_feed(symbol, persist=True, sink=None, **kw):
    raise FileNotFoundError("No parseable subscribe_*.txt in data")
    yield  # pragma: no cover - makes this an async generator

of_feed.live_feed = broken_feed
seen = []
th = app_mod.FeedThread("ASII", None)
th.status.connect(lambda sym, msg: seen.append((sym, msg)))
th.start()
deadline = time.time() + 10
while th.isRunning() and time.time() < deadline:
    qapp.processEvents()
    time.sleep(0.02)
th.wait(3000)
qapp.processEvents()

assert seen, "a dead feed must say something — it used to vanish into a None stderr"
sym, msg = seen[-1]
assert sym == "ASII", seen
assert "token" in msg.lower(), "expected a plain-English cause, got %r" % msg
print("PASS: feed with no token reports %r" % msg)
txt = CRASH.read_text(encoding="utf-8")
assert "feed thread ASII" in txt, "and it is recorded for later"
print("PASS: and the traceback landed in crash.log")

# ---- 5. doctor covers what you need when it will not start ---------------
rep = diag.doctor()
for want in ("[interpreter]", "[paths]", "[session]", "[qt]", "DATA_DIR",
             "platform plugin", "token", "startup/remember"):
    assert want in rep, "doctor() missing %r" % want
print("PASS: doctor() reports interpreter, paths, session, qt and saved startup")

print("\nALL PASS")
