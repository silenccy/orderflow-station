"""Failures must be visible even with no console. This is the bug that made every
problem look like 'nothing happens'."""
import _safety  # noqa: F401 -- keep first: refuses to run against the real archive
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

# ---- 6. [today]: what the session left on disk, for the post-close paste ---
# A fixed past date, so neither the clock nor the crash.log lines written above
# (dated today) can leak into the counts.
from orderflow.paths import BOOK_CSV, CAPTURE_LOG, GAPS_CSV, TRADES_CSV  # noqa: E402

DAY, OTHER = "2026-01-15", "2026-01-14"


def write(path, header, rows):
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + "\n" + "".join(r + "\n" for r in rows))


write(TRADES_CSV, "recv_time,symbol,trade_time,price,qty,lots,value,trade_id,flag",
      ["%sT09:0%d:00,ASII,x,4800,100,1,0,%d,1" % (DAY, i, i) for i in range(3)]
      + ["%sT09:1%d:00,BUMI,x,230,100,1,0,%d,1" % (DAY, i, 10 + i) for i in range(2)]
      + ["%sT09:0%d:00,ASII,x,4800,100,1,0,%d,1" % (OTHER, i, 20 + i) for i in range(4)])
write(BOOK_CSV, "recv_time,symbol,side,price,freq,value",
      ["%sT09:00:0%d,ASII,BID,4795,3,300" % (DAY, i) for i in range(5)]
      + ["%sT09:00:0%d,BUMI,BID,229,3,300" % (OTHER, i) for i in range(2)])
write(GAPS_CSV, "recv_time,symbol,kind,started,ended,seconds,attempts,detail",
      ["%sT11:00:00,ASII,disconnect,a,b,41.5,2,ConnectionClosed" % DAY,
       "%sT11:00:00,ASII,disconnect,a,b,9.0,1,x" % OTHER])
def armed(stamp, pid):
    """The header exactly as install() writes it -- including the explanatory
    line that quotes 'Windows fatal exception', which once fooled the count."""
    return ("=" * 72 + "\n[%s] faulthandler armed (pid %d)\n" % (stamp, pid)
            + "  any 'Windows fatal exception' / 'Fatal Python error' block below\n"
            + "  belongs to THIS run until the next armed line.\n" + "-" * 72 + "\n")


with open(CRASH, "a", encoding="utf-8") as f:
    f.write(armed(OTHER + "T08:58:00", 1)
            + "Windows fatal exception: access violation\n"
            + armed(DAY + "T08:59:00", 2)
            + "Windows fatal exception: access violation\n"
            + "\n"                                   # dumps contain blank lines
            + "Windows fatal exception: access violation\n"   # and can report twice
            + "[%sT10:00:00] unhandled exception in Boom.paint (pid 2)\n" % DAY
            + armed(DAY + "T10:30:00", 3))           # a run that did NOT crash
CAPTURE_LOG.write_text("".join("[1%d:00:00] line %d\n" % (i % 10, i) for i in range(8)),
                       encoding="utf-8")

t = diag.recorded_today(DAY)
assert t["trades"] == {"ASII": 3, "BUMI": 2}, t["trades"]
assert t["book"] == {"ASII": 5}, t["book"]
assert t["gaps"] == 1 and abs(t["gap_sec"] - 41.5) < 1e-9, (t["gaps"], t["gap_sec"])
assert t["launches"] == 2, "two runs armed on %s, got %d" % (DAY, t["launches"])
assert t["crashed_runs"] == 1, \
    "one run crashed that day (its dump reports twice, across a blank line): %d" % t["crashed_runs"]
assert t["errors"] == 1, t["errors"]
assert len(t["capture_tail"]) == 6 and t["capture_tail"][-1].endswith("line 7")
assert "[today]" in diag.doctor(), "doctor() must print the [today] section"
print("PASS: [today] counts rows per symbol, gaps, launches and crashes for one day")

print("\nALL PASS")
