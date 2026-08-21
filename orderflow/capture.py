"""
capture.py — headless IDX capture daemon, and the writer-lock every writer shares.

Run it yourself, or let the app start and stop it (Session ▸ Start recording).

    python -m orderflow.capture                 # default symbol
    python -m orderflow.capture ASII BBCA TLKM  # several at once
    python -m orderflow.capture --status        # is anything recording?
    python -m orderflow.capture --stop          # ask it to shut down cleanly

MULTI-SYMBOL: one process opens one websocket per symbol and funnels them all
through a SINGLE shared CsvSink. The archive is shared by design (every row
carries a `symbol` column and readers filter on it) but must have exactly one
writer: two would interleave rows mid-snapshot and replay_feed's groupby, which
only groups adjacent rows, would silently shred the book into fragments that
still parse.

That rule used to be a README warning. It is now enforced by a lock file:

  * data/capture.lock  — whoever is writing holds it, and refreshes its MTIME
    every couple of seconds. That heartbeat is the liveness test. A pid probe is
    NOT usable here: on Windows os.kill with any signal other than
    CTRL_C_EVENT/CTRL_BREAK_EVENT terminates the target, so asking "is the daemon
    alive?" would kill it.
  * data/capture.stop  — create it to request a clean shutdown. The writer
    notices, closes its CsvSink properly and removes the lock. Nothing is ever
    killed mid-write.

NOTE: the subscribe frame holds a ~24h session token; it is never printed/logged.
"""

import asyncio
import contextlib
import json
import os
import sys
import time
from datetime import datetime

from . import feed as of_feed
from .paths import CAPTURE_LOCK, CAPTURE_LOG, CAPTURE_STOP

# Log lines contain em-dashes; Windows consoles default to cp1252 and would
# mangle them (backtest.py does the same for its arrow labels).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):      # None under pythonw, or a pipe
        pass

SYMBOL = "ASII"
HEARTBEAT_SEC = 2.0        # how often the writer refreshes the lock's mtime
STALE_AFTER_SEC = 10.0     # an older heartbeat means the writer died
SUMMARY_EVERY_SEC = 30.0   # periodic counts into capture.log
LOG_MAX_BYTES = 256_000


