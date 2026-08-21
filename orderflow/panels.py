"""
panels.py — every workstation widget as an independently dockable Panel.

Each panel owns its own pg.PlotWidget (or table) and its own refresh, so the
same kind can be instantiated several times on different symbols and bar bases.
A panel resolves its data through a LINK GROUP: panels sharing a group colour
share symbol, bar basis, crosshair and x-range; an unlinked (grey) panel keeps a
private source. The host (MainWindow) owns the model registry and answers
`model_for(symbol, kind, size)`.

Drawing code is the same as the old single-window build — it moved off
MainWindow onto the panel that owns it, bodies intact.
"""

import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets

from . import model as of_model
from .items import (BEAR, BULL, CommaAxis, DeltaFooterItem, DepthBarDelegate,
                    FootprintItem, HeatmapCandleItem, SmoothImageItem)

GROUPS = ["A", "B", "C"]
GROUP_COLOR = {None: "#5f6b76", "A": "#ff5454", "B": "#5ad1ff", "C": "#3fe26a"}
PANEL_TYPES = {}          # kind -> class, filled by @register


def register(cls):
    PANEL_TYPES[cls.kind] = cls
    return cls


# ============================================================
#  Dock title bar (a custom one loses Qt's buttons, so re-add them)
# ============================================================
class PanelTitleBar(QtWidgets.QWidget):
    """Title, link-group chip, per-panel source controls, float + close."""

    def __init__(self, panel):
        super().__init__(panel)
        self.panel = panel
        self._loading = False
        self.setStyleSheet("QWidget{background:#12161a;} QLabel{color:#c8ccd0;}"
                           "QToolButton{border:none; color:#9aa0a6;}"
                           "QToolButton:hover{color:#e0e0e0;}")
        lay = QtWidgets.QHBoxLayout(self)
        lay.setContentsMargins(6, 1, 2, 1)
        lay.setSpacing(4)

        self.chip = QtWidgets.QToolButton()
        self.chip.setAutoRaise(True)
        self.chip.setToolTip("Link group — panels sharing a colour share symbol, bar\n"
                             "basis, crosshair and x-range. Click to cycle.")
        self.chip.clicked.connect(self._cycle_group)
        lay.addWidget(self.chip)

        self.label = QtWidgets.QLabel(panel.title)
        f = self.label.font()
        f.setPointSize(8)
        self.label.setFont(f)
        lay.addWidget(self.label)

        self.sym = self.kind = self.size = None
        if panel.can_source:
            self.sym = QtWidgets.QComboBox()
            self.sym.setFixedWidth(74)
            self.sym.setToolTip("Symbol for this panel (or its whole link group)")
            self.sym.currentTextChanged.connect(self._src_changed)
            lay.addWidget(self.sym)
            self.kind = QtWidgets.QComboBox()
            self.kind.addItems(["time", "tick", "volume"])
            self.kind.setFixedWidth(74)
            self.kind.currentTextChanged.connect(self._src_changed)
            lay.addWidget(self.kind)
            self.size = QtWidgets.QSpinBox()
            self.size.setRange(1, 100000)
            self.size.setFixedWidth(66)
            self.size.editingFinished.connect(self._src_changed)
            lay.addWidget(self.size)

        for w in panel.title_extras():
            lay.addWidget(w)

        lay.addStretch(1)
        self.note = QtWidgets.QLabel("")     # crosshair / status readout
        self.note.setStyleSheet("color:#7f8792; font-family:Consolas; font-size:10px;")
        lay.addWidget(self.note)

        for text, tip, slot in (("⧉", "Float / dock", self._toggle_float),
                                ("✕", "Close panel", panel.close)):
            b = QtWidgets.QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setAutoRaise(True)
            b.clicked.connect(slot)
            lay.addWidget(b)

    def _toggle_float(self):
        self.panel.setFloating(not self.panel.isFloating())

    def _cycle_group(self):
        order = [None] + GROUPS
        nxt = order[(order.index(self.panel.group) + 1) % len(order)]
        self.panel.set_group(nxt)

    def _src_changed(self, *_):
        if self._loading:
            return
        self.panel.set_source(symbol=self.sym.currentText() or None,
                              bar_kind=self.kind.currentText(),
                              bar_size=self.size.value())

    def sync(self):
        """Push the panel's current group/source into the widgets."""
        self._loading = True
        try:
            col = GROUP_COLOR[self.panel.group]
            self.chip.setText("●")
            self.chip.setStyleSheet("QToolButton{border:none; color:%s; font-size:14px;}"
                                    % col)
            if self.sym is not None:
                spec = self.panel.spec()
                syms = list(self.panel.host.symbols())
                cur = spec["symbol"]
                if cur and cur not in syms:
                    syms = sorted(set(syms) | {cur})
                if [self.sym.itemText(i) for i in range(self.sym.count())] != syms:
                    self.sym.clear()
                    self.sym.addItems(syms)
                self.sym.setCurrentText(cur or "")
                self.kind.setCurrentText(spec["bar_kind"])
                self.size.setValue(int(spec["bar_size"]))
        finally:
            self._loading = False

    def set_note(self, txt):
        self.note.setText(txt)


# ============================================================
#  Base panel
# ============================================================
class Panel(QtWidgets.QDockWidget):
    kind = "panel"
    title = "Panel"
    wants_model = True        # False -> not bound to a (symbol, bars) model
    can_source = False        # True -> symbol/bar controls in the title bar

    def __init__(self, host, uid, group="A"):
        super().__init__()
        self.host = host
        self.uid = uid
        self.group = group
        self._spec = dict(host.group_spec(group or GROUPS[0]))
        self.setObjectName("%s#%d" % (self.kind, uid))
        F = QtWidgets.QDockWidget.DockWidgetFeature
        self.setFeatures(F.DockWidgetMovable | F.DockWidgetFloatable
                         | F.DockWidgetClosable)
        self.setWidget(self.build())
        self.titlebar = PanelTitleBar(self)
        self.setTitleBarWidget(self.titlebar)
        self.titlebar.sync()
        # a panel tabbed behind another is skipped by refresh(); repaint it the
        # moment it comes forward so switching tabs never shows a stale chart
        self.visibilityChanged.connect(self._on_visibility)

    def _on_visibility(self, visible):
        if visible and getattr(self.host, "panels", None):
            try:
                self.refresh()
            except Exception:
                pass          # a partially-built host during restore must not crash

    # ---- construction hooks ----
    def build(self):
        return QtWidgets.QWidget()

    def title_extras(self):
        return []

    # ---- data source ----
    @property
    def cfg(self):
        return self.host.cfg

    def spec(self):
        if self.group:
            return self.host.group_spec(self.group)
        return self._spec

    def model_key(self):
        s = self.spec()
        return (s["symbol"], s["bar_kind"], int(s["bar_size"]))

    def model(self):
        if not self.wants_model:
            return None
        if not self.spec()["symbol"]:
            return None
        return self.host.model_for(*self.model_key())

    def set_group(self, name):
        self.group = name
        if name is None:                      # unlinking keeps the source it had
            self._spec = dict(self.host.group_spec(GROUPS[0]))
        self.titlebar.sync()
        self.host.on_panel_source_changed(self)

    def set_source(self, symbol=None, bar_kind=None, bar_size=None):
        """Edit this panel's source — or its whole group, if it is linked."""
        upd = {k: v for k, v in (("symbol", symbol), ("bar_kind", bar_kind),
                                 ("bar_size", bar_size)) if v is not None}
        if not upd:
            return
        if self.group:
            self.host.set_group_spec(self.group, **upd)
        else:
            self._spec.update(upd)
            self.host.on_panel_source_changed(self)

    # ---- persistence ----
    def state(self):
        return {"kind": self.kind, "uid": self.uid, "group": self.group,
                "spec": dict(self._spec)}

    def apply_state(self, d):
        self._spec.update(d.get("spec") or {})
        self.titlebar.sync()

    # ---- redraw ----
    def refresh(self):
        pass

    def closeEvent(self, ev):
        super().closeEvent(ev)
        self.host.on_panel_closed(self)


