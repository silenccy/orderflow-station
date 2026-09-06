"""Settings: the new colour kind, and that colours reach the pixels.

The volume profile used to hardcode its RGB, so it silently ignored the palette
the footprint beside it was using. The brush assertions below are the check that
would have caught that.
"""
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtGui, QtWidgets
from orderflow import app as of_app, panels as of_panels
from orderflow.items import (DEFAULTS, HELP_BY_KEY, SETTINGS_SPEC, SPEC_BY_KEY,
                             SettingsDialog, side_colors)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _synth import events_for  # noqa: E402

app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

BLUE, AMBER = "#3aa0ff", "#ffb03a"

# ---- 1. spec tolerates 4- and 5-tuples ----------------------------------
lens = {len(row) for _t, items in SETTINGS_SPEC for row in items}
assert lens <= {4, 5}, lens
assert len(SPEC_BY_KEY) == len(DEFAULTS), (len(SPEC_BY_KEY), len(DEFAULTS))
assert HELP_BY_KEY["buy_color"], "buy_color should carry help text"
print("PASS: spec has %s-length rows, all %d keys mapped" % (sorted(lens), len(SPEC_BY_KEY)))

# ---- 2. derived shades track the base ------------------------------------
d = side_colors(DEFAULTS)
assert len(set(c.name() for c in d.values())) == 6, "six distinct colours expected"
b = side_colors({"buy_color": BLUE, "sell_color": AMBER})
assert b["buy"].name() == BLUE and b["sell"].name() == AMBER
# a blue base must derive blue-ish shades: blue channel dominant, not green
assert b["buy_fill"].blue() > b["buy_fill"].green(), b["buy_fill"].name()
assert b["buy_edge"].blue() > b["buy_edge"].green(), b["buy_edge"].name()
print("PASS: blue base -> blue-ish derived shades (%s / %s)"
      % (b["buy_fill"].name(), b["buy_edge"].name()))

# an invalid colour falls back instead of raising
bad = side_colors({"buy_color": "not-a-colour", "sell_color": ""})
assert bad["buy"].isValid() and bad["sell"].isValid()
print("PASS: an invalid colour string falls back to the default")

# ---- 3. the dialog round-trips a colour ---------------------------------
captured = {}
dlg = SettingsDialog(dict(DEFAULTS), lambda v: captured.update(v))
w, kind = dlg.widgets["buy_color"]
assert kind == "color", kind
assert w.value() == DEFAULTS["buy_color"], w.value()
w._color = QtGui.QColor(BLUE); w._paint()
dlg._apply()
assert captured["buy_color"] == BLUE, captured["buy_color"]
print("PASS: colour edited in the dialog reaches on_apply as %s" % captured["buy_color"])

# ---- 4. search filters, reset restores ----------------------------------
dlg.search.setText("buy side")
# isHidden(), not isVisible(): an unshown dialog makes every child "not visible"
# regardless of the filter, so isVisible() would pass or fail for the wrong reason
vis = [lab.isHidden() for _i, k, _l, lab, _f in dlg._rows if k == "buy_color"]
hidden = [lab.isHidden() for _i, k, _l, lab, _f in dlg._rows if k == "colormap"]
assert vis == [False], "matching row should stay shown, got hidden=%s" % vis
assert hidden == [True], "non-matching rows should hide, got hidden=%s" % hidden
dlg.search.setText("")
print("PASS: search shows the matching row and hides the rest")

dlg.nav.setCurrentRow(0)
dlg._reset_page()
assert dlg.widgets["buy_color"][0].value() == DEFAULTS["buy_color"]
print("PASS: Reset page restores defaults")

# ---- 5. the profile's brush actually follows the colour ------------------
S = QtCore.QSettings("orderflow-test", "settings"); S.clear(); S.sync()
win = of_app.MainWindow({"ASII": events_for("ASII")}, "time", 60, ["ASII"],
                        live=False, settings=S)
app.processEvents()
vap = next((p for p in win.panels if p.kind == "vap"), None) or win.add_panel("vap", group="A")
vap.setVisible(True)

def brushes():
    vap.refresh(); app.processEvents()
    out = []
    for it in vap.p.items:
        if isinstance(it, of_panels.pg.BarGraphItem):
            out.append(it.opts.get("brush"))
    return [QtGui.QColor(b.color()).name() if hasattr(b, "color") else QtGui.QColor(b).name()
            for b in out if b is not None]

before = brushes()
assert DEFAULTS["buy_color"] in before, (DEFAULTS["buy_color"], before)
win.cfg["buy_color"], win.cfg["sell_color"] = BLUE, AMBER
after = brushes()
assert BLUE in after and AMBER in after, (BLUE, AMBER, after)
assert DEFAULTS["buy_color"] not in after, "old colour still painted: %s" % after
print("PASS: volume-profile bars repainted %s -> %s" % (before[:2], after[:2]))

S.clear()
print("\nALL PASS")
