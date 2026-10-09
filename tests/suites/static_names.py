"""No function may use a name that does not exist.

FootprintItem.paint used `sc` -- a local of a different method, _generate -- from
2026-09-06 to 2026-10-09. It only ran once the footprint was zoomed in far
enough to draw numbers in its cells, so nothing caught it, and a NameError
escaping a paint() is not a Python error in this app: PySide turns it into a
native access violation in QtCore.pyd that takes the window down with no
traceback. It did, on 2026-09-08 and twice on 2026-10-09, and the first one was
misdiagnosed for a month.

An undefined name is found in milliseconds without running anything. This is
pyflakes' core check, built on the standard library so CI needs no new package:
for every scope in every module, a name read as a global must be defined at
module level (assigned, imported, a def or class, or set via `global`) or be a
builtin.
"""
import builtins
import pathlib
import symtable
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
TARGETS = sorted((ROOT / "orderflow").glob("*.py")) + sorted((ROOT / "tools").glob("*.py"))
BUILTINS = set(dir(builtins)) | {"__file__", "__name__", "__doc__", "__spec__",
                                 "__package__", "__loader__", "__builtins__",
                                 "__path__", "__annotations__", "__dict__"}


def walk(table):
    yield table
    for child in table.get_children():
        yield from walk(child)


def module_defs(top):
    names = {s.get_name() for s in top.get_symbols()
             if s.is_assigned() or s.is_imported() or s.is_namespace()}
    for t in walk(top):                      # `global x` then `x = ...` inside a function
        for s in t.get_symbols():
            if s.is_declared_global() and s.is_assigned():
                names.add(s.get_name())
    return names


def undefined(path):
    src = path.read_text(encoding="utf-8")
    top = symtable.symtable(src, str(path), "exec")
    defined = module_defs(top) | BUILTINS
    bad = []
    for t in walk(top):
        for s in t.get_symbols():
            if not s.is_referenced():
                continue
            name = s.get_name()
            if t is top:
                if not (s.is_assigned() or s.is_imported() or s.is_namespace()) \
                        and name not in defined:
                    bad.append((t.get_lineno(), "<module>", name))
            elif s.is_global() and name not in defined:
                bad.append((t.get_lineno(), t.get_name(), name))
    return bad


problems = []
for f in TARGETS:
    for line, scope, name in undefined(f):
        problems.append("%s:%d in %s(): '%s' is not defined"
                        % (f.relative_to(ROOT), line, scope, name))

# the check must be able to see the bug it exists for, or it proves nothing
probe = "def a():\n    sc = 1\n    return sc\ndef paint():\n    return sc['x']\n"
pt = symtable.symtable(probe, "<probe>", "exec")
seen = [s.get_name() for t in walk(pt) if t.get_name() == "paint"
        for s in t.get_symbols() if s.is_global() and s.get_name() not in module_defs(pt)]
assert seen == ["sc"], "the checker failed to flag the original bug: %s" % seen
print("PASS: the checker flags a FootprintItem-style borrowed local")

if problems:
    print("\n".join(problems))
    sys.exit("FAIL: %d undefined name(s)" % len(problems))
print("PASS: %d modules, no undefined names" % len(TARGETS))
print()
print("ALL PASS")
