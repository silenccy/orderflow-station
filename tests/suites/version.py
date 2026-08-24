"""Version discipline: one source of truth, valid semver, documented, reachable.

The version used to be written in two places -- pyproject.toml and
orderflow/__init__.py -- which drift the moment someone bumps one and forgets
the other. pyproject now derives it from the package, and this suite proves the
derivation actually holds in an installed environment.
"""
import importlib.metadata as md
import re
import subprocess
import sys
from pathlib import Path

import orderflow

ROOT = Path(__file__).resolve().parents[2]
V = orderflow.__version__

# ---- 1. valid semver ------------------------------------------------------
assert re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?", V), \
    "%r is not semantic versioning" % V
print("PASS: __version__ %s is valid semver" % V)

# ---- 2. the build metadata agrees with the package -----------------------
installed = md.version("orderflow-station")
assert installed == V, "drift: package says %s, installed metadata says %s" % (V, installed)
print("PASS: installed metadata matches __version__ (%s)" % installed)

# ---- 3. pyproject does not hardcode a second copy -------------------------
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
assert 'dynamic = ["version"]' in pyproject, "pyproject should derive the version"
hard = re.search(r'^version\s*=\s*"', pyproject, re.M)
assert hard is None, "pyproject hardcodes a version again -- that is the drift bug"
print("PASS: pyproject derives the version instead of copying it")

# ---- 4. every entry point can report it -----------------------------------
for mod in ("orderflow.app", "orderflow.capture", "orderflow.backtest"):
    out = subprocess.run([sys.executable, "-m", mod, "--version"],
                         capture_output=True, text=True, timeout=120)
    text = (out.stdout + out.stderr).strip()
    assert V in text, "%s --version said %r" % (mod, text)
print("PASS: app, capture and backtest all report %s" % V)

# ---- 5. the release is documented ----------------------------------------
changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
assert ("[%s]" % V) in changelog or ("## %s" % V) in changelog, \
    "CHANGELOG.md has no section for %s -- ship notes, not just code" % V
print("PASS: CHANGELOG.md documents %s" % V)

print("\nALL PASS")
