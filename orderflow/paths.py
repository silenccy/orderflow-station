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

# session auth (SECRET — never commit)
SUBSCRIBE_AUTO = DATA_DIR / "subscribe_auto.txt"   # written by the token grabber
PW_PROFILE = DATA_DIR / "pw_profile"               # Playwright login profile
GRAB_CONFIG = DATA_DIR / "grab_config.json"        # saved ticker URL