# ============================================================
#  Chart panel: pg.PlotWidget + shared crosshair plumbing
# ============================================================
class ChartPanel(Panel):
    cross_x = False           # accepts a bar-index crosshair from its group
    cross_y = False           # accepts a price crosshair from its group

    def _make_plot(self, **kw):
        pw = pg.PlotWidget(**kw)
        self.pw = pw
        self.p = pw.getPlotItem()
        return pw

    def _init_cross(self):
        pen = pg.mkPen("#6d7680", width=1, style=QtCore.Qt.PenStyle.DashLine)
        self._cv = pg.InfiniteLine(angle=90, pen=pen)
        self._ch = pg.InfiniteLine(angle=0, pen=pen)
        for ln in (self._cv, self._ch):
            ln.setVisible(False)
            ln.setZValue(50)
            self.p.addItem(ln, ignoreBounds=True)
        self._proxy = pg.SignalProxy(self.p.scene().sigMouseMoved, rateLimit=45,
                                     slot=self._mouse_moved)

    def _mouse_moved(self, evt):
        pos = evt[0]
        if not self.p.sceneBoundingRect().contains(pos):
            self.host.broadcast_cross(self, None, None)
            return
        pt = self.p.vb.mapSceneToView(pos)
        self.host.broadcast_cross(self, pt.x(), pt.y())

    def set_cross(self, x, y):
        """x = bar index, y = price. A panel takes only the axis it shares."""
        if not hasattr(self, "_cv"):
            return
        self._cv.setVisible(bool(self.cross_x and x is not None))
        self._ch.setVisible(bool(self.cross_y and y is not None))
        if self.cross_x and x is not None:
            self._cv.setPos(x)
        if self.cross_y and y is not None:
            self._ch.setPos(y)


