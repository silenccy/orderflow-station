"""Workstation: vap_for_bars, model registry + GC, link groups, roster round-trip."""
import os, sys, json
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtWidgets
from orderflow import model as of_model
from orderflow import app as of_app
from orderflow import panels as of_panels

app = QtWidgets.QApplication(sys.argv)

# a throwaway, EMPTY settings scope: MainWindow would otherwise use the real
# config, and a roster saved by one run would leak into the next
SETTINGS = QtCore.QSettings("orderflow-test", "of_app_test")
SETTINGS.clear()
SETTINGS.sync()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _synth import BASE, events_for  # noqa: E402

EVENTS = {"ASII": events_for("ASII"), "BBCA": events_for("BBCA", px=9200)}

# ---------------------------------------------------------------- 1. vap_for_bars
m = of_model.build_model(EVENTS["ASII"], "time", 60)
ids = m.bar_ids()
assert ids, "no bars built"
full = m.vap_for_bars(ids)
sess = m.vap_rows()
assert sum(r[3] for r in full) == sum(r[3] for r in sess), "all-bars profile != session"
assert {r[0] for r in full} == {r[0] for r in sess}, "price levels differ"
half = m.vap_for_bars(ids[: len(ids) // 2])
rest = m.vap_for_bars(ids[len(ids) // 2:])
assert abs(sum(r[3] for r in half) + sum(r[3] for r in rest)
           - sum(r[3] for r in full)) < 1e-6, "subsets do not partition the total"
assert sum(r[3] for r in half) < sum(r[3] for r in full), "subset should be smaller"
print("PASS: vap_for_bars partitions the session profile exactly")

# ---------------------------------------------------------------- 2. window boots
win = of_app.MainWindow(EVENTS, "time", 60, ["ASII", "BBCA"], debug=False, live=False, settings=SETTINGS)
win.show(); app.processEvents()
kinds = sorted(p.kind for p in win.panels)
assert len(win.panels) == 8, "default roster should be 8 panels, got %d" % len(win.panels)
assert win.centralWidget() is None, "central widget must be gone (all-dock layout)"
print("PASS: default layout =", kinds)

# ---------------------------------------------------------------- 3. model registry
a = win.model_for("ASII", "time", 60)
b = win.model_for("ASII", "time", 60)
assert a is b, "same key must return the same model instance"
c = win.model_for("ASII", "time", 300)
assert c is not a, "different bar size must be a different model"
assert len(c.bar_ids()) < len(a.bar_ids()), "300s bars should be fewer than 60s"
before = len(win.models)
win._gc_models()
assert len(win.models) < before, "GC should drop models no panel is bound to"
assert all(k in {p.model_key() for p in win.panels if p.wants_model}
           for k in win.models), "GC left an unreferenced model"
print("PASS: registry caches by key and GCs unreferenced models (%d -> %d)"
      % (before, len(win.models)))

# ---------------------------------------------------------------- 4. link groups
fp_a = next(p for p in win.panels if p.kind == "footprint")
vap_a = next(p for p in win.panels if p.kind == "vap")
assert fp_a.group == vap_a.group == "A"
fp_b = win.add_panel("footprint", group="B")
win._relink()
assert fp_b.model_key()[0] == "BBCA", "group B should have seeded to the 2nd symbol"
win.set_group_spec("A", symbol="ASII", bar_size=120)
assert fp_a.model_key() == ("ASII", "time", 120), fp_a.model_key()
assert vap_a.model_key() == ("ASII", "time", 120), "linked panel did not follow its group"
assert fp_b.model_key() == ("BBCA", "time", 60), "group B must not have moved"
print("PASS: group A retargets its members; group B unaffected")

# unlinked panel keeps a private source
fp_c = win.add_panel("footprint", group=None, spec={"symbol": "BBCA", "bar_size": 30})
win.set_group_spec("A", bar_size=90)
assert fp_c.model_key() == ("BBCA", "time", 30), "unlinked panel followed a group"
print("PASS: unlinked (grey) panel keeps its own source")

# ---------------------------------------------------------------- 5. crosshair scope
seen = []
for p in win.panels:
    if isinstance(p, of_panels.ChartPanel):
        p.set_cross = (lambda pp: (lambda x, y: seen.append((pp.kind, pp.group, x))))(p)
app.processEvents()
win.broadcast_cross(fp_a, 5.0, 4800.0)
groups = {g for _k, g, _x in seen}
assert groups == {"A"}, "crosshair leaked outside group A: %s" % groups
print("PASS: crosshair stays inside its link group")

# ---------------------------------------------------------------- 6. roster round-trip
win._save_roster()
blob = win.settings.value("panels/roster")
data = json.loads(blob)
assert len(data["panels"]) == len(win.panels)
expect = [(p.kind, p.group, p.model_key()) for p in win.panels]
win2 = of_app.MainWindow(EVENTS, "time", 60, ["ASII", "BBCA"], debug=False, live=False, settings=SETTINGS)
win2.show(); app.processEvents()
got = [(p.kind, p.group, p.model_key()) for p in win2.panels]
assert got == expect, "roster round-trip differs:\n  %s\n  %s" % (expect, got)
print("PASS: roster round-trip preserved %d panels, groups and sources" % len(got))

# ---------------------------------------------------------------- 7. panels menu
win2._rebuild_panels_menu()
labels = [a.text() for a in win2.panels_menu.actions() if a.text()]
assert any("Add panel" == a.title() for a in
           [x.menu() for x in win2.panels_menu.actions() if x.menu()]), "no Add panel menu"
assert "Reset to default layout" in labels, labels
toggles = [a for a in win2.panels_menu.actions() if a.isCheckable()]
assert len(toggles) == len(win2.panels), "menu must list every panel"
assert all(a.isChecked() for a in toggles), "all panels should start visible"
# hiding a panel must uncheck its action with no manual sync
p0 = win2.panels[0]
p0.hide()
assert not toggles[0].isChecked(), "toggleViewAction did not follow the panel"
p0.show()
assert toggles[0].isChecked()
print("PASS: Panels menu lists %d panels, toggles track visibility" % len(toggles))

# ---------------------------------------------------------------- 8. hidden panels skipped
calls = []
for p in win2.panels:
    p.refresh = (lambda pp: (lambda: calls.append(pp.kind)))(p)
win2.panels[1].hide(); app.processEvents()
calls.clear()
win2.refresh()
assert win2.panels[1].kind not in calls or calls.count(win2.panels[1].kind) == 0, \
    "hidden panel was refreshed"
print("PASS: refresh skips hidden panels")

print("\nALL PASS")
