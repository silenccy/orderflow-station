"""Imported first by every suite: refuse to run against the real archive.

Suites write into DATA_DIR -- recording.py writes a fake subscribe frame,
diagnostics.py truncates crash.log and overwrites trades.csv and book.csv.
Through tests/run_all.py (or pytest) each suite gets a throwaway ORDERFLOW_DATA.
Run directly as a script, DATA_DIR would be the real data/ folder, which holds
your session token and your archive. So without a throwaway folder, stop here,
before orderflow is even imported.
"""
import os
import sys
from pathlib import Path

_REPO_DATA = (Path(__file__).resolve().parents[2] / "data").resolve()
_target = os.environ.get("ORDERFLOW_DATA")

if not _target or Path(_target).resolve() == _REPO_DATA:
    name = Path(sys.argv[0]).stem or "this suite"
    sys.exit(
        "refusing to run %s against the real data/ folder -- suites write into it "
        "(token, archive, logs).\n"
        "Run it through the runner, which gives it a throwaway folder:\n"
        "    python tests/run_all.py %s\n"
        "or set ORDERFLOW_DATA to an empty scratch directory first." % (name, name))