# ============================================================
#  Footprint
# ============================================================
@register
class FootprintPanel(ChartPanel):
    kind = "footprint"
    title = "Footprint"
    can_source = True
    cross_x = cross_y = True

    def build(self):
        pw = self._make_plot()
        self.p.showGrid(x=False, y=True, alpha=0.2)
        self.p.getAxis("left").setWidth(54)
        self.fp_item = FootprintItem()
        self.p.addItem(self.fp_item)
        self.p.vb.sigRangeChangedManually.connect(self._manual)

        dot = QtCore.Qt.PenStyle.DotLine
        self.vwap_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#5ad1ff", width=1, style=dot),
                                         label="VWAP {value:.0f}",
                                         labelOpts={"position": 0.04, "color": "#5ad1ff"})
        self.vah_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#9aa0a6", width=1, style=dot),
                                        label="VAH {value:.0f}",
                                        labelOpts={"position": 0.04, "color": "#9aa0a6"})
        self.val_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#9aa0a6", width=1, style=dot),
                                        label="VAL {value:.0f}",
                                        labelOpts={"position": 0.12, "color": "#9aa0a6"})
        for ln in (self.vwap_line, self.vah_line, self.val_line):
            ln.setVisible(False)
            self.p.addItem(ln, ignoreBounds=True)

        # measure tool: two clicks — no drag-state machine to get wrong
        self._meas = []
        self._meas_rect = QtWidgets.QGraphicsRectItem()
        self._meas_rect.setPen(pg.mkPen("#e6b450", width=1))
        self._meas_rect.setBrush(pg.mkBrush(230, 180, 80, 40))
        self._meas_rect.setZValue(60)
        self._meas_rect.setVisible(False)
        self.p.vb.addItem(self._meas_rect, ignoreBounds=True)
        self._meas_txt = pg.TextItem(color="#e6b450", anchor=(0, 1))
        self._meas_txt.setZValue(61)
        self._meas_txt.setVisible(False)
        self.p.vb.addItem(self._meas_txt, ignoreBounds=True)
        self.p.scene().sigMouseClicked.connect(self._clicked)

        self._init_cross()
        return pw

    def _manual(self, *_):
        self.host.on_manual_range(self)

    # ---- measure ----
    def _clicked(self, ev):
        if not self.host.measure_active():
            return
        pt = self.p.vb.mapSceneToView(ev.scenePos())
        self._meas.append((pt.x(), pt.y()))
        if len(self._meas) == 1:
            self._meas_rect.setVisible(False)
            self._meas_txt.setVisible(False)
            self.titlebar.set_note("measure: click the second point")
            return
        (x0, y0), (x1, y1) = self._meas[-2:]
        self._meas = []
        self._draw_measure(x0, y0, x1, y1)

    def _draw_measure(self, x0, y0, x1, y1):
        m = self.model()
        xa, xb = sorted((x0, x1))
        ya, yb = sorted((y0, y1))
        self._meas_rect.setRect(QtCore.QRectF(xa, ya, xb - xa, yb - ya))
        self._meas_rect.setVisible(True)
        tick = (m.tick_size() if m else None) or self.fp_item._tickval or 1
        dp = y1 - y0
        pct = (dp / y0 * 100) if y0 else 0.0
        parts = ["%s (%+.0f ticks, %+.2f%%)" % (format(dp, "+,.0f"), dp / tick, pct),
                 "%d bars" % int(round(xb - xa))]
        if m:
            ids = m.bar_ids()
            lo, hi = max(0, int(xa)), min(len(ids) - 1, int(xb))
            if ids and lo <= hi:
                secs = max(m.bar_meta[ids[hi]]["t1"] - m.bar_meta[ids[lo]]["t0"], 0)
                parts.append("%dm%02ds" % (secs // 60, secs % 60))
                unit = 100 if self.cfg["header_units"] == "lots" else 1
                vol = sum(t for pr, _b, _s, t in m.vap_for_bars(ids[lo:hi + 1])
                          if ya <= pr <= yb)
                parts.append("vol %s %s" % (format(vol / unit, ",.0f"),
                                            self.cfg["header_units"]))
        self._meas_txt.setText("   ".join(parts))
        self._meas_txt.setPos(xa, yb)
        self._meas_txt.setVisible(True)
        self.titlebar.set_note("")

    def clear_measure(self):
        self._meas = []
        self._meas_rect.setVisible(False)
        self._meas_txt.setVisible(False)

    # ---- redraw ----
    def refresh(self):
        m = self.model()
        self.fp_item.cfg = self.cfg
        self.fp_item.cell_mode = self.host.cell_mode()
        self.fp_item.set_model(m if m is not None else of_model.OrderflowModel())
        s = self.spec()
        self.titlebar.label.setText("Footprint  %s %s/%s"
                                    % (s["symbol"], s["bar_kind"], s["bar_size"]))
        if m is None:
            return
        c = self.cfg
        vw = m.vwap()
        self.vwap_line.setVisible(c["show_vwap"] and vw is not None)
        if vw is not None:
            self.vwap_line.setPos(vw)
        va = m.value_area(c["va_coverage"] / 100)
        for ln, val in ((self.val_line, va and va[0]), (self.vah_line, va and va[2])):
            ln.setVisible(c["show_va_lines"] and val is not None)
            if val is not None:
                ln.setPos(val)

    def set_cross(self, x, y):
        super().set_cross(x, y)
        m = self.model()
        if x is None or m is None:
            self.titlebar.set_note("")
            return
        ids = m.bar_ids()
        i = int(x)
        if not (0 <= i < len(ids)):
            self.titlebar.set_note("")
            return
        unit = 100 if self.cfg["header_units"] == "lots" else 1
        rows, _poc = m.bar_cells(ids[i])
        tick = m.tick_size() or 1
        cell = next((r for r in rows if abs(r[0] - y) <= tick / 2), None)
        txt = format(y, ",.0f")
        if cell:
            _pr, b, sv, d, t = cell
            txt += "   B %s  S %s  D %s  V %s" % (
                format(b / unit, ",.0f"), format(sv / unit, ",.0f"),
                format(d / unit, "+,.0f"), format(t / unit, ",.0f"))
        self.titlebar.set_note(txt)


# ============================================================
#  Delta footer
# ============================================================
@register
class DeltaPanel(ChartPanel):
    kind = "delta"
    title = "Delta"
    cross_x = True

    def build(self):
        pw = self._make_plot()
        self.p.setYRange(0, 1, padding=0)
        self.p.hideAxis("bottom")
        self.p.getAxis("left").setStyle(showValues=False)
        self.p.getAxis("left").setWidth(54)
        self.p.setMenuEnabled(False)
        self.p.setMouseEnabled(x=True, y=False)
        self.delta_item = DeltaFooterItem()
        self.p.addItem(self.delta_item)
        self._init_cross()
        return pw

    def refresh(self):
        m = self.model()
        self.delta_item.cfg = self.cfg
        self.delta_item.set_model(m if m is not None else of_model.OrderflowModel())


# ============================================================
#  Volume @ Price  (session profile, or the visible range)
# ============================================================
@register
class VapPanel(ChartPanel):
    kind = "vap"
    title = "Vol@Price"
    cross_y = True

    def build(self):
        pw = self._make_plot()
        self.p.getAxis("bottom").setStyle(showValues=False)   # sqrt widths would lie
        self._init_cross()
        return pw

    def title_extras(self):
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(["session", "visible"])
        self.mode.setFixedWidth(78)
        self.mode.setToolTip("session = whole day.  visible = only the bars in view\n"
                             "on the linked footprint, so POC/VA follow your zoom.")
        self.mode.currentTextChanged.connect(lambda *_: self.host.request_refresh())
        return [self.mode]

    def state(self):
        d = super().state()
        d["mode"] = self.mode.currentText()
        return d

    def apply_state(self, d):
        super().apply_state(d)
        if d.get("mode"):
            self.mode.setCurrentText(d["mode"])

    def _rows(self, m):
        """Session rows, or only the bars visible on the group's footprint."""
        if self.mode.currentText() == "session":
            return m.vap_rows(), "session"
        fp = self.host.footprint_for(self)
        if fp is None:
            return m.vap_rows(), "session (no linked footprint)"
        ids = m.bar_ids()
        if not ids:
            return [], "visible"
        (x0, x1), _y = fp.p.viewRange()
        lo = max(0, int(np.floor(x0)))
        hi = min(len(ids) - 1, int(np.ceil(x1)))
        if lo > hi:
            return [], "visible (0 bars)"
        return m.vap_for_bars(ids[lo:hi + 1]), "visible %d bars" % (hi - lo + 1)

    def refresh(self):
        self.p.clear()
        self._cv = self._ch = None
        m = self.model()
        if m is None:
            return
        raw, note = self._rows(m)
        self.titlebar.set_note(note)
        # re-add the crosshair lines: p.clear() dropped them
        self._init_cross()
        if not raw:
            return
        tick = m.tick_size() or 10
        # snap off-tick negotiated prints onto the tick grid — overlapping rows
        # render as "warped" blocks otherwise (a 4,788 print vs the 4,790 row)
        agg = {}
        for pr, b, s, _t in raw:
            g = round(pr / tick) * tick
            e = agg.setdefault(g, [0.0, 0.0])
            e[0] += b
            e[1] += s
        rows = [(pr, bs[0], bs[1], bs[0] + bs[1]) for pr, bs in sorted(agg.items())]
        ys = [r[0] for r in rows]
        h = tick * 0.8
        if self.cfg["vap_scale"] == "sqrt":
            # outlier-proof: a block print stays biggest but stops flattening the rest
            buys = [b ** 0.5 for _p, b, _s, _t in rows]
            sells = [s ** 0.5 for _p, _b, s, _t in rows]
        else:
            buys = [r[1] for r in rows]
            sells = [r[2] for r in rows]
        nopen = pg.mkPen(None)                # no 1px outline on near-zero rows
        self.p.addItem(pg.BarGraphItem(x0=[-s for s in sells], y=ys, height=h,
                                       width=sells, brush=pg.mkBrush(255, 84, 84, 190),
                                       pen=nopen))
        self.p.addItem(pg.BarGraphItem(x0=0, y=ys, height=h, width=buys,
                                       brush=pg.mkBrush(63, 226, 106, 190), pen=nopen))
        ipoc = max(range(len(rows)), key=lambda i: rows[i][3])
        self.p.addItem(pg.BarGraphItem(x0=[-sells[ipoc]], y=[ys[ipoc]], height=h,
                                       width=[sells[ipoc] + buys[ipoc]],
                                       brush=pg.mkBrush(255, 224, 102, 80), pen=nopen))
        # fit the widest bars exactly -> the block can never run off the panel
        self.p.setXRange(-max(max(sells), 1) * 1.08, max(max(buys), 1) * 1.08, padding=0)


# ============================================================
#  Liquidity heatmap
# ============================================================
@register
class HeatmapPanel(ChartPanel):
    kind = "heatmap"
    title = "Liquidity heatmap"
    cross_y = True

    def build(self):
        pw = self._make_plot()
        self.hm_img = SmoothImageItem()          # bilinear smoothing (Bookmap-like)
        self.p.addItem(self.hm_img)
        self._cmap_name = None
        self.hm_price = self.p.plot([], [], pen=pg.mkPen(240, 244, 250, 210, width=2.2))
        dash = QtCore.Qt.PenStyle.DashLine       # biggest resting bid/ask per column
        shadow = pg.mkPen(8, 10, 12, 220, width=3)   # dark under-stroke on bright bands
        self.hm_bidwall = self.p.plot([], [], pen=pg.mkPen(63, 226, 106, 230,
                                                           width=1.4, style=dash),
                                      shadowPen=shadow)
        self.hm_askwall = self.p.plot([], [], pen=pg.mkPen(255, 84, 84, 230,
                                                           width=1.4, style=dash),
                                      shadowPen=shadow)
        self.hm_trades = pg.ScatterPlotItem(pxMode=True, pen=None)
        self.p.addItem(self.hm_trades)
        self.hm_candles = HeatmapCandleItem()    # added last -> draws on top
        self.p.addItem(self.hm_candles)
        self._init_cross()
        return pw

    def set_colormap(self, name):
        """Heatmap LUT. 'bookmap' is the hand-tuned palette; the rest come from
        pyqtgraph/matplotlib, falling back to bookmap if unavailable."""
        if name == self._cmap_name:
            return
        self._cmap_name = name
        lut = None
        if name != "bookmap":
            for src in ("matplotlib", None):
                try:
                    cm = pg.colormap.get(name, source=src) if src else pg.colormap.get(name)
                    lut = cm.getLookupTable(0.0, 1.0, 256)
                    break
                except Exception:
                    lut = None
        if lut is None:
            bm_pos = np.array([0.0, 0.10, 0.28, 0.48, 0.68, 0.85, 1.0])
            bm_col = np.array([[7, 9, 18, 255], [12, 24, 64, 255], [22, 72, 168, 255],
                               [22, 176, 200, 255], [122, 216, 74, 255],
                               [255, 216, 32, 255], [255, 255, 255, 255]], dtype=np.ubyte)
            lut = pg.ColorMap(bm_pos, bm_col).getLookupTable(0.0, 1.0, 256)
        self.hm_img.setLookupTable(lut)

    def _clear(self):
        self.hm_img.clear()
        self.hm_price.setData([], [])
        self.hm_bidwall.setData([], [])
        self.hm_askwall.setData([], [])
        self.hm_trades.setData([])
        self.hm_candles.set_bars([])

    def refresh(self):
        c = self.cfg
        self.set_colormap(c["colormap"])
        m = self.model()
        if m is None:
            self._clear()
            return
        cols = m.heatmap
        if not cols:
            self._clear()
            return
        prices = sorted({p for _e, snap, _mid in cols for p in snap})
        if not prices:
            return
        tick = min((b - a for a, b in zip(prices, prices[1:])), default=5) or 5
        pmin = min(prices)
        nrows = int(round((max(prices) - pmin) / tick)) + 1
        arr = np.zeros((nrows, len(cols)), dtype=float)
        for j, (_e, snap, _mid) in enumerate(cols):
            for p, v in snap.items():
                arr[int(round((p - pmin) / tick)), j] = v
        if c["hm_scale"] == "equalize":
            # Rank-based (histogram equalization): colour = size *percentile*, so the
            # field stays readable whatever the size distribution. gamma darkens the
            # bulk — median level ~ rank 0.5 -> 0.5^y of the palette; only the top few
            # percent reach yellow/white, like Bookmap.
            nz = arr[arr > 0]
            if nz.size:
                order = np.sort(nz)
                ranks = np.searchsorted(order, arr, side="right") / len(order)
                arr = np.where(arr > 0, ranks ** c["hm_gamma"], 0.0)
            hi = 1.0
        else:
            if c["hm_scale"] == "log":
                arr = np.log1p(arr)
            elif c["hm_scale"] == "sqrt":
                arr = np.sqrt(arr)              # compress heavy tails ("linear" = as-is)
            nz = arr[arr > 0]
            hi = float(np.percentile(nz, c["hm_pctile"])) if nz.size else 1.0
        self.hm_img.setImage(arr, autoLevels=False)
        self.hm_img.setLevels([0, hi or 1])
        self.hm_img.setRect(QtCore.QRectF(0, pmin - tick / 2, len(cols), nrows * tick))

        # Overlays (x = heatmap column space): price line, OHLC candles, bubbles
        col_eps = np.array([e for e, _s, _mid in cols])
        xs = np.arange(len(cols))
        e0, e1 = col_eps[0], col_eps[-1]

        if c["show_price_line"]:
            lx = [j for j, (_e, _s, mid) in enumerate(cols) if mid is not None]
            ly = [mid for _e, _s, mid in cols if mid is not None]
            self.hm_price.setData(lx, ly)
        else:
            self.hm_price.setData([], [])

        if c["show_walls"]:
            # price level holding the largest resting size each side of the mid — the
            # gap from the price line to these = your distance to the big orders
            bx, by, ax_, ay = [], [], [], []
            for j, (_e, snap, mid) in enumerate(cols):
                if mid is None or not snap:
                    continue
                bw = aw = None
                bv = av = 0.0
                for p, v0 in snap.items():
                    if p < mid:
                        if v0 > bv:
                            bv, bw = v0, p
                    elif p > mid and v0 > av:
                        av, aw = v0, p
                if bw is not None:
                    bx.append(j)
                    by.append(bw)
                if aw is not None:
                    ax_.append(j)
                    ay.append(aw)
            self.hm_bidwall.setData(bx, by)
            self.hm_askwall.setData(ax_, ay)
        else:
            self.hm_bidwall.setData([], [])
            self.hm_askwall.setData([], [])

        if c["show_hm_candles"] and len(cols) > 1:
            crows = []
            for b in m.bar_ids():
                meta = m.bar_meta.get(b)
                if not meta or meta["t1"] < e0 or meta["t0"] > e1:
                    continue                    # bar outside the rolling window
                cells = m.footprint[b]
                x0 = float(np.interp(meta["t0"], col_eps, xs))
                x1 = float(np.interp(meta["t1"], col_eps, xs))
                if x1 - x0 < 1.0:
                    x1 = x0 + 1.0               # single-print bar still visible
                crows.append((x0, x1, meta["o"], max(cells), min(cells), meta["c"]))
            self.hm_candles.set_bars(crows, tick)
        else:
            self.hm_candles.set_bars([])

        if not c["show_bubbles"]:
            self.hm_trades.setData([])
            return
        win = []
        for r in m.trades[-2000:]:
            ep = of_model.trade_epoch(r)
            if ep is not None and e0 <= ep <= e1:
                win.append((ep, r))
        spots = []
        if win:
            op = c["bubble_opacity"]
            buy_b, sell_b = pg.mkBrush(63, 226, 106, op), pg.mkBrush(255, 84, 84, op)
            qref = float(np.percentile([r["qty"] for _e, r in win],
                                       c["bubble_ref_pct"])) or 1
            minfrac = c["bubble_min_frac"]
            for ep, r in win:
                frac = (r["qty"] / qref) ** 0.5
                if frac < minfrac:              # hide small prints -> less clutter
                    continue
                x = float(np.interp(ep, col_eps, xs))
                spots.append({"pos": (x, r["price"]), "size": min(2 + 11 * frac, 15),
                              "brush": buy_b if r.get("side") == "buy" else sell_b,
                              "pen": None})
        self.hm_trades.setData(spots)


# ============================================================
#  CVD
# ============================================================
@register
class CvdPanel(ChartPanel):
    kind = "cvd"
    title = "CVD"

    def build(self):
        pw = self._make_plot(axisItems={"bottom": pg.DateAxisItem(),
                                        "left": CommaAxis(orientation="left")})
        self.cvd_curve = self.p.plot([], [], pen=pg.mkPen("#2ee6e6", width=2))
        return pw

    def refresh(self):
        m = self.model()
        unit = 100 if self.cfg["header_units"] == "lots" else 1
        self.titlebar.label.setText("CVD (%s)" % self.cfg["header_units"])
        if m is None:
            self.cvd_curve.setData([], [])
            return
        cx = np.asarray(m.cvd_x, dtype=float)
        cy = np.asarray(m.cvd_y, dtype=float) / unit    # match the volume units
        if cx.size > 1:
            # Break the line across capture holes rather than faking a flat run.
            # Recorded gaps are authoritative; the >5-min rule stays as a fallback
            # for archives captured before gaps were logged (and it cannot tell a
            # quiet market from a dropped socket, which is why it is only a
            # fallback now).
            breaks = set(np.where(np.diff(cx) > 300)[0].tolist())
            for gap in getattr(m, "gaps", ()):
                gs = of_model._parse_iso_epoch(gap.get("started"))
                if gs is None:
                    continue
                i = int(np.searchsorted(cx, gs)) - 1
                if 0 <= i < cx.size - 1:
                    breaks.add(i)
            if breaks:
                idx = np.array(sorted(breaks))
                cx = np.insert(cx, idx + 1, cx[idx])
                cy = np.insert(cy, idx + 1, np.nan)
        self.cvd_curve.setData(cx, cy, connect="finite")


# ============================================================
#  Regime
# ============================================================
@register
class RegimePanel(ChartPanel):
    kind = "regime"
    title = "Regime"

    def build(self):
        pw = self._make_plot()
        self.p.setYRange(0, 1, padding=0.03)
        self.p.setMouseEnabled(x=True, y=False)
        self.p.showGrid(x=False, y=True, alpha=0.15)
        self.rg_curve = self.p.plot([], [], pen=pg.mkPen("#e6b450", width=2))
        dot = QtCore.Qt.PenStyle.DotLine
        self.rg_trend_ln = pg.InfiniteLine(angle=0, pen=pg.mkPen("#3fe26a", width=1, style=dot),
                                           label="trend",
                                           labelOpts={"position": 0.14, "color": "#3fe26a"})
        self.rg_chop_ln = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ff5454", width=1, style=dot),
                                          label="chop",
                                          labelOpts={"position": 0.14, "color": "#ff5454"})
        for ln in (self.rg_trend_ln, self.rg_chop_ln):
            self.p.addItem(ln, ignoreBounds=True)
        return pw

    def refresh(self):
        c = self.cfg
        self.rg_trend_ln.setPos(c["er_trend"])
        self.rg_chop_ln.setPos(c["er_chop"])
        m = self.model()
        if m is None:
            self.rg_curve.setData([], [])
            return
        r = m.regime(c["regime_window"], c["regime_warmup_min"], c["er_trend"], c["er_chop"])
        self.last = r
        if not r["ready"]:                       # warm-up countdown
            mins = int(r.get("span", 0) // 60)
            self.rg_curve.setData([], [])
            self.titlebar.set_note("warm-up %d/%dm" % (mins, c["regime_warmup_min"]))
            return
        fg = {"TREND↑": "#4fe27a", "TREND↓": "#ff6a6a",
              "CHOP": "#c9ced4"}.get(r["core"], "#e6b450")
        pts = m.er_series(c["regime_window"])    # ER history
        self.rg_curve.setData([i for i, _e in pts], [e for _i, e in pts])
        self.titlebar.label.setText("Regime  %s" % r["core"])
        self.titlebar.label.setStyleSheet("color:%s; font-weight:600;" % fg)
        self.titlebar.set_note("ER %.2f  v%.0f" % (r["er"], r["rv_pct"]))


# ============================================================
#  Order book (DOM)
# ============================================================
@register
class DomPanel(Panel):
    kind = "dom"
    title = "Order book"

    def build(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self.dom = QtWidgets.QTableWidget(0, 6)
        self.dom.setHorizontalHeaderLabels(["B.Freq", "B.Lot", "Bid", "Ask", "A.Lot", "A.Freq"])
        self.dom.verticalHeader().setVisible(False)
        self.dom.verticalHeader().setDefaultSectionSize(20)   # dense QT-style rows
        self._del_bid = DepthBarDelegate("bid", QtGui.QColor(63, 226, 106), self.dom)
        self._del_ask = DepthBarDelegate("ask", QtGui.QColor(255, 84, 84), self.dom)
        self.dom.setItemDelegateForColumn(1, self._del_bid)
        self.dom.setItemDelegateForColumn(4, self._del_ask)
        self.dom.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.dom.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.dom)
        self.dom_totals = QtWidgets.QLabel("")   # pinned sum footer (never scrolls away)
        self.dom_totals.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.dom_totals.setStyleSheet("font-family:Consolas; font-size:12px; padding:1px 4px;")
        lay.addWidget(self.dom_totals)
        self._dom_mode = None
        return w

    def _smooth(self, attr, val, a=0.15):
        """EMA toward val; keeps DOM depth-bar scaling steady across ticks."""
        prev = getattr(self, attr, val)
        new = (1 - a) * prev + a * val
        setattr(self, attr, new)
        return new or 1

    def _header_key(self, pro):
        return "dom_header_pro" if pro else "dom_header_classic"

    def apply_dom_mode(self):
        """Switch between Stockbit-style side-by-side (classic) and the
        Quantower-style centered ladder (pro); drag-to-resize widths persist
        per mode."""
        st = self.host.settings
        pro = self.cfg["dom_pro"]
        mode = (pro, bool(self.cfg["dom_resizable"]))
        prev = self._dom_mode
        if prev == mode:
            return
        if prev is not None and prev[1]:         # keep the outgoing mode's widths
            st.setValue(self._header_key(prev[0]), self.dom.horizontalHeader().saveState())
        self._dom_mode = mode
        for col in range(6):
            self.dom.setItemDelegateForColumn(col, None)
        if pro:
            self.dom.setColumnCount(5)
            self.dom.setHorizontalHeaderLabels(["Bid", "Price", "Ask", "Chg", "Vol"])
            self.dom.setItemDelegateForColumn(0, self._del_bid)
            self.dom.setItemDelegateForColumn(2, self._del_ask)
        else:
            self.dom.setColumnCount(6)
            self.dom.setHorizontalHeaderLabels(["B.Freq", "B.Lot", "Bid",
                                                "Ask", "A.Lot", "A.Freq"])
            self.dom.setItemDelegateForColumn(1, self._del_bid)
            self.dom.setItemDelegateForColumn(4, self._del_ask)
        self.dom_totals.setVisible(pro)
        hdr = self.dom.horizontalHeader()
        if mode[1]:                              # drag-to-resize, widths persisted
            hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
            hdr.setStretchLastSection(True)
            blob = st.value(self._header_key(pro))
            if isinstance(blob, (QtCore.QByteArray, bytes, bytearray)):
                try:
                    hdr.restoreState(blob)
                except (TypeError, ValueError):
                    pass
        else:                                    # classic auto-fit
            hdr.setStretchLastSection(False)
            hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)

    def save_header(self):
        if self._dom_mode and self._dom_mode[1]:
            self.host.settings.setValue(self._header_key(self._dom_mode[0]),
                                        self.dom.horizontalHeader().saveState())

    def refresh(self):
        self.apply_dom_mode()
        m = self.model()
        if m is None:
            self.dom.setRowCount(0)
            return
        self.titlebar.label.setText("Order book  %s" % self.spec()["symbol"])
        if self.cfg["dom_pro"]:
            self._refresh_pro(m)
        else:
            self._refresh_classic(m)

    def _refresh_pro(self, m):
        """Quantower-style DOM: one centered ladder (asks above the spread row,
        bids below), depth bars, session-volume column, sum totals, BBO lit."""
        c = self.cfg
        rows = m.ladder(c["dom_depth"])
        if not rows:
            self.dom.setRowCount(0)
            return
        bb, ba = m.book.best_bid(), m.book.best_ask()
        spread = m.book.spread()
        blots = [(bv or 0) / 100 for _p, _bf, bv, _af, _av in rows]
        alots = [(av or 0) / 100 for _p, _bf, _bv, _af, av in rows]
        lots = sorted(x for x in blots + alots if x)
        maxlot = self._smooth("_dom_max", lots[-1] if lots else 1)
        wall = c["wall_mult"] * self._smooth("_dom_med", lots[len(lots) // 2] if lots else 0)
        show_bars = c["show_depth_bars"]

        ctr = QtCore.Qt.AlignmentFlag.AlignCenter
        rgt = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        UR = QtCore.Qt.ItemDataRole.UserRole
        bold = QtGui.QFont()
        bold.setBold(True)
        DIMV = QtGui.QColor(140, 152, 168)
        ASK_BG = QtGui.QColor(84, 32, 32)
        BID_BG = QtGui.QColor(24, 66, 40)

        def lot_item(lot):
            it = QtWidgets.QTableWidgetItem(format(lot, ",.0f") if lot else "")
            it.setTextAlignment(rgt)
            if lot and show_bars:
                it.setData(UR, lot / maxlot)
                it.setData(UR + 1, bool(wall and lot >= wall))
            return it

        # liquidity change vs the previous refresh (QT's "+x/-x" column):
        # + = stacking (size added at the level), - = pulling
        prev = getattr(self, "_dom_prev", {})
        cur = {p: v / 100 for p, v in m.book.bids.items()}
        cur.update({p: v / 100 for p, v in m.book.asks.items()})

        self.dom.setRowCount(len(rows))
        for r, (price, bf, bv, af, av) in enumerate(rows):
            is_ask = av is not None
            self.dom.setItem(r, 0, lot_item((bv or 0) / 100))
            pit = QtWidgets.QTableWidgetItem(format(price, ",.0f"))
            pit.setTextAlignment(ctr)
            pit.setForeground(BEAR if is_ask else BULL)
            if price == ba or price == bb:       # best bid & offer stand out
                pit.setFont(bold)
                pit.setBackground(ASK_BG if price == ba else BID_BG)
            self.dom.setItem(r, 1, pit)
            self.dom.setItem(r, 2, lot_item((av or 0) / 100))
            now = cur.get(price, 0)
            chg = now - prev.get(price, now)
            cit = QtWidgets.QTableWidgetItem(format(chg, "+,.0f") if abs(chg) >= 1 else "")
            cit.setTextAlignment(rgt)
            cit.setForeground(BULL if chg > 0 else BEAR)
            self.dom.setItem(r, 3, cit)
            v = m.vap.get(price)
            vt = QtWidgets.QTableWidgetItem(
                format((v["buy"] + v["sell"]) / 100, ",.0f") if v else "")
            vt.setTextAlignment(rgt)
            vt.setForeground(DIMV)
            self.dom.setItem(r, 4, vt)
        self._dom_prev = cur

        tb, ta = sum(blots), sum(alots)
        tv = sum((m.vap[p]["buy"] + m.vap[p]["sell"]) / 100
                 for p, _bf, _bv, _af, _av in rows if p in m.vap)
        bid_pct = 100 * tb / (tb + ta) if (tb + ta) else 50
        self.dom_totals.setText(
            "<span style='color:#5f6b76'>&Sigma;&nbsp;</span>"
            "<span style='color:#3fe26a;font-weight:600'>B %s</span>"
            "<span style='color:#5f6b76'> / </span>"
            "<span style='color:#ff5454;font-weight:600'>A %s</span>"
            "<span style='color:#5f6b76'> lots &nbsp;&middot;&nbsp; %.0f%% bid "
            "&nbsp;&middot;&nbsp; vol %s</span>"
            % (format(tb, ",.0f"), format(ta, ",.0f"), bid_pct, format(tv, ",.0f")))

        last = m.trades[-1]["price"] if m.trades else None
        if last is not None and spread is not None:
            self.titlebar.set_note("Last %s  Spread %s"
                                   % (format(last, ",.0f"), format(spread, ",.0f")))

        # keep the BBO in view: only scroll if it drifted out of the viewport
        if ba is not None:
            i_ba = next((i for i, t in enumerate(rows) if t[0] == ba), None)
            if i_ba is not None:
                it = self.dom.item(i_ba, 1)
                if it is not None and not self.dom.viewport().rect().intersects(
                        self.dom.visualItemRect(it)):
                    self.dom.scrollToItem(
                        it, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def _refresh_classic(self, m):
        c = self.cfg
        bids, asks = m.two_sided_ladder(c["dom_depth"])
        n = max(len(bids), len(asks))
        self.dom.setRowCount(n)
        lots = sorted(v / 100 for _p, _f, v in (bids + asks) if v)
        maxlot = self._smooth("_dom_max", lots[-1] if lots else 1)
        wall = c["wall_mult"] * self._smooth("_dom_med",
                                             lots[len(lots) // 2] if lots else 0)
        show_bars = c["show_depth_bars"]
        ctr = QtCore.Qt.AlignmentFlag.AlignCenter
        rgt = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        UR = QtCore.Qt.ItemDataRole.UserRole
        bold = QtGui.QFont()
        bold.setBold(True)

        def freq_item(f):
            return QtWidgets.QTableWidgetItem(format(f, ",") if f else "")

        def lot_item(v):
            lot = v / 100 if v else None
            it = QtWidgets.QTableWidgetItem(format(lot, ",.0f") if lot else "")
            it.setTextAlignment(rgt)
            if lot and show_bars:
                it.setData(UR, lot / maxlot)
                it.setData(UR + 1, bool(wall and lot >= wall))
            return it

        def price_item(p, color, top):
            it = QtWidgets.QTableWidgetItem(format(p, ",.0f"))
            it.setTextAlignment(ctr)
            it.setForeground(color)
            if top:
                it.setFont(bold)
            return it

        for i in range(n):
            if i < len(bids):
                bp, bf, bv = bids[i]
                self.dom.setItem(i, 0, freq_item(bf))
                self.dom.setItem(i, 1, lot_item(bv))
                self.dom.setItem(i, 2, price_item(bp, BULL, i == 0))
            else:
                for col in (0, 1, 2):
                    self.dom.setItem(i, col, QtWidgets.QTableWidgetItem(""))
            if i < len(asks):
                ap, af, av = asks[i]
                self.dom.setItem(i, 3, price_item(ap, BEAR, i == 0))
                self.dom.setItem(i, 4, lot_item(av))
                self.dom.setItem(i, 5, freq_item(af))
            else:
                for col in (3, 4, 5):
                    self.dom.setItem(i, col, QtWidgets.QTableWidgetItem(""))


# ============================================================
#  Trade tape
# ============================================================
@register
class TapePanel(Panel):
    kind = "tape"
    title = "Trade tape"

    def build(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self.tape = QtWidgets.QTableWidget(0, 4)
        self.tape.setHorizontalHeaderLabels(["Time", "Price", "Qty", "Side"])
        self.tape.verticalHeader().setVisible(False)
        self.tape.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tape.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lay.addWidget(self.tape)
        return w

    @staticmethod
    def fmt_time(r, ms=3):
        t = r.get("trade_time") or r.get("recv_iso") or ""
        if "T" in t:
            t = t.split("T", 1)[1]
        if "." in t:
            hms, frac = t.split(".", 1)
            return hms + ("." + frac[:ms] if ms > 0 else "")
        return t

    def refresh(self):
        m = self.model()
        if m is None:
            self.tape.setRowCount(0)
            return
        c = self.cfg
        self.titlebar.label.setText("Trade tape  %s" % self.spec()["symbol"])
        recent = m.trades[-c["tape_rows"]:][::-1]
        self.tape.setRowCount(len(recent))
        big = self.host.big_lots() * 100         # lots -> shares
        bigfont = QtGui.QFont()
        bigfont.setBold(True)
        bigbg = QtGui.QColor(74, 62, 30)
        for i, r in enumerate(recent):
            isbig = r["qty"] >= big
            items = [QtWidgets.QTableWidgetItem(self.fmt_time(r, c["time_ms"])),
                     QtWidgets.QTableWidgetItem(format(r["price"], ",.0f")),
                     QtWidgets.QTableWidgetItem(format(r["qty"], ",.0f")),
                     QtWidgets.QTableWidgetItem(r.get("side", ""))]
            items[3].setForeground(BULL if r.get("side") == "buy" else BEAR)
            for j, it in enumerate(items):
                if isbig:
                    it.setBackground(bigbg)
                    if j > 0:        # don't bold Time — bold text overflows the column
                        it.setFont(bigfont)
                self.tape.setItem(i, j, it)


# ============================================================
#  Watchlist — every symbol in the archive, click to retarget
# ============================================================
@register
class WatchlistPanel(Panel):
    kind = "watchlist"
    title = "Watchlist"
    wants_model = False

    def build(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self.tbl = QtWidgets.QTableWidget(0, 5)
        self.tbl.setHorizontalHeaderLabels(["Symbol", "Last", "Chg%", "Lot", "Trades"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.verticalHeader().setDefaultSectionSize(20)
        self.tbl.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tbl.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.cellClicked.connect(self._clicked)
        self.tbl.cellDoubleClicked.connect(self._dbl)
        self.tbl.setToolTip("Click: point this panel's link group at the symbol.\n"
                            "Double-click: open a new footprint for it.")
        lay.addWidget(self.tbl)
        return w

    def _sym_at(self, row):
        it = self.tbl.item(row, 0)
        return it.text() if it else None

    def _clicked(self, row, _col):
        sym = self._sym_at(row)
        if sym:
            self.set_source(symbol=sym)

    def _dbl(self, row, _col):
        sym = self._sym_at(row)
        if sym:
            self.host.add_panel("footprint", group=None, spec={"symbol": sym})

    def refresh(self):
        stats = self.host.symbol_stats()
        syms = sorted(stats)
        self.tbl.setRowCount(len(syms))
        rgt = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        active = {p.spec()["symbol"] for p in self.host.panels if p.wants_model}
        bold = QtGui.QFont()
        bold.setBold(True)
        for i, sym in enumerate(syms):
            s = stats[sym]
            last, prev = s.get("last"), s.get("prev")
            chg = ((last - prev) / prev * 100) if (last and prev) else None
            cells = [sym,
                     format(last, ",.0f") if last else "-",
                     ("%+.2f" % chg) if chg is not None else "-",
                     format((s.get("vol_sh") or 0) / 100, ",.0f"),
                     format(s.get("trades") or 0, ",.0f")]
            for j, txt in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(txt)
                if j:
                    it.setTextAlignment(rgt)
                if j == 2 and chg is not None:
                    it.setForeground(BULL if chg >= 0 else BEAR)
                if sym in active:
                    it.setFont(bold)
                self.tbl.setItem(i, j, it)


# ============================================================
#  Signal log — absorption / imbalance the footprint already found
# ============================================================
@register
class SignalLogPanel(Panel):
    kind = "signals"
    title = "Signal log"
    wants_model = False

    def build(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        self.tbl = QtWidgets.QTableWidget(0, 4)
        self.tbl.setHorizontalHeaderLabels(["Time", "Price", "Signal", "Bar"])
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.verticalHeader().setDefaultSectionSize(19)
        self.tbl.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tbl.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.cellClicked.connect(self._jump)
        self.tbl.setToolTip("Absorption and diagonal-imbalance events from the linked\n"
                            "footprint. Click a row to centre the chart on that bar.")
        lay.addWidget(self.tbl)
        return w

    def _jump(self, row, _col):
        it = self.tbl.item(row, 3)
        fp = self.host.footprint_for(self)
        if it and fp:
            self.host.center_on_bar(fp, int(it.text()))

    def refresh(self):
        fp = self.host.footprint_for(self)
        if fp is None or fp.model() is None:
            self.tbl.setRowCount(0)
            self.titlebar.set_note("no linked footprint")
            return
        m = fp.model()
        item = fp.fp_item
        ids = m.bar_ids()
        ev = []
        for xi, price, flag in item._absorbs:
            ev.append((xi, price, "ABSORB " + ("support" if flag > 0 else "resist"),
                       "#38e6ff" if flag > 0 else "#ff9f43"))
        for xi, price, _sell, _buy, _d, sell_imb, buy_imb, _poc in item._cells:
            if buy_imb:
                ev.append((xi, price, "IMB buy", "#3fe26a"))
            elif sell_imb:
                ev.append((xi, price, "IMB sell", "#ff5454"))
        ev.sort(key=lambda e: e[0])
        ev = ev[-self.cfg["signal_rows"]:][::-1]
        self.titlebar.set_note("%d events" % len(ev))
        self.tbl.setRowCount(len(ev))
        rgt = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        for i, (xi, price, label, col) in enumerate(ev):
            t = ""
            if 0 <= xi < len(ids):
                meta = m.bar_meta.get(ids[xi])
                if meta:
                    t = QtCore.QDateTime.fromSecsSinceEpoch(
                        int(meta["t1"])).toString("HH:mm:ss")
            cells = [t, format(price, ",.0f"), label, str(xi)]
            for j, txt in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(txt)
                if j in (1, 3):
                    it.setTextAlignment(rgt)
                if j == 2:
                    it.setForeground(QtGui.QColor(col))
                self.tbl.setItem(i, j, it)


# ============================================================
#  Cumulative depth curve
# ============================================================
@register
class DepthPanel(ChartPanel):
    kind = "depth"
    title = "Depth"
    cross_y = True

    def build(self):
        pw = self._make_plot(axisItems={"left": CommaAxis(orientation="left")})
        self.p.showGrid(x=True, y=True, alpha=0.15)
        self.bid_curve = self.p.plot([], [], pen=pg.mkPen("#3fe26a", width=2),
                                     fillLevel=0, brush=pg.mkBrush(63, 226, 106, 60),
                                     stepMode="right")
        self.ask_curve = self.p.plot([], [], pen=pg.mkPen("#ff5454", width=2),
                                     fillLevel=0, brush=pg.mkBrush(255, 84, 84, 60),
                                     stepMode="right")
        self._init_cross()
        return pw

    def refresh(self):
        m = self.model()
        if m is None:
            self.bid_curve.setData([], [])
            self.ask_curve.setData([], [])
            return
        n = self.cfg["depth_levels"]
        bids, asks = m.two_sided_ladder(n)
        # cumulative resting size walking away from the touch, in lots
        def curve(rows, ascending):
            pts = [(p, (v or 0) / 100) for p, _f, v in rows]
            pts.sort(key=lambda t: t[0])
            if not ascending:                    # bids accumulate downward from best
                pts.reverse()
            xs, ys, run = [], [], 0.0
            for p, lot in pts:
                run += lot
                xs.append(p)
                ys.append(run)
            if not ascending:
                xs.reverse()
                ys.reverse()
            return xs, ys
        bx, by = curve(bids, False)
        ax, ay = curve(asks, True)
        # stepMode "right" takes equal-length x/y (only "center" wants the extra edge)
        self.bid_curve.setData(bx, by)
        self.ask_curve.setData(ax, ay)
        self.titlebar.label.setText("Depth  %s" % self.spec()["symbol"])


# ============================================================
#  Capture integrity — the holes in the tape, recorded not inferred
# ============================================================
@register
class CaptureIntegrityPanel(Panel):
    """Every gap the feed recorded, and how much of the session survived.

    The CVD panel and the backtest used to *infer* holes from missing timestamps,
    which cannot tell a quiet market from a dropped socket. feed.live_feed now
    emits a ('gap', rec) event for each one, so this is fact."""
    kind = "integrity"
    title = "Capture integrity"

    COLS = ["Started", "Lasted", "Cause", "Symbol"]
    CAUSE_COLOUR = {"disconnect": "#ff9f43", "token": "#ff5454", "idle": "#e6b450"}

    def build(self):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(2)
        self.summary = QtWidgets.QLabel("")
        self.summary.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.summary.setStyleSheet("font-family:Consolas; font-size:12px; padding:1px 3px;")
        lay.addWidget(self.summary)
        self.tbl = QtWidgets.QTableWidget(0, len(self.COLS))
        self.tbl.setHorizontalHeaderLabels(self.COLS)
        self.tbl.verticalHeader().setVisible(False)
        self.tbl.verticalHeader().setDefaultSectionSize(19)
        self.tbl.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.Stretch)
        self.tbl.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tbl.setToolTip("Holes in the captured tape. 'disconnect' = the socket\n"
                            "dropped and reconnected; 'token' = the session token\n"
                            "was rejected. Missed tape cannot be recovered.")
        lay.addWidget(self.tbl)
        return w

    @staticmethod
    def _dur(sec):
        if sec < 60:
            return "%.1fs" % sec
        if sec < 3600:
            return "%dm %02ds" % (sec // 60, sec % 60)
        return "%dh %02dm" % (sec // 3600, (sec % 3600) // 60)

    def refresh(self):
        m = self.model()
        if m is None:
            self.tbl.setRowCount(0)
            self.summary.setText("")
            self.titlebar.set_note("no source")
            return
        sym = self.spec()["symbol"]
        gaps = [g for g in m.gaps if not sym or g.get("symbol") in (sym, "", None)]

        cov = m.coverage()
        if cov is None:
            self.summary.setText("<span style='color:#7f8792'>no trades yet</span>")
        else:
            frac, lost, span = cov
            colour = "#3fe26a" if frac >= 0.999 else "#e6b450" if frac >= 0.99 else "#ff5454"
            self.summary.setText(
                "<span style='color:%s'>%.2f%% captured</span>"
                "<span style='color:#5f6b76'> of %s session &middot; </span>"
                "<span style='color:#c9ced4'>%s lost</span>"
                % (colour, 100.0 * frac, self._dur(span), self._dur(lost)))
        self.titlebar.set_note("%d gap%s" % (len(gaps), "" if len(gaps) == 1 else "s"))

        rows = gaps[-self.cfg.get("signal_rows", 300):][::-1]   # newest first
        self.tbl.setRowCount(len(rows))
        for i, g in enumerate(rows):
            started = (g.get("started") or "")
            hhmmss = started.split("T", 1)[1][:8] if "T" in started else started
            cause = g.get("kind", "?")
            cells = [hhmmss, self._dur(float(g.get("seconds") or 0.0)),
                     cause, g.get("symbol", "")]
            for j, text in enumerate(cells):
                it = QtWidgets.QTableWidgetItem(text)
                if j == 2:
                    it.setForeground(QtGui.QColor(self.CAUSE_COLOUR.get(cause, "#c9ced4")))
                if g.get("detail"):
                    it.setToolTip(g["detail"])
                self.tbl.setItem(i, j, it)
