"""
paths.py — every file the toolkit reads or writes, in one place.

All captured data, session frames and the browser profile live under a single
DATA_DIR (repo-local `data/` by default, gitignored). Override with the
ORDERFLOW_DATA environment variable to keep the archive elsewhere:

    set ORDERFLOW_DATA=E:\\idx-archive          (Windows)
    export ORDERFLOW_DATA=/mnt/idx-archive      (POSIX)

Nothing here is committed: `data/` is gitignored precisely because the subscribe
frames contain a live account token.
"""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("ORDERFLOW_DATA") or (REPO_ROOT / "data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# captured market data
BOOK_CSV = str(DATA_DIR / "book.csv")
TRADES_CSV = str(DATA_DIR / "trades.csv")
SUMMARY_CSV = str(DATA_DIR / "summary.csv")
RAW_LOG = str(DATA_DIR / "capture_raw.jsonl")
# every hole in the tape: when the feed dropped, for how long, and why.
# Recorded rather than inferred, so replay and the backtest work from fact.
GAPS_CSV = str(DATA_DIR / "gaps.csv")

# capture coordination — exactly one process may write the CSVs.
# capture.lock's MTIME is the heartbeat (see capture.writer_status: a pid probe
# is unusable on Windows, where os.kill with any ordinary signal terminates the
# target). capture.stop is a cooperative shutdown request, so the writer closes
# its CsvSink cleanly instead of being killed mid-write.
CAPTURE_LOCK = DATA_DIR / "capture.lock"
CAPTURE_STOP = DATA_DIR / "capture.stop"
CAPTURE_LOG = DATA_DIR / "capture.log"

# session auth (SECRET — never commit)
# subscribe_*.txt lives here too: captured by hand from the browser console
# (see README "Get a session token"). feed.load_subscribe_frame() finds it.
