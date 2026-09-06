"""Every setting must be reachable, defaulted, and actually wired to something.

Three ways the settings surface rots, all silent:
  - a key in DEFAULTS but not in SETTINGS_SPEC is unreachable from the UI
  - a key in SETTINGS_SPEC but not in DEFAULTS raises KeyError when the dialog opens
  - a key nothing reads is a control that appears to do something and does not

The third one is the reason this suite exists: `vap_mode` sat in the dialog for a
while labelled "Vol@price range (new panels)" while no code read it.
"""
import os, pathlib, re, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from orderflow.settings import DEFAULTS, HELP_BY_KEY, SETTINGS_SPEC, SPEC_BY_KEY

ROOT = pathlib.Path(__file__).resolve().parents[2] / "orderflow"

# ---- 1. no duplicates, no orphans either way -----------------------------
keys = [row[0] for _tab, items in SETTINGS_SPEC for row in items]
dupes = sorted({k for k in keys if keys.count(k) > 1})
assert not dupes, "duplicate setting rows: %s" % dupes

unreachable = sorted(set(DEFAULTS) - set(keys))
keyerror = sorted(set(keys) - set(DEFAULTS))
assert not unreachable, "in DEFAULTS but not editable: %s" % unreachable
assert not keyerror, "in the dialog but not in DEFAULTS (KeyError on open): %s" % keyerror
print("PASS: %d settings, all editable and all defaulted" % len(keys))

assert len(SPEC_BY_KEY) == len(DEFAULTS), (len(SPEC_BY_KEY), len(DEFAULTS))
print("PASS: SPEC_BY_KEY covers every key")

# ---- 2. every setting is consumed somewhere ------------------------------
items_src = (ROOT / "settings.py").read_text(encoding="utf-8")
decl = items_src[items_src.index("DEFAULTS = {"):items_src.index("SPEC_BY_KEY")]
sources = []
for f in sorted(ROOT.glob("*.py")):
    text = f.read_text(encoding="utf-8")
    if f.name == "settings.py":
        text = text.replace(decl, "")     # declaring a key is not using it
    sources.append((f.name, text))

dead = []
for key in sorted(DEFAULTS):
    quoted = re.compile("[\"']" + re.escape(key) + "[\"']")
    if not any(quoted.search(t) for _n, t in sources):
        dead.append(key)
assert not dead, "editable in the UI but wired to nothing: %s" % dead
print("PASS: every setting is read somewhere in the package")

# ---- 3. nothing reads a cfg key that has no default ----------------------
missing = []
pat = re.compile(r"""\bcfg\[\s*["'](\w+)["']\s*\]""")
for name, text in sources:
    for m in pat.finditer(text):
        if m.group(1) not in DEFAULTS:
            missing.append("%s: cfg[%r]" % (name, m.group(1)))
assert not missing, "cfg read with no default -> KeyError at runtime: %s" % missing
print("PASS: no cfg read lacks a default")

# ---- 4. spec rows are well formed ---------------------------------------
KINDS = {"bool", "int", "double", "choice", "color"}
for _tab, rows in SETTINGS_SPEC:
    for row in rows:
        assert 4 <= len(row) <= 5, row
        key, label, kind, spec = row[0], row[1], row[2], row[3]
        assert kind in KINDS, "unknown kind %r for %s" % (kind, key)
        assert label and not label.endswith(" "), row
        if kind in ("int", "double"):
            assert spec and len(spec) >= 2 and spec[0] <= spec[1], row
            val = DEFAULTS[key]
            assert spec[0] <= val <= spec[1], (
                "default %r for %s is outside its own range %s" % (val, key, spec))
        elif kind == "choice":
            assert spec and DEFAULTS[key] in spec, (
                "default %r for %s is not one of %s" % (DEFAULTS[key], key, spec))
        elif kind == "color":
            assert isinstance(DEFAULTS[key], str) and DEFAULTS[key].startswith("#"), row
print("PASS: every row is well formed and every default is inside its own range")

# ---- 5. help coverage ----------------------------------------------------
nohelp = sorted(k for k in keys if not HELP_BY_KEY.get(k))
assert all(k.startswith("show_") for k in nohelp), (
    "these need help text, their labels do not explain them: %s"
    % [k for k in nohelp if not k.startswith("show_")])
print("PASS: %d of %d have help; the rest are self-explanatory show_* toggles"
      % (len(keys) - len(nohelp), len(keys)))

print("\nALL PASS")