# ============================================================
#  Writer lock — liveness by heartbeat, shutdown by flag file
# ============================================================
def writer_status():
    """(alive, info) for whoever is currently writing the archive.

    Liveness is the lock file's mtime, refreshed by the holder. Deliberately not
    a pid probe — see the module docstring for why os.kill is unusable here."""
    try:
        age = time.time() - CAPTURE_LOCK.stat().st_mtime
    except OSError:
        return False, {}
    try:
        info = json.loads(CAPTURE_LOCK.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        info = {}
    info["age"] = age
    return age <= STALE_AFTER_SEC, info


def take_lock(symbols, owner):
    """Claim the archive. Callers must check writer_status() first."""
    try:
        CAPTURE_LOCK.write_text(json.dumps({
            "pid": os.getpid(), "owner": owner, "symbols": list(symbols),
            "started": time.time()}), encoding="utf-8")
        return True
    except OSError:
        return False


def touch_lock():
    """Heartbeat. Call at least every STALE_AFTER_SEC while writing."""
    try:
        CAPTURE_LOCK.touch()
    except OSError:
        pass


def release_lock():
    try:
        CAPTURE_LOCK.unlink()
    except OSError:
        pass


def request_stop():
    """Ask the current writer to shut down cleanly (see stop_requested)."""
    try:
        CAPTURE_STOP.write_text(str(time.time()), encoding="utf-8")
        return True
    except OSError:
        return False


def clear_stop():
    try:
        CAPTURE_STOP.unlink()
    except OSError:
        pass


def stop_requested():
    return CAPTURE_STOP.exists()


def describe_status():
    """One plain-English line for the GUI and --status."""
    alive, info = writer_status()
    if not alive:
        return "Not recording"
    syms = ", ".join(info.get("symbols") or []) or "?"
    return "Recording %s (%s, pid %s)" % (syms, info.get("owner", "?"),
                                          info.get("pid", "?"))


# ============================================================
#  Logging — a file the GUI can tail instead of you watching a terminal
# ============================================================
def log(msg, err=False, to_file=True):
    line = "[%s] %s" % (datetime.now().strftime("%H:%M:%S"), msg)
    stream = sys.stderr if err else sys.stdout
    if stream is not None:          # None under pythonw.exe (no console)
        try:
            print(line, file=stream, flush=True)
        except (OSError, ValueError):
            pass
    if not to_file:
        return
    try:
        if CAPTURE_LOG.exists() and CAPTURE_LOG.stat().st_size > LOG_MAX_BYTES:
            tail = CAPTURE_LOG.read_text(encoding="utf-8",
                                         errors="replace").splitlines()[-200:]
            CAPTURE_LOG.write_text("\n".join(tail) + "\n", encoding="utf-8")
        with open(CAPTURE_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def tail_log(n=12):
    try:
        return CAPTURE_LOG.read_text(encoding="utf-8",
                                     errors="replace").splitlines()[-n:]
    except OSError:
        return []


# ============================================================
#  Capture
# ============================================================
async def _pump(symbol, sink, tag, stat):
    """Stream one symbol into the shared sink. Never raises: a symbol that dies
    (dropped socket, bad frame) must not take the other symbols down with it."""
    try:
        async for ev in of_feed.live_feed(symbol, persist=True, sink=sink):
            kind = ev[0]
            if kind == "status":
                log(tag + ev[1])
            elif kind == "book":
                stat["book"] += 1
                _, sym, side, levels, ts = ev
                top = levels[0][0] if levels else "-"
                # per-frame lines are console-only: book frames arrive constantly
                # and writing every one to capture.log would thrash the disk
                log("%s %-5s %2d lvls  top=%s" % (sym, side, len(levels), top),
                    to_file=False)
            elif kind == "gap":
                g = ev[1]
                stat["gap"] += 1
                log("%sGAP %s for %.1fs (%s) - that tape is gone"
                    % (tag, g.get("kind"), g.get("seconds") or 0.0,
                       g.get("detail") or "no detail"), err=True)
            elif kind == "trade":
                stat["trade"] += 1
                r = ev[1]
                log("TRADE %s %s x %ssh (id %s)"
                    % (r["symbol"], of_feed._num(r["price"]),
                       of_feed._num(r["qty"]), r["id"]), to_file=False)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        log("%sstream failed (%s: %s)" % (tag, type(e).__name__, e), err=True)
    # There is no reconnect: feed.live_feed ends when the socket drops. Say so
    # loudly — an open window is NOT evidence the capture is still running.
    log("%sstream ENDED — %s is no longer being captured" % (tag, symbol), err=True)


async def _watchdog(symbols, stats, stop_evt):
    """Heartbeat the lock, watch for a stop request, log periodic counts."""
    last_summary = time.time()
    while True:
        await asyncio.sleep(HEARTBEAT_SEC)
        touch_lock()
        if stop_requested():
            log("stop requested — shutting down cleanly")
            stop_evt.set()
            return
        if time.time() - last_summary >= SUMMARY_EVERY_SEC:
            last_summary = time.time()
            log("  ".join("%s %db/%dt%s"
                          % (s, stats[s]["book"], stats[s]["trade"],
                             "/%dgap" % stats[s]["gap"] if stats[s]["gap"] else "")
                          for s in symbols))


async def _run(symbols):
    log("capturing %s -> %s" % (", ".join(symbols), of_feed.BOOK_CSV))
    take_lock(symbols, "daemon")
    sink = of_feed.CsvSink()                 # ONE writer for every symbol
    stats = {s: {"book": 0, "trade": 0, "gap": 0} for s in symbols}
    tags = {s: ("%s: " % s if len(symbols) > 1 else "") for s in symbols}
    stop_evt = asyncio.Event()
    pumps = asyncio.gather(*(_pump(s, sink, tags[s], stats[s]) for s in symbols))
    wd = asyncio.ensure_future(_watchdog(symbols, stats, stop_evt))
    stopper = asyncio.ensure_future(stop_evt.wait())
    try:
        # finish when a stop is requested OR when every stream has died
        await asyncio.wait({pumps, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in (pumps, wd, stopper):
            t.cancel()
        for t in (pumps, wd, stopper):
            with contextlib.suppress(BaseException):
                await t
        sink.close()
        release_lock()
        clear_stop()
        log("capture stopped")


USAGE = """usage: orderflow-capture [SYMBOL ...] [--status] [--stop]

  SYMBOL     4-letter IDX ticker(s), default {sym}
             several are captured concurrently into one archive
  --status   report whether anything is recording, then exit
  --stop     ask the current writer to shut down cleanly, then exit

Needs a subscribe_*.txt in data/ — grab one from the browser console
(see README 'Get a session token', or use the app's Get token button).
One frame covers every symbol; the ticker is swapped in software.
""".format(sym=SYMBOL)


def main():
    """CLI entry point: orderflow-capture [SYMBOL ...]"""
    flags = {a for a in sys.argv[1:] if a.startswith("-")}
    if flags & {"--help", "-h"}:
        print(USAGE)
        return
    if "--status" in flags:
        print(describe_status())
        return
    if "--stop" in flags:
        if not writer_status()[0]:
            print("nothing is recording")
            return
        request_stop()
        for _ in range(50):                  # give it up to ~10s to wind down
            time.sleep(0.2)
            if not writer_status()[0]:
                print("stopped")
                return
        print("stop requested, but it is still holding the lock", file=sys.stderr)
        sys.exit(1)

    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    symbols = [a.upper() for a in argv] or [SYMBOL]
    bad = [s for s in symbols if len(s) != 4 or not s.isalpha()]
    if bad:
        print("not 4-letter tickers: %s" % ", ".join(bad), file=sys.stderr)
        sys.exit(2)
    symbols = list(dict.fromkeys(symbols))   # dedupe, keep order

    alive, info = writer_status()
    if alive:                                # the one-writer rule, enforced
        print("refusing to start: %s is already recording %s (pid %s).\n"
              "Stop it first with:  orderflow-capture --stop"
              % (info.get("owner", "another writer"),
                 ", ".join(info.get("symbols") or []) or "?", info.get("pid", "?")),
              file=sys.stderr)
        sys.exit(3)
    clear_stop()                             # a leftover flag would stop us instantly
    try:
        asyncio.run(_run(symbols))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
