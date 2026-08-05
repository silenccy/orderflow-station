"""
of_app.py — IDX orderflow chart (PySide6 + pyqtgraph). One tool: capture + chart.

Panels:
  - Footprint   : per bar, per price -> bid x ask volume + delta color, POC outlined
  - Heatmap     : resting depth (size) over time, perceptual colormap
  - CVD         : cumulative volume delta (time axis)
  - Volume@Price: session volume profile (shares Y = price)
  - DOM ladder + trade tape (side dock tables)

Modes:
  python of_app.py --replay                 # default: load book.csv/trades.csv
  python of_app.py --live --symbol ASII     # connect, persist CSVs, chart live
  python of_app.py --shot out.png           # render replay once to PNG (headless)

Bar basis toggled in the toolbar (time / tick / volume).
"""

import argparse
import os
import sys
import time
from collections import Counter
from datetime import datetime

import numpy as np

from . import feed as of_feed
from . import model as of_model

# Qt platform must be chosen before QApplication is constructed.
if "--shot" in sys.argv and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402
import pyqtgraph as pg  # noqa: E402

pg.setConfigOption("background", "#0b0d0e")
pg.setConfigOption("foreground", "#9aa0a6")
pg.setConfigOption("imageAxisOrder", "row-major")
pg.setConfigOptions(antialias=True)

BULL = QtGui.QColor(63, 226, 106)
BEAR = QtGui.QColor(255, 84, 84)
POC = QtGui.QColor(255, 224, 102)
GRID = QtGui.QColor(40, 46, 50)
TXT = QtGui.QColor(224, 224, 224)
DIM = QtGui.QColor(120, 126, 132)
IMB_BUY = QtGui.QColor(124, 255, 124)   # buy-imbalance edge marker
IMB_SELL = QtGui.QColor(255, 77, 77)    # sell-imbalance edge marker
IMB_RATIO = 3.0
IMB_MIN = 300                           # shares; ignore imbalances on tiny cells
# Quantower-style bid|ask footprint cell palette
CELL_BG = QtGui.QColor(44, 80, 132)      # volume-shaded blue cell
IMB_BUY_BG = QtGui.QColor(34, 150, 84)   # buy-imbalance cell fill (green)
IMB_SELL_BG = QtGui.QColor(150, 46, 46)  # sell-imbalance bid-half tint (red)
IMB_SELL_NUM = QtGui.QColor(245, 96, 96) # sell-imbalance number (red)
CELL_NUM = QtGui.QColor(205, 214, 226)   # default cell numbers
CELL_DIV = QtGui.QColor(16, 26, 40)      # bid|ask divider
HDR_DIM = QtGui.QColor(150, 162, 178)    # per-bar V / R-H / R-L header text
ABSORB_SUP = QtGui.QColor(56, 230, 255)  # buy absorption at the low = support (cyan)
ABSORB_RES = QtGui.QColor(255, 159, 67)  # sell absorption at the high = resistance (orange)


def _absorption(price, buy, sell, total, hi, lo, ref, floor, ratio):
    """Absorption at a rejected bar extreme: heavy aggressive volume that failed to
    move price. +1 = support (heavy selling held the low, bids absorbed),
    -1 = resistance (heavy buying stalled at the high, asks absorbed), else 0."""
    if hi == lo or total < max(ref, floor):
        return 0
    if price == lo and sell >= ratio * max(buy, 1):
        return 1
    if price == hi and buy >= ratio * max(sell, 1):
        return -1
    return 0

# Tunable settings (the Settings panel edits these; cell_mode + big_lots stay on the toolbar).
DEFAULTS = {
    "imb_ratio": 3.0, "imb_min_lots": 3, "va_coverage": 70,
    "show_poc": True, "show_imbalance": True, "show_va_lines": True, "show_vwap": True,
    "show_headers": True, "header_units": "lots", "show_candle": True, "cell_gap": 6,
    "fp_style": "clusters",
    "show_absorption": True, "absorption_min_lots": 100, "absorption_ratio": 2.0,
    "show_regime": True, "regime_window": 20, "regime_warmup_min": 20,
    "er_trend": 0.5, "er_chop": 0.3,
    "colormap": "viridis", "hm_window": 1500, "hm_throttle": 1.0, "hm_pctile": 97,
    "hm_scale": "equalize", "hm_gamma": 3.0,
    "show_price_line": True, "show_bubbles": True, "show_hm_candles": True,
    "show_walls": True,
    "bubble_ref_pct": 90,
    "bubble_min_frac": 0.45, "bubble_opacity": 130,
    "dom_pro": True, "dom_resizable": True, "dom_depth": 14, "wall_mult": 3.0,
    "show_depth_bars": True,
    "tape_rows": 200, "time_ms": 3,
    "show_footprint": True, "show_vap": True, "vap_scale": "sqrt", "show_delta": False,
    "show_heatmap_panel": True, "show_cvd": True, "live_hz": 7,
    "history": "today",
}

# (tab, [(key, label, kind, spec)]) — kind: bool | int | double | choice
SETTINGS_SPEC = [
    ("Footprint", [
        ("fp_style", "Chart style", "choice", ["clusters", "candles"]),
        ("imb_ratio", "Imbalance ratio", "double", (1.0, 20.0, 0.5)),
        ("imb_min_lots", "Imbalance min (lots)", "int", (0, 100000)),
        ("va_coverage", "Value-area %", "int", (10, 95)),
        ("show_poc", "Highlight POC cell (gold)", "bool", None),
        ("show_candle", "Show mid candle (OHLC)", "bool", None),
        ("cell_gap", "Bid|ask gap (% of bar)", "int", (0, 30)),
        ("show_imbalance", "Show imbalance markers", "bool", None),
        ("show_absorption", "Show absorption (support/resist)", "bool", None),
        ("absorption_min_lots", "Absorption min (lots)", "int", (0, 100000)),
        ("absorption_ratio", "Absorption ratio", "double", (1.0, 20.0, 0.5)),
        ("show_headers", "Show V / D / R headers", "bool", None),
        ("header_units", "Volume units (headers+cells)", "choice", ["lots", "shares"]),
        ("show_va_lines", "Show value-area lines", "bool", None),
        ("show_vwap", "Show VWAP line", "bool", None),
        ("show_regime", "Show regime chip", "bool", None),
        ("regime_window", "Regime window (bars)", "int", (5, 200)),
        ("regime_warmup_min", "Regime warm-up (min)", "int", (0, 120)),
        ("er_trend", "Trend threshold (ER)", "double", (0.1, 1.0, 0.05)),
        ("er_chop", "Chop threshold (ER)", "double", (0.0, 0.9, 0.05)),
    ]),
    ("Heatmap", [
        ("colormap", "Colormap", "choice", ["bookmap", "inferno", "viridis", "turbo", "magma"]),
        ("hm_window", "Window (columns)", "int", (60, 10000)),
        ("hm_throttle", "Throttle (s / column)", "double", (0.1, 10.0, 0.5)),
        ("hm_scale", "Contrast scale", "choice", ["equalize", "sqrt", "linear", "log"]),
        ("hm_gamma", "Equalize darkness (γ)", "double", (0.5, 6.0, 0.25)),
        ("hm_pctile", "Clip percentile (non-equalize)", "int", (50, 100)),
        ("show_price_line", "Show price line", "bool", None),
        ("show_walls", "Track biggest walls (lines)", "bool", None),
        ("show_hm_candles", "Overlay OHLC candles", "bool", None),
        ("show_bubbles", "Show trade bubbles", "bool", None),
        ("bubble_ref_pct", "Bubble size percentile", "int", (50, 100)),
        ("bubble_min_frac", "Bubble min size", "double", (0.0, 1.0, 0.05)),
        ("bubble_opacity", "Bubble opacity", "int", (20, 255)),
    ]),
    ("DOM & Tape", [
        ("dom_pro", "Pro mode (centered ladder)", "bool", None),
        ("dom_resizable", "Resizable columns (drag headers)", "bool", None),
        ("dom_depth", "DOM depth (rows)", "int", (3, 60)),
        ("wall_mult", "Wall × median", "double", (1.0, 20.0, 0.5)),
        ("show_depth_bars", "Show depth bars", "bool", None),
        ("tape_rows", "Tape rows", "int", (10, 2000)),
        ("time_ms", "Tape time decimals", "int", (0, 9)),
    ]),
    ("Layout", [
        ("show_footprint", "Show footprint", "bool", None),
        ("show_vap", "Show vol@price", "bool", None),
        ("vap_scale", "Vol@price width scale", "choice", ["sqrt", "linear"]),
        ("show_delta", "Show delta footer", "bool", None),
        ("show_heatmap_panel", "Show heatmap", "bool", None),
        ("show_cvd", "Show CVD", "bool", None),
        ("live_hz", "Live redraw (Hz)", "int", (1, 30)),
    ]),
    ("General", [
        ("history", "Live history preload (restart)", "choice", ["today", "all", "none"]),
    ]),
]

# key -> (kind, spec) for range-clamp / choice-validation on restore
SPEC_BY_KEY = {key: (kind, spec)
               for _tab, items in SETTINGS_SPEC for key, _label, kind, spec in items}
MODEL_CFG_KEYS = {"hm_throttle", "hm_window"}   # only these need a model rebuild to apply


class SettingsDialog(QtWidgets.QDialog):
    """Tabbed editor over a cfg dict. Calls `on_apply(new_values)` on Apply/OK."""

    def __init__(self, cfg, on_apply, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(360)
        self._on_apply = on_apply
        self.widgets = {}
        tabs = QtWidgets.QTabWidget()
        for tabname, items in SETTINGS_SPEC:
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            for key, label, kind, spec in items:
                w = self._make(kind, spec, cfg.get(key, DEFAULTS[key]))
                self.widgets[key] = (w, kind)
                form.addRow(label, w)
            tabs.addTab(page, tabname)
        B = QtWidgets.QDialogButtonBox
        btns = B(B.StandardButton.Ok | B.StandardButton.Apply | B.StandardButton.Cancel)
        btns.accepted.connect(self._ok)        # OK = apply once, then close
        btns.rejected.connect(self.reject)     # Cancel discards (values only read on _apply)
        btns.button(B.StandardButton.Apply).clicked.connect(self._apply)
        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(tabs)
        lay.addWidget(btns)

    @staticmethod
    def _make(kind, spec, val):
        if kind == "bool":
            w = QtWidgets.QCheckBox(); w.setChecked(bool(val))
        elif kind == "int":
            w = QtWidgets.QSpinBox(); w.setRange(spec[0], spec[1]); w.setValue(int(val))
        elif kind == "double":
            w = QtWidgets.QDoubleSpinBox()
            w.setRange(spec[0], spec[1]); w.setSingleStep(spec[2]); w.setValue(float(val))
        else:
            w = QtWidgets.QComboBox(); w.addItems(spec); w.setCurrentText(str(val))
        return w

    def values(self):
        out = {}
        for key, (w, kind) in self.widgets.items():
            if kind == "bool":
                out[key] = w.isChecked()
            elif kind in ("int", "double"):
                out[key] = w.value()
            else:
                out[key] = w.currentText()
        return out

    def _apply(self):
        self._on_apply(self.values())

    def _ok(self):
        self._apply()
        self.accept()


def _imb_ratio(num, den, cap=99.0):
    """Diagonal imbalance ratio, capped. A 0 denominator (no opposing volume on
    the diagonal) means a fully one-sided extreme -> report the cap."""
    if den <= 0:
        return cap if num > 0 else 0.0
    return min(num / den, cap)


def _fmt_ratio(r):
    if r >= 10:
        return f"{r:.0f}"
    if r >= 1:
        return f"{r:.1f}"
    return f"{r:.2f}"


class CommaAxis(pg.AxisItem):
    """Y axis with plain comma-separated ticks (no 4e+06 scientific notation)."""

    def tickStrings(self, values, scale, spacing):
        return [f"{v * scale:,.0f}" for v in values]


# ============================================================
#  Footprint graphics item
# ============================================================
class FootprintItem(pg.GraphicsObject):
    """Draws per-bar price cells: left half = sell vol, right half = buy vol,
    volume-shaded; per-bar POC cell filled gold (Quantower-style, dark digits).
    x = bar index, y = price."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._brect = QtCore.QRectF()
        self._cells = []         # (xi, price, sell, buy, delta, sell_imb, buy_imb)
        self._barstats = []      # (xi, hi, lo, V, D, R_H, R_L) for headers/footers
        self._absorbs = []       # (xi, price, +1 support / -1 resistance) absorption markers
        self._tickval = 1.0
        self.cell_mode = "bidask"   # "bidask" -> Quantower bid|ask | "delta" -> Δ heat
        self.cfg = DEFAULTS         # MainWindow swaps in the live cfg dict

    def set_model(self, model):
        self._generate(model)
        self.informViewBoundsChanged()
        self.update()

    def _tick(self, prices):
        # most common gap = the real tick (robust to stray odd-priced prints
        # that would otherwise make the diagonal/imbalance lookups miss)
        diffs = [round(b - a, 6) for a, b in zip(prices, prices[1:]) if b > a]
        return Counter(diffs).most_common(1)[0][0] if diffs else 5.0

    def _generate(self, model):
        self.picture = QtGui.QPicture()
        self._cells = []
        self._barstats = []
        self._absorbs = []
        p = QtGui.QPainter(self.picture)
        bars = model.bar_ids()
        if not bars:
            p.end()
            self._brect = QtCore.QRectF()
            return

        all_cells = [c for b in bars for c in model.bar_cells(b)[0]]
        all_prices = sorted({c[0] for c in all_cells})
        # book-derived tick: sparse/off-tick traded prices can't corrupt the grid
        tick = model.tick_size() or self._tick(all_prices)
        self._tickval = tick
        ymin, ymax = min(all_prices) - tick, max(all_prices) + tick
        # robust volume reference: 95th percentile of cell totals (outlier-proof)
        totals = sorted(c[4] for c in all_cells)
        ref = (totals[min(len(totals) - 1, int(0.95 * len(totals)))] or 1) if totals else 1
        gdelta = max((abs(c[3]) for c in all_cells), default=1) or 1
        delta_mode = self.cell_mode == "delta"
        cfg = self.cfg
        imb_ratio = cfg["imb_ratio"]
        imb_min = cfg["imb_min_lots"] * 100
        show_imb = cfg["show_imbalance"]
        show_poc = cfg["show_poc"]
        show_candle = cfg["show_candle"]
        gap = min(max(cfg["cell_gap"], 0), 30) / 100.0   # bid|ask spacing, frac of bar
        candles_only = cfg.get("fp_style", "clusters") == "candles"
        show_absorb = cfg["show_absorption"]
        absorb_floor = cfg["absorption_min_lots"] * 100
        absorb_ratio = cfg["absorption_ratio"]

        for xi, bar in enumerate(bars):
            rows, poc = model.bar_cells(bar)
            cmap = {pr: (bv, sv) for pr, bv, sv, _d, _t in rows}
            tl = sorted(t for _p, _b, _s, _d, t in rows)
            imb_floor = max(imb_min, tl[len(tl) // 2]) if tl else imb_min
            hi, lo = max(cmap), min(cmap)
            absorbed = {}                        # price -> +1 support / -1 resistance
            if show_absorb:
                for pr, bv, sv, _d, tt in rows:
                    fl = _absorption(pr, bv, sv, tt, hi, lo, ref, absorb_floor, absorb_ratio)
                    if fl:
                        absorbed[pr] = fl
                        self._absorbs.append((xi, pr, fl))
            for price, buy, sell, delta, total in ([] if candles_only else rows):
                y = price - tick / 2
                below_sell = cmap.get(price - tick, (0, 0))[1]   # diagonal imbalance
                above_buy = cmap.get(price + tick, (0, 0))[0]
                buy_imb = show_imb and buy >= imb_floor and buy >= imb_ratio * below_sell
                sell_imb = show_imb and sell >= imb_floor and sell >= imb_ratio * above_buy

                is_poc = show_poc and price == poc
                if delta_mode:                                   # signed-|delta| heat
                    col = QtGui.QColor(BULL if delta >= 0 else BEAR)
                    col.setAlpha(40 + int(195 * min(abs(delta) / gdelta, 1.0)))
                    p.fillRect(QtCore.QRectF(xi + 0.04, y, 0.92, tick), col)
                else:                                            # Quantower bid|ask
                    # floor 60: even tiny cells stay a visible blue block (QT-like
                    # cluster body at any zoom), shading still scales with volume
                    base = QtGui.QColor(CELL_BG)
                    base.setAlpha(60 + int(175 * (min(total / ref, 1.0) ** 0.5)))
                    if is_poc:                                   # gold POC cell, no outline
                        lcol = rcol = QtGui.QColor(POC)
                        lcol.setAlpha(215)
                    else:
                        lcol = rcol = base
                        if sell_imb:
                            lcol = QtGui.QColor(IMB_SELL_BG)
                            lcol.setAlpha(110 + int(90 * min(total / ref, 1.0)))
                        if buy_imb:
                            rcol = QtGui.QColor(IMB_BUY_BG)
                            rcol.setAlpha(150 + int(95 * min(total / ref, 1.0)))
                    p.fillRect(QtCore.QRectF(xi + 0.03, y, 0.47 - gap / 2, tick), lcol)
                    p.fillRect(QtCore.QRectF(xi + 0.5 + gap / 2, y, 0.47 - gap / 2, tick), rcol)
                    if gap == 0:                  # touching halves need the divider back
                        p.setPen(QtGui.QPen(CELL_DIV, 0))
                        p.drawLine(QtCore.QPointF(xi + 0.5, y), QtCore.QPointF(xi + 0.5, y + tick))
                    if price in absorbed:         # absorption glow border (zoom-independent)
                        gp = QtGui.QPen(ABSORB_SUP if absorbed[price] > 0 else ABSORB_RES)
                        gp.setWidth(2); gp.setCosmetic(True)
                        p.setPen(gp); p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
                        p.drawRect(QtCore.QRectF(xi + 0.03, y, 0.94, tick))
                self._cells.append((xi, price, sell, buy, delta, sell_imb, buy_imb, is_poc))
            V = sum(t for _p, _b, _s, _d, t in rows)
            D = sum(d for _p, _b, _s, d, _t in rows)
            if candles_only or show_candle:    # candle: full-width style or thin mid-bar
                meta = model.bar_meta.get(bar)
                if meta and meta.get("o") is not None:
                    o, cl = meta["o"], meta["c"]
                    col = QtGui.QColor(BULL if cl >= o else BEAR)
                    p.setPen(QtGui.QPen(col, 0))
                    p.drawLine(QtCore.QPointF(xi + 0.5, lo),      # wick spans the traded range
                               QtCore.QPointF(xi + 0.5, hi))
                    bot, top = min(o, cl), max(o, cl)
                    if top - bot < tick * 0.3:
                        top = bot + tick * 0.3            # doji still gets a visible body
                    if candles_only:
                        p.fillRect(QtCore.QRectF(xi + 0.22, bot, 0.56, top - bot), col)
                    else:
                        p.fillRect(QtCore.QRectF(xi + 0.45, bot, 0.10, top - bot), col)
            if candles_only and show_poc:      # gold POC dash beside the candle
                p.fillRect(QtCore.QRectF(xi + 0.02, poc - tick * 0.35, 0.13, tick * 0.7),
                           QtGui.QColor(POC))
            r_h = _imb_ratio(cmap[hi][0], cmap.get(hi - tick, (0, 0))[1])   # buy@hi / sell@(hi-1)
            r_l = _imb_ratio(cmap[lo][1], cmap.get(lo + tick, (0, 0))[0])   # sell@lo / buy@(lo+1)
            self._barstats.append((xi, hi, lo, V, D, r_h, r_l))
        p.end()
        self._brect = QtCore.QRectF(0, ymin - 1.5 * tick, len(bars), (ymax - ymin) + 5 * tick)

    def boundingRect(self):
        return self._brect

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)
        if not self._cells and not self._barstats:
            return                              # candles mode has no cells but keeps headers
        tick = self._tickval
        tr = p.worldTransform()
        p.save()
        p.resetTransform()                 # fixed-size text in device space
        font = QtGui.QFont("Consolas"); font.setPixelSize(11)
        boldf = QtGui.QFont("Consolas"); boldf.setPixelSize(11); boldf.setBold(True)
        delta_mode = self.cell_mode == "delta"
        unit = 100 if self.cfg["header_units"] == "lots" else 1   # cells match the headers
        gap = min(max(self.cfg["cell_gap"], 0), 30) / 100.0
        A_R = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        A_L = QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter
        A_C = QtCore.Qt.AlignmentFlag.AlignCenter
        poc_num = QtGui.QColor(26, 28, 20)     # dark digits on the gold POC cell
        for xi, price, sell, buy, delta, sell_imb, buy_imb, is_poc in self._cells:
            x0 = tr.map(QtCore.QPointF(xi + 0.03, price)).x()
            x1 = tr.map(QtCore.QPointF(xi + 0.97, price)).x()
            ya = tr.map(QtCore.QPointF(xi + 0.5, price + tick / 2)).y()
            yb = tr.map(QtCore.QPointF(xi + 0.5, price - tick / 2)).y()
            wpx, hpx, ytop = abs(x1 - x0), abs(yb - ya), min(ya, yb)
            if hpx < 10:
                continue
            if delta_mode:
                txt = f"{delta / unit:+,.0f}"
                if wpx >= len(txt) * 6.5:
                    p.setFont(font); p.setPen(QtGui.QPen(TXT))
                    p.drawText(QtCore.QRectF(x0, ytop, wpx, hpx), A_C, txt)
                continue
            if wpx < 34:
                continue
            xg0 = tr.map(QtCore.QPointF(xi + 0.5 - gap / 2, price)).x()   # gap edges
            xg1 = tr.map(QtCore.QPointF(xi + 0.5 + gap / 2, price)).x()
            p.setFont(boldf if sell_imb else font)
            p.setPen(QtGui.QPen(poc_num if is_poc
                                else IMB_SELL_NUM if sell_imb else CELL_NUM))
            p.drawText(QtCore.QRectF(x0 + 2, ytop, xg0 - x0 - 4, hpx), A_R,
                       f"{sell / unit:,.0f}")
            p.setFont(boldf if buy_imb else font)
            p.setPen(QtGui.QPen(poc_num if is_poc
                                else QtGui.QColor(235, 242, 248) if buy_imb else CELL_NUM))
            p.drawText(QtCore.QRectF(xg1 + 2, ytop, x1 - xg1 - 4, hpx), A_L,
                       f"{buy / unit:,.0f}")

        if self.cfg["show_headers"]:
            unit = 100 if self.cfg["header_units"] == "lots" else 1
            hdr = QtGui.QFont("Consolas"); hdr.setPixelSize(10)
            p.setFont(hdr)
            for xi, hi, lo, V, D, r_h, r_l in self._barstats:
                xL = tr.map(QtCore.QPointF(xi + 0.03, hi)).x()
                wpx = abs(tr.map(QtCore.QPointF(xi + 0.97, hi)).x() - xL)
                if wpx < 44:                       # only label bars wide enough to read
                    continue
                htop = tr.map(QtCore.QPointF(xi + 0.5, hi + tick / 2)).y()
                p.setPen(QtGui.QPen(HDR_DIM))
                p.drawText(QtCore.QRectF(xL, htop - 42, wpx, 13), A_C, f"V {V / unit:,.0f}")
                p.setPen(QtGui.QPen(BULL if D >= 0 else BEAR))
                p.drawText(QtCore.QRectF(xL, htop - 29, wpx, 13), A_C, f"D {D / unit:+,.0f}")
                p.setPen(QtGui.QPen(HDR_DIM))
                p.drawText(QtCore.QRectF(xL, htop - 16, wpx, 13), A_C, f"R/H {_fmt_ratio(r_h)}")
                lbot = tr.map(QtCore.QPointF(xi + 0.5, lo - tick / 2)).y()
                p.drawText(QtCore.QRectF(xL, lbot + 2, wpx, 13), A_C, f"R/L {_fmt_ratio(r_l)}")

        for xi, price, flag in self._absorbs:      # ▲ support / ▼ resistance at the extreme
            cx = tr.map(QtCore.QPointF(xi + 0.5, price)).x()
            col = ABSORB_SUP if flag > 0 else ABSORB_RES
            p.setPen(QtGui.QPen(col)); p.setBrush(QtGui.QBrush(col))
            s = 5
            if flag > 0:                           # below the low, pointing up
                yb = tr.map(QtCore.QPointF(xi + 0.5, price - tick / 2)).y() + 3
                tri = [(cx, yb), (cx - s, yb + 2 * s), (cx + s, yb + 2 * s)]
            else:                                  # above the high, pointing down
                yt = tr.map(QtCore.QPointF(xi + 0.5, price + tick / 2)).y() - 3
                tri = [(cx, yt), (cx - s, yt - 2 * s), (cx + s, yt - 2 * s)]
            p.drawPolygon(QtGui.QPolygonF([QtCore.QPointF(*pt) for pt in tri]))
        p.restore()


class SmoothImageItem(pg.ImageItem):
    """ImageItem with bilinear smoothing so the heatmap renders as a continuous
    Bookmap-style liquidity field instead of hard nearest-neighbour rectangles."""

    def paint(self, p, *args):
        p.setRenderHint(QtGui.QPainter.RenderHint.SmoothPixmapTransform, True)
        super().paint(p, *args)


class DeltaFooterItem(pg.GraphicsObject):
    """Per-bar delta as always-visible colored bars (green up / red down from a
    midline, height proportional to |delta|); the number is overlaid when the bar
    is wide enough. x = bar index, y in [0, 1]."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._rows = []          # (bar_index, delta)
        self._brect = QtCore.QRectF()
        self.cfg = DEFAULTS      # MainWindow swaps in the live cfg dict

    def set_model(self, model):
        bd = model.bar_deltas()
        self._rows = [(i, d) for i, (_b, d, _c) in enumerate(bd)]
        maxabs = max((abs(d) for _i, d in self._rows), default=1) or 1
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for i, d in self._rows:
            frac = min(abs(d) / maxabs, 1.0)
            col = QtGui.QColor(BULL if d >= 0 else BEAR)
            col.setAlpha(210)
            if d >= 0:
                p.fillRect(QtCore.QRectF(i + 0.08, 0.5 - 0.46 * frac, 0.84, 0.46 * frac), col)
            else:
                p.fillRect(QtCore.QRectF(i + 0.08, 0.5, 0.84, 0.46 * frac), col)
        p.setPen(QtGui.QPen(GRID, 0))
        p.drawLine(QtCore.QPointF(0, 0.5), QtCore.QPointF(len(self._rows), 0.5))
        p.end()
        self._brect = QtCore.QRectF(0, 0, max(len(self._rows), 1), 1)
        self.informViewBoundsChanged()
        self.update()

    def boundingRect(self):
        return self._brect

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)
        if not self._rows:
            return
        tr = p.worldTransform()
        p.save()
        p.resetTransform()
        font = QtGui.QFont("Consolas")
        font.setPixelSize(11)
        p.setFont(font)
        p.setPen(QtGui.QPen(TXT))
        unit = 100 if self.cfg["header_units"] == "lots" else 1
        for i, d in self._rows:
            cx = i + 0.5
            ctr = tr.map(QtCore.QPointF(cx, 0.5))
            wpx = abs(tr.map(QtCore.QPointF(cx + 0.84, 0.5)).x()
                      - tr.map(QtCore.QPointF(cx, 0.5)).x())
            txt = f"{d / unit:+,.0f}"
            if wpx >= len(txt) * 6.5:
                p.drawText(QtCore.QRectF(ctr.x() - wpx / 2, ctr.y() - 7, wpx, 14),
                           QtCore.Qt.AlignmentFlag.AlignCenter, txt)
        p.restore()


class HeatmapCandleItem(pg.GraphicsObject):
    """OHLC candles overlaid on the liquidity heatmap (x = heatmap column space,
    one candle per footprint bar, bar-width like the Binance-style heatmaps)."""

    def __init__(self):
        super().__init__()
        self.picture = QtGui.QPicture()
        self._brect = QtCore.QRectF()

    def set_bars(self, rows, tick=1.0):
        """rows: [(x0, x1, open, high, low, close)] in heatmap column coords."""
        self.picture = QtGui.QPicture()
        p = QtGui.QPainter(self.picture)
        for x0, x1, o, h, l, c in rows:
            col = QtGui.QColor(BULL if c >= o else BEAR)
            cx = (x0 + x1) / 2
            p.setPen(QtGui.QPen(col, 0))
            p.drawLine(QtCore.QPointF(cx, l), QtCore.QPointF(cx, h))
            bot, top = min(o, c), max(o, c)
            if top - bot < tick * 0.25:
                top = bot + tick * 0.25       # doji stays visible
            body = QtGui.QColor(col)
            body.setAlpha(220)
            w = x1 - x0
            p.fillRect(QtCore.QRectF(x0 + 0.08 * w, bot, 0.84 * w, top - bot), body)
        p.end()
        if rows:
            xa = min(r[0] for r in rows)
            xb = max(r[1] for r in rows)
            ylo = min(r[4] for r in rows)
            yhi = max(r[3] for r in rows)
            self._brect = QtCore.QRectF(xa, ylo - tick, xb - xa, (yhi - ylo) + 2 * tick)
        else:
            self._brect = QtCore.QRectF()
        self.informViewBoundsChanged()
        self.update()

    def boundingRect(self):
        return self._brect

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)


class DepthBarDelegate(QtWidgets.QStyledItemDelegate):
    """Paints a horizontal depth bar (length proportional to lot) behind a
    right-aligned number. Reads fraction from UserRole, wall flag from UserRole+1."""

    def __init__(self, side, color, parent=None):
        super().__init__(parent)
        self.side = side
        self.bar = QtGui.QColor(color); self.bar.setAlpha(120)   # solid QT-style blocks
        self.wall = QtGui.QColor(color); self.wall.setAlpha(205)

    def paint(self, painter, option, index):
        frac = index.data(QtCore.Qt.ItemDataRole.UserRole)
        if frac:
            r = option.rect
            w = int(r.width() * min(float(frac), 1.0))
            col = self.wall if index.data(QtCore.Qt.ItemDataRole.UserRole + 1) else self.bar
            if self.side == "bid":
                painter.fillRect(QtCore.QRect(r.right() - w, r.top(), w, r.height()), col)
            else:
                painter.fillRect(QtCore.QRect(r.left(), r.top(), w, r.height()), col)
        super().paint(painter, option, index)


# ============================================================
#  Main window
# ============================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, events, bar_kind="time", bar_size=60, symbol="ASII", debug=False):
        super().__init__()
        self.setWindowTitle(f"IDX Orderflow — {symbol}")
        self.resize(1700, 1000)   # clamped to the actual screen by _ensure_on_screen()
        self.events = events
        self.bar_kind = bar_kind
        self.bar_size = bar_size
        self.symbol = symbol
        self.model = None
        self.cfg = dict(DEFAULTS)
        self.debug = debug
        self._last_batch_t = None     # wall-clock of the last live batch (stall detector)
        self._last_diag_t = 0.0       # throttle the in-window diag readout to ~1 Hz

        self._build_toolbar()
        self._build_statusbar()
        self._build_charts()
        self.fp_item.cfg = self.cfg
        self.delta_item.cfg = self.cfg
        self._build_dock()
        self._settings = QtCore.QSettings("orderflow", "of_app")
        self._restore_settings()
        self._apply_cfg()        # model is None here -> sets colormap/layout/timer, no rebuild
        self.rebuild()

    # ---- UI scaffolding ----
    def _build_toolbar(self):
        tb = self.addToolBar("controls")
        tb.setObjectName("controls")
        self.toolbar = tb
        # keep "controls" out of the right-click toolbar/dock menu — unchecking it
        # there hides the toolbar and saveState() would persist the hidden state
        tb.toggleViewAction().setVisible(False)
        tb.addWidget(QtWidgets.QLabel("  Bars: "))
        self.bar_combo = QtWidgets.QComboBox()
        self.bar_combo.addItems(["time", "tick", "volume"])
        self.bar_combo.setCurrentText(self.bar_kind)
        self.bar_combo.currentTextChanged.connect(self._on_bars_changed)
        tb.addWidget(self.bar_combo)
        tb.addWidget(QtWidgets.QLabel("  Size: "))
        self.size_spin = QtWidgets.QSpinBox()
        self.size_spin.setRange(1, 100000)
        self.size_spin.setValue(self.bar_size)
        self.size_spin.editingFinished.connect(self._on_bars_changed)
        tb.addWidget(self.size_spin)
        self.delta_cells = QtWidgets.QCheckBox("Δ cells")
        self.delta_cells.toggled.connect(self._toggle_cell_mode)
        tb.addWidget(self.delta_cells)
        tb.addWidget(QtWidgets.QLabel("  Big≥(lots): "))
        self.big_spin = QtWidgets.QSpinBox()
        self.big_spin.setRange(1, 1000000)
        self.big_spin.setValue(50)
        self.big_spin.editingFinished.connect(
            lambda: self._refresh_tape(self.model) if self.model else None)
        tb.addWidget(self.big_spin)
        center = QtWidgets.QToolButton()
        center.setText(" ⌖ Center ")
        center.setToolTip("Jump to the latest bars, keeping your zoom (Home)")
        center.setAutoRaise(True)
        center.clicked.connect(lambda: self._center_latest())   # lambda: clicked passes a bool
        tb.addWidget(center)
        self.follow_chk = QtWidgets.QCheckBox("Follow")
        self.follow_chk.setToolTip("Auto-scroll to new bars as they form; panning back turns it off")
        tb.addWidget(self.follow_chk)
        QtGui.QShortcut(QtGui.QKeySequence("Home"), self).activated.connect(self._center_latest)
        gear = QtWidgets.QToolButton()
        gear.setText("  ⚙  ")
        gear.setToolTip("Settings")
        gear.setAutoRaise(True)
        gear.clicked.connect(self._open_settings)
        tb.addWidget(gear)
        self.regime_lbl = QtWidgets.QLabel("")     # intraday regime chip (ER + realized vol)
        tb.addWidget(self.regime_lbl)
        self.status_lbl = QtWidgets.QLabel("   ")
        tb.addWidget(self.status_lbl)

    def _build_statusbar(self):
        """--debug: diagnostics live in a bottom status bar (grouped, health-colored),
        not squeezed into the toolbar. Four equal-stretch labels (FLOW/BOOK/FEED/CHECK)
        so the sections fill the full window width — no dead strip under the dock.
        Without --debug no status bar is created."""
        self.diag_lbls = []
        if not self.debug:
            return
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        sb.setStyleSheet("QStatusBar{background:#0e1114; border-top:1px solid #23282d;}"
                         "QLabel{font-family:Consolas; font-size:11px;}")
        for _ in range(4):
            lbl = QtWidgets.QLabel("")
            lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
            lbl.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            sb.addPermanentWidget(lbl, 1)
            self.diag_lbls.append(lbl)

    def _build_charts(self):
        central = QtWidgets.QWidget()
        v = QtWidgets.QVBoxLayout(central)
        v.setContentsMargins(6, 2, 6, 0)
        v.setSpacing(3)
        self.summary_lbl = QtWidgets.QLabel("")
        self.summary_lbl.setStyleSheet("font-family:Consolas; font-size:12px; color:#c8ccd0")
        v.addWidget(self.summary_lbl)
        self.glw = pg.GraphicsLayoutWidget()
        v.addWidget(self.glw, 1)
        self.setCentralWidget(central)

        self.p_fp = self.glw.addPlot(row=0, col=0, title="Footprint")
        self.fp_item = FootprintItem()
        self.p_fp.addItem(self.fp_item)
        self.p_fp.showGrid(x=False, y=True, alpha=0.2)
        # mouse pan/zoom only (not programmatic setRange) -> maybe disengage Follow
        self.p_fp.vb.sigRangeChangedManually.connect(self._on_manual_range)

        dot = QtCore.Qt.PenStyle.DotLine
        self.vwap_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#5ad1ff", width=1, style=dot),
                                         label="VWAP {value:.0f}",
                                         labelOpts={"position": 0.04, "color": "#5ad1ff"})
        self.vah_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#9aa0a6", width=1, style=dot),
                                        label="VAH {value:.0f}",
                                        labelOpts={"position": 0.04, "color": "#9aa0a6"})
        self.val_line = pg.InfiniteLine(angle=0, pen=pg.mkPen("#9aa0a6", width=1, style=dot),
                                        label="VAL {value:.0f}",   # offset x so a tight VA
                                        labelOpts={"position": 0.12, "color": "#9aa0a6"})
        for ln in (self.vwap_line, self.vah_line, self.val_line):
            ln.setVisible(False)
            self.p_fp.addItem(ln, ignoreBounds=True)

        self.p_delta = self.glw.addPlot(row=1, col=0)
        self.p_delta.setXLink(self.p_fp)
        self.p_delta.setYRange(0, 1, padding=0)
        self.p_delta.hideAxis("bottom")
        self.p_delta.getAxis("left").setStyle(showValues=False)
        self.p_delta.setMenuEnabled(False)
        self.p_delta.setMouseEnabled(x=True, y=False)
        for pl in (self.p_fp, self.p_delta):
            pl.getAxis("left").setWidth(54)   # equal axis width -> footer aligns under bars
        self.delta_item = DeltaFooterItem()
        self.p_delta.addItem(self.delta_item)

        self.p_vap = self.glw.addPlot(row=0, col=1, title="Vol@Price")
        self.p_vap.setMaximumWidth(230)
        self.p_vap.setYLink(self.p_fp)
        # width scale may be sqrt -> numeric x labels would lie; shape is the signal
        self.p_vap.getAxis("bottom").setStyle(showValues=False)

        self.p_hm = self.glw.addPlot(row=2, col=0, title="Liquidity heatmap")
        self.hm_img = SmoothImageItem()            # bilinear smoothing (Bookmap-like)
        self.p_hm.addItem(self.hm_img)
        self.p_hm.setYLink(self.p_fp)
        self._set_colormap(self.cfg["colormap"])   # heatmap LUT (cfg-driven)
        self.hm_price = self.p_hm.plot([], [], pen=pg.mkPen(240, 244, 250, 210, width=2.2))
        dash = QtCore.Qt.PenStyle.DashLine        # biggest resting bid/ask per column;
        shadow = pg.mkPen(8, 10, 12, 220, width=3)  # dark under-stroke -> visible on bright bands
        self.hm_bidwall = self.p_hm.plot([], [], pen=pg.mkPen(63, 226, 106, 230,
                                                              width=1.4, style=dash),
                                         shadowPen=shadow)
        self.hm_askwall = self.p_hm.plot([], [], pen=pg.mkPen(255, 84, 84, 230,
                                                              width=1.4, style=dash),
                                         shadowPen=shadow)
        self.hm_trades = pg.ScatterPlotItem(pxMode=True, pen=None)
        self.p_hm.addItem(self.hm_trades)
        self.hm_candles = HeatmapCandleItem()      # added last -> draws on top
        self.p_hm.addItem(self.hm_candles)

        self.p_cvd = self.glw.addPlot(row=3, col=0, title="CVD (lots)",
                                      axisItems={"bottom": pg.DateAxisItem(),
                                                 "left": CommaAxis(orientation="left")})
        self.cvd_curve = self.p_cvd.plot([], [], pen=pg.mkPen("#2ee6e6", width=2))

        # regime panel fills the otherwise-empty col-1 slot beside heatmap/CVD:
        # rolling ER history with the trend/chop thresholds, title = current state
        self.p_rg = self.glw.addPlot(row=2, col=1, rowspan=2, title="Regime")
        self.p_rg.setMaximumWidth(230)
        self.p_rg.setYRange(0, 1, padding=0.03)
        self.p_rg.setMouseEnabled(x=True, y=False)
        self.p_rg.showGrid(x=False, y=True, alpha=0.15)
        self.rg_curve = self.p_rg.plot([], [], pen=pg.mkPen("#e6b450", width=2))
        self.rg_trend_ln = pg.InfiniteLine(angle=0, pen=pg.mkPen("#3fe26a", width=1, style=dot),
                                           label="trend", labelOpts={"position": 0.14,
                                                                     "color": "#3fe26a"})
        self.rg_chop_ln = pg.InfiniteLine(angle=0, pen=pg.mkPen("#ff5454", width=1, style=dot),
                                          label="chop", labelOpts={"position": 0.14,
                                                                   "color": "#ff5454"})
        for ln in (self.rg_trend_ln, self.rg_chop_ln):
            self.p_rg.addItem(ln, ignoreBounds=True)

        self.glw.ci.layout.setRowStretchFactor(0, 6)   # footprint
        self.glw.ci.layout.setRowStretchFactor(1, 1)   # delta footer
        self.glw.ci.layout.setRowStretchFactor(2, 3)   # heatmap
        self.glw.ci.layout.setRowStretchFactor(3, 2)   # cvd

    def _build_dock(self):
        dock = QtWidgets.QDockWidget("Book / Tape")
        dock.setObjectName("booktape")
        dock.setFeatures(QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetMovable
                         | QtWidgets.QDockWidget.DockWidgetFeature.DockWidgetFloatable)
        self.dock = dock
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)
        self.dom_title = QtWidgets.QLabel("DOM ladder")
        lay.addWidget(self.dom_title)
        self.dom = QtWidgets.QTableWidget(0, 6)
        self.dom.setHorizontalHeaderLabels(["B.Freq", "B.Lot", "Bid", "Ask", "A.Lot", "A.Freq"])
        self.dom.verticalHeader().setVisible(False)
        self.dom.verticalHeader().setDefaultSectionSize(20)   # dense QT-style rows
        self._del_bid = DepthBarDelegate("bid", QtGui.QColor(63, 226, 106), self.dom)
        self._del_ask = DepthBarDelegate("ask", QtGui.QColor(255, 84, 84), self.dom)
        self.dom.setItemDelegateForColumn(1, self._del_bid)
        self.dom.setItemDelegateForColumn(4, self._del_ask)
        lay.addWidget(self.dom)
        self.dom_totals = QtWidgets.QLabel("")     # pinned Σ footer (never scrolls away)
        self.dom_totals.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.dom_totals.setStyleSheet("font-family:Consolas; font-size:12px; padding:1px 4px;")
        lay.addWidget(self.dom_totals)
        lay.addWidget(QtWidgets.QLabel("Trade tape"))
        self.tape = QtWidgets.QTableWidget(0, 4)
        self.tape.setHorizontalHeaderLabels(["Time", "Price", "Qty", "Side"])
        self.tape.verticalHeader().setVisible(False)
        lay.addWidget(self.tape)
        for tbl in (self.dom, self.tape):
            tbl.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)
            tbl.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        dock.setWidget(w)
        dock.setMinimumWidth(330)
        self.addDockWidget(QtCore.Qt.RightDockWidgetArea, dock)

    # ---- data / refresh ----
    def _on_bars_changed(self, *_):
        self.bar_kind = self.bar_combo.currentText()
        self.bar_size = self.size_spin.value()
        self.rebuild()

    def rebuild(self, preserve_view=False):
        vr = self.p_fp.viewRange() if preserve_view else None
        self.model = of_model.build_model(
            self.events, self.bar_kind, self.bar_size,
            heatmap_every_sec=self.cfg["hm_throttle"], heatmap_max=self.cfg["hm_window"])
        self.refresh()
        if vr is not None:                      # settings-driven rebuild: keep the trader's view
            self.p_fp.setRange(xRange=vr[0], yRange=vr[1], padding=0)
        else:
            self.p_fp.autoRange()   # frame data on first build / bar-basis change only

    def refresh(self):
        m = self.model
        self.fp_item.set_model(m)
        self.delta_item.set_model(m)
        self._refresh_levels(m)
        self._refresh_vap(m)
        self._refresh_heatmap(m)
        unit = 100 if self.cfg["header_units"] == "lots" else 1
        cx = np.asarray(m.cvd_x, dtype=float)
        cy = np.asarray(m.cvd_y, dtype=float) / unit   # panel matches the volume units
        self.p_cvd.setTitle(f"CVD ({self.cfg['header_units']})")
        if cx.size > 1:
            g = np.where(np.diff(cx) > 300)[0]   # >5 min without trades = capture hole
            if g.size:                           # break the line instead of faking a flat
                cx = np.insert(cx, g + 1, cx[g])
                cy = np.insert(cy, g + 1, np.nan)
        self.cvd_curve.setData(cx, cy, connect="finite")
        self._refresh_dom(m)
        self._refresh_tape(m)
        self._refresh_summary(m)
        self._refresh_regime(m)
        self.status_lbl.setText(
            f"   {self.symbol}  bars={len(m.bar_ids())}  trades={len(m.trades)}  "
            f"vol={m.total_volume() / unit:,.0f} {self.cfg['header_units']}  "
            f"CVD={(m.cvd_y[-1] if m.cvd_y else 0) / unit:,.0f}")
        if self.follow_chk.isChecked():      # auto-scroll X to the live edge (keeps Y)
            self._center_latest(center_y=False)
        if self.debug:                       # in-window diag, throttled to ~1 Hz
            now = time.monotonic()
            if now - self._last_diag_t > 1.0:
                self._last_diag_t = now
                self._refresh_diag()

    @staticmethod
    def _diag_text(d, age=None):
        """One-line health string from model.diag(): per-bar trade rate (mean/last),
        classification split, book sanity, spread distribution (cur/median/p90), and
        the footprint==VAP invariant."""
        def f0(x):
            return "—" if x is None else f"{x:.0f}"
        parts = [f"bars={d['bars']}", f"tpb={d['tpb_mean']:.0f}/{d['tpb_last']}",
                 f"tr={d['trade_frames']}", f"buy%={d['buy_pct']:.0f}",
                 f"(Q{d['cls_quote']}/T{d['cls_tick']}/C{d['cls_carry']})",
                 f"book={d['book_frames']}f", f"crossed={d['crossed_book']}",
                 f"noBook={d['no_book']}", f"dedup={d['dedup_skips']}",
                 f"hm={d['heatmap_cols']}c/trim{d['heatmap_trimmed']}",
                 f"spr={f0(d['spread'])}/{f0(d['spread_med'])}/{f0(d['spread_p90'])}",
                 f"vap={d['vap_sh'] / 1e6:.2f}M", f"fp={d['fp_sh'] / 1e6:.2f}M"]
        if abs(d["vap_sh"] - d["fp_sh"]) > 0.5:
            parts.append("‼FP≠VAP")           # aggregation invariant broken
        if age is not None:
            parts.append(f"age={age:.1f}s")    # seconds since last live batch (stall)
        return " ".join(parts)

    @staticmethod
    def _diag_html(d, age=None):
        """Status-bar version of the diagnostics: grouped sections, quiet grey when
        healthy, amber/red only when a value needs attention."""
        DIM, VAL, WARN, BAD = "#5f6b76", "#c9ced4", "#e6b450", "#ff5454"
        GRN, RED = "#3fe26a", "#ff5454"

        def v(txt, color=VAL, bold=False):
            w = "font-weight:600;" if bold else ""
            return f"<span style='color:{color};{w}'>{txt}</span>"

        def lab(txt):
            return f"<span style='color:{DIM}'>{txt}</span>"

        DOT = f"<span style='color:{DIM}'>&nbsp;·&nbsp;</span>"

        bp = d["buy_pct"]
        bp_c = GRN if bp >= 55 else RED if bp <= 45 else VAL
        burst = d["tpb_last"] > 3 * max(d["tpb_mean"], 1)
        flow = (lab("FLOW") + "&nbsp;" + v(f"tr {d['trade_frames']:,}") + DOT
                + v(f"buy {bp:.0f}%", bp_c) + DOT
                + v(f"Q/T/C {d['cls_quote']}/{d['cls_tick']}/{d['cls_carry']}") + DOT
                + v(f"tpb {d['tpb_mean']:.0f}/{d['tpb_last']}",
                    WARN if burst else VAL, bold=burst))

        s_now = "—" if d["spread"] is None else f"{d['spread']:.0f}"
        wide = (d["spread_p90"] or 0) > 2 * (d["spread_med"] or 1)
        crossed_hot = d["crossed_book"] > max(5, 0.01 * max(d["book_frames"], 1))
        book = (lab("BOOK") + "&nbsp;"
                + v(f"spr {s_now}/{d['spread_med'] or 0:.0f}/{d['spread_p90'] or 0:.0f}",
                    WARN if wide else VAL) + DOT
                + v(f"crossed {d['crossed_book']}", WARN if crossed_hot else VAL) + DOT
                + v(f"frames {d['book_frames']:,}"))

        if age is None:
            age_h = v("age —")
        else:
            a_c = VAL if age < 3 else WARN if age < 10 else BAD
            age_h = v(f"age {age:.1f}s", a_c, bold=age >= 10)
        feed = (lab("FEED") + "&nbsp;" + age_h + DOT
                + v(f"dedup {d['dedup_skips']}") + DOT
                + v(f"noBook {d['no_book']}") + DOT
                + v(f"hm {d['heatmap_cols']}/{d['heatmap_trimmed']}"))

        ok = abs(d["vap_sh"] - d["fp_sh"]) <= 0.5
        chk = (lab("CHECK") + "&nbsp;"
               + (v(f"vap {d['vap_sh'] / 1e6:.2f}M = fp {d['fp_sh'] / 1e6:.2f}M ✓", "#7fd18b")
                  if ok else
                  v(f"‼ FP≠VAP ({d['fp_sh'] / 1e6:.2f}M vs {d['vap_sh'] / 1e6:.2f}M)",
                    BAD, bold=True)))

        return flow, book, feed, chk          # one section per status-bar slot

    def _refresh_diag(self, to_stderr=False):
        if not self.model:
            return ""
        age = (time.monotonic() - self._last_batch_t) if self._last_batch_t else None
        d = self.model.diag()
        text = self._diag_text(d, age)
        if self.diag_lbls:
            for lbl, html in zip(self.diag_lbls, self._diag_html(d, age)):
                lbl.setText(html)
        if to_stderr:
            ts = datetime.now().strftime("%H:%M:%S")
            print(f"[diag {ts}] {text}", file=sys.stderr, flush=True)
        return text

    def _style_regime(self, bg, fg):
        self.regime_lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; font-family:Consolas; font-weight:600; "
            f"border-radius:3px; padding:1px 7px; margin:0 4px;")

    def _refresh_regime(self, m):
        c = self.cfg
        if not c["show_regime"]:
            self.regime_lbl.setVisible(False)
            return
        self.regime_lbl.setVisible(True)
        self.rg_trend_ln.setPos(c["er_trend"])
        self.rg_chop_ln.setPos(c["er_chop"])
        r = m.regime(c["regime_window"], c["regime_warmup_min"], c["er_trend"], c["er_chop"])
        if not r["ready"]:                          # warm-up countdown
            mins = int(r.get("span", 0) // 60)
            self.regime_lbl.setText(f"REGIME: warming up ({mins}/{c['regime_warmup_min']} min)")
            self._style_regime("#22262b", "#7a828c")
            self.rg_curve.setData([], [])
            self.p_rg.setTitle(f"<span style='color:#7a828c; font-size:9pt'>"
                               f"warm-up {mins}/{c['regime_warmup_min']}m</span>")
            return
        core = r["core"]
        bg, fg = {"TREND↑": ("#173e28", "#4fe27a"), "TREND↓": ("#3e1a1a", "#ff6a6a"),
                  "CHOP": ("#22262b", "#c9ced4")}.get(core, ("#2c2620", "#e6b450"))
        vr = f"  VR {r['vr']:.2f}" if r.get("vr") is not None else ""
        self.regime_lbl.setText(f"{r['label']}   ER {r['er']:.2f}{vr}")
        self.regime_lbl.setToolTip(
            f"Efficiency Ratio {r['er']:.2f} (trend≥{c['er_trend']}, chop≤{c['er_chop']})  ·  "
            f"realized-vol pctile {r['rv_pct']:.0f}  ·  variance-ratio "
            f"{('%.2f' % r['vr']) if r.get('vr') is not None else '—'} (>1 trend, <1 mean-revert)")
        self._style_regime(bg, fg)
        pts = m.er_series(c["regime_window"])       # ER history in the side panel
        self.rg_curve.setData([i for i, _e in pts], [e for _i, e in pts])
        self.p_rg.setTitle(f"<span style='color:{fg}; font-weight:600; font-size:9pt'>{core}</span>"
                           f"<span style='color:#7a828c; font-size:9pt'>&nbsp; ER {r['er']:.2f}"
                           f" · v{r['rv_pct']:.0f}</span>")   # full label stays on the toolbar chip

    def _refresh_levels(self, m):
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

    def _refresh_summary(self, m):
        s = m.summary
        if not s:
            self.summary_lbl.setText("")
            return
        def n(k):
            v = s.get(k)
            return f"{v:,.0f}" if isinstance(v, (int, float)) else "—"
        val = s.get("val") or 0
        lot = (s.get("vol_sh") or 0) / 100
        self.summary_lbl.setText(
            f"Open {n('open')}   Prev {n('prev')}   High {n('high')}   Low {n('low')}   "
            f"Avg {n('avg')}   Last {n('last')}   Lot {lot:,.0f}   "
            f"Val {val / 1e9:,.2f}B   Trades {n('trades')}")

    def _refresh_vap(self, m):
        self.p_vap.clear()
        raw = m.vap_rows()
        if not raw:
            return
        tick = m.tick_size() or 10
        # snap off-tick negotiated prints onto the tick grid — overlapping rows
        # rendered as "warped" blocks otherwise (e.g. a 4,788 print vs the 4,790 row)
        agg = {}
        for p, b, s, _t in raw:
            g = round(p / tick) * tick
            e = agg.setdefault(g, [0.0, 0.0])
            e[0] += b
            e[1] += s
        rows = [(p, bs[0], bs[1], bs[0] + bs[1]) for p, bs in sorted(agg.items())]
        ys = [r[0] for r in rows]
        h = tick * 0.8
        if self.cfg["vap_scale"] == "sqrt":
            # outlier-proof: a block print stays biggest but stops flattening the rest
            buys = [b ** 0.5 for _p, b, _s, _t in rows]
            sells = [s ** 0.5 for _p, _b, s, _t in rows]
        else:
            buys = [r[1] for r in rows]
            sells = [r[2] for r in rows]
        # butterfly profile: sells mirror left of zero, buys right; gold wash on POC
        nopen = pg.mkPen(None)                    # no 1px outline on near-zero rows
        self.p_vap.addItem(pg.BarGraphItem(x0=[-s for s in sells], y=ys, height=h,
                                           width=sells, brush=pg.mkBrush(255, 84, 84, 190),
                                           pen=nopen))
        self.p_vap.addItem(pg.BarGraphItem(x0=0, y=ys, height=h, width=buys,
                                           brush=pg.mkBrush(63, 226, 106, 190), pen=nopen))
        ipoc = max(range(len(rows)), key=lambda i: rows[i][3])
        self.p_vap.addItem(pg.BarGraphItem(x0=[-sells[ipoc]], y=[ys[ipoc]], height=h,
                                           width=[sells[ipoc] + buys[ipoc]],
                                           brush=pg.mkBrush(255, 224, 102, 80), pen=nopen))
        # fit the widest bars exactly -> the block can never run off the panel
        self.p_vap.setXRange(-max(max(sells), 1) * 1.08, max(max(buys), 1) * 1.08, padding=0)

    def _refresh_heatmap(self, m):
        c = self.cfg
        cols = m.heatmap
        if not cols:
            self.hm_img.clear()
            self.hm_price.setData([], [])
            self.hm_bidwall.setData([], [])
            self.hm_askwall.setData([], [])
            self.hm_trades.setData([])
            self.hm_candles.set_bars([])
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
            # Rank-based (histogram equalization): color = size *percentile*, so the
            # field stays readable no matter the size distribution. gamma darkens the
            # bulk — median level ~ rank 0.5 -> 0.5^γ of the palette (deep blue at
            # γ=2); only the top few percent reach yellow/white, like Bookmap.
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
                arr = np.sqrt(arr)               # compress heavy tails ("linear" = as-is)
            nz = arr[arr > 0]
            hi = float(np.percentile(nz, c["hm_pctile"])) if nz.size else 1.0  # walls clip
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
            # price level holding the largest resting size on each side of the mid —
            # the gap between the price line and these = distance to the big orders
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
                    bx.append(j); by.append(bw)
                if aw is not None:
                    ax_.append(j); ay.append(aw)
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
                    continue                      # bar outside the rolling window
                cells = m.footprint[b]
                x0 = float(np.interp(meta["t0"], col_eps, xs))
                x1 = float(np.interp(meta["t1"], col_eps, xs))
                if x1 - x0 < 1.0:
                    x1 = x0 + 1.0                 # single-print bar still visible
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
            qref = float(np.percentile([r["qty"] for _e, r in win], c["bubble_ref_pct"])) or 1
            minfrac = c["bubble_min_frac"]
            for ep, r in win:
                frac = (r["qty"] / qref) ** 0.5
                if frac < minfrac:              # hide the small prints -> less clutter
                    continue
                x = float(np.interp(ep, col_eps, xs))
                sz = min(2 + 11 * frac, 15)
                spots.append({"pos": (x, r["price"]), "size": sz,
                              "brush": buy_b if r.get("side") == "buy" else sell_b,
                              "pen": None})
        self.hm_trades.setData(spots)

    def _smooth(self, attr, val, a=0.15):
        """EMA toward val; used to keep DOM depth-bar scaling steady across ticks."""
        prev = getattr(self, attr, val)
        new = (1 - a) * prev + a * val
        setattr(self, attr, new)
        return new or 1

    def _dom_header_key(self, pro):
        return "dom_header_pro" if pro else "dom_header_classic"

    def _apply_dom_mode(self):
        """Switch the DOM table between Stockbit-style side-by-side (classic) and
        the Quantower-style centered ladder (pro); apply the column-resize mode
        (drag-to-resize widths persist per mode)."""
        pro = self.cfg["dom_pro"]
        mode = (pro, bool(self.cfg["dom_resizable"]))
        prev = getattr(self, "_dom_mode", None)
        if prev == mode:
            return
        if prev is not None and prev[1]:          # keep the outgoing mode's widths
            self._settings.setValue(self._dom_header_key(prev[0]),
                                    self.dom.horizontalHeader().saveState())
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
            self.dom.setHorizontalHeaderLabels(["B.Freq", "B.Lot", "Bid", "Ask", "A.Lot", "A.Freq"])
            self.dom.setItemDelegateForColumn(1, self._del_bid)
            self.dom.setItemDelegateForColumn(4, self._del_ask)
            self.dom_title.setText("DOM ladder")
        self.dom_totals.setVisible(pro)
        hdr = self.dom.horizontalHeader()
        if mode[1]:                               # drag-to-resize, widths persisted
            hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
            hdr.setStretchLastSection(True)
            st = self._settings.value(self._dom_header_key(pro))
            if isinstance(st, (QtCore.QByteArray, bytes, bytearray)):
                try:
                    hdr.restoreState(st)
                except (TypeError, ValueError):
                    pass
        else:                                     # classic auto-fit
            hdr.setStretchLastSection(False)
            hdr.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Stretch)

    def _refresh_dom(self, m):
        if self.cfg["dom_pro"]:
            self._refresh_dom_pro(m)
        else:
            self._refresh_dom_classic(m)

    def _refresh_dom_pro(self, m):
        """Quantower-style DOM trader: one centered price ladder (asks above the
        spread row, bids below), depth bars, session-volume column, Σ totals row,
        best bid & offer highlighted. Level-1 strip in the dock title."""
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
        bold = QtGui.QFont(); bold.setBold(True)
        DIMV = QtGui.QColor(140, 152, 168)
        ASK_BG = QtGui.QColor(84, 32, 32)
        BID_BG = QtGui.QColor(24, 66, 40)

        def lot_item(lot):
            it = QtWidgets.QTableWidgetItem(f"{lot:,.0f}" if lot else "")
            it.setTextAlignment(rgt)
            if lot and show_bars:
                it.setData(UR, lot / maxlot)
                it.setData(UR + 1, bool(wall and lot >= wall))
            return it

        # liquidity changes vs the previous refresh (QT's "+x/-x" column):
        # + = stacking (size added at the level), - = pulling
        prev = getattr(self, "_dom_prev", {})
        cur = {p: v / 100 for p, v in m.book.bids.items()}
        cur.update({p: v / 100 for p, v in m.book.asks.items()})

        self.dom.setRowCount(len(rows))           # BBO rows adjacent, spread in title
        for r, (price, bf, bv, af, av) in enumerate(rows):
            is_ask = av is not None
            self.dom.setItem(r, 0, lot_item((bv or 0) / 100))
            pit = QtWidgets.QTableWidgetItem(f"{price:,.0f}")
            pit.setTextAlignment(ctr)
            pit.setForeground(BEAR if is_ask else BULL)
            if price == ba or price == bb:        # best bid & offer stand out
                pit.setFont(bold)
                pit.setBackground(ASK_BG if price == ba else BID_BG)
            self.dom.setItem(r, 1, pit)
            self.dom.setItem(r, 2, lot_item((av or 0) / 100))
            now = cur.get(price, 0)
            chg = now - prev.get(price, now)
            cit = QtWidgets.QTableWidgetItem(f"{chg:+,.0f}" if abs(chg) >= 1 else "")
            cit.setTextAlignment(rgt)
            cit.setForeground(BULL if chg > 0 else BEAR)
            self.dom.setItem(r, 3, cit)
            v = m.vap.get(price)
            vt = QtWidgets.QTableWidgetItem(
                f"{(v['buy'] + v['sell']) / 100:,.0f}" if v else "")
            vt.setTextAlignment(rgt)
            vt.setForeground(DIMV)
            self.dom.setItem(r, 4, vt)
        self._dom_prev = cur

        # pinned Σ footer under the table — visible without scrolling
        tb, ta = sum(blots), sum(alots)
        tv = sum((m.vap[p]["buy"] + m.vap[p]["sell"]) / 100
                 for p, _bf, _bv, _af, _av in rows if p in m.vap)
        bid_pct = 100 * tb / (tb + ta) if (tb + ta) else 50
        self.dom_totals.setText(
            "<span style='color:#5f6b76'>Σ&nbsp;</span>"
            f"<span style='color:#3fe26a;font-weight:600'>B {tb:,.0f}</span>"
            "<span style='color:#5f6b76'> / </span>"
            f"<span style='color:#ff5454;font-weight:600'>A {ta:,.0f}</span>"
            f"<span style='color:#5f6b76'> lots &nbsp;·&nbsp; {bid_pct:.0f}% bid "
            f"&nbsp;·&nbsp; vol {tv:,.0f}</span>")

        last = m.trades[-1]["price"] if m.trades else None
        if last is not None and spread is not None:
            self.dom_title.setText(f"DOM — Last {last:,.0f}   Spread {spread:,.0f}")

        # keep the BBO in view: only scroll if it drifted out of the viewport
        if ba is not None:
            i_ba = next((i for i, t in enumerate(rows) if t[0] == ba), None)
            if i_ba is not None:
                it = self.dom.item(i_ba, 1)
                if it is not None and not self.dom.viewport().rect().intersects(
                        self.dom.visualItemRect(it)):
                    self.dom.scrollToItem(
                        it, QtWidgets.QAbstractItemView.ScrollHint.PositionAtCenter)

    def _refresh_dom_classic(self, m):
        c = self.cfg
        bids, asks = m.two_sided_ladder(c["dom_depth"])
        n = max(len(bids), len(asks))
        self.dom.setRowCount(n)
        lots = sorted(v / 100 for _p, _f, v in (bids + asks) if v)
        cur_max = lots[-1] if lots else 1
        cur_med = lots[len(lots) // 2] if lots else 0
        maxlot = self._smooth("_dom_max", cur_max)   # EMA -> bars don't rescale every tick
        wall = c["wall_mult"] * self._smooth("_dom_med", cur_med)
        show_bars = c["show_depth_bars"]
        ctr = QtCore.Qt.AlignmentFlag.AlignCenter
        rgt = QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        UR = QtCore.Qt.ItemDataRole.UserRole
        bold = QtGui.QFont()
        bold.setBold(True)

        def freq_item(f):
            return QtWidgets.QTableWidgetItem(f"{f:,}" if f else "")

        def lot_item(v):
            lot = v / 100 if v else None
            it = QtWidgets.QTableWidgetItem(f"{lot:,.0f}" if lot else "")
            it.setTextAlignment(rgt)
            if lot and show_bars:
                it.setData(UR, lot / maxlot)
                it.setData(UR + 1, bool(wall and lot >= wall))
            return it

        def price_item(p, color, top):
            it = QtWidgets.QTableWidgetItem(f"{p:,.0f}")
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
                for c in (0, 1, 2):
                    self.dom.setItem(i, c, QtWidgets.QTableWidgetItem(""))
            if i < len(asks):
                ap, af, av = asks[i]
                self.dom.setItem(i, 3, price_item(ap, BEAR, i == 0))
                self.dom.setItem(i, 4, lot_item(av))
                self.dom.setItem(i, 5, freq_item(af))
            else:
                for c in (3, 4, 5):
                    self.dom.setItem(i, c, QtWidgets.QTableWidgetItem(""))

    def _toggle_cell_mode(self, on):
        self.fp_item.cell_mode = "delta" if on else "bidask"
        if self.model:
            self.fp_item.set_model(self.model)

    # ---- view centering / follow ----
    def _center_latest(self, center_y=True):
        """Snap X to the newest bars and (optionally) center Y on the last traded
        price — preserving the current zoom spans, unlike autoRange."""
        m = self.model
        n = len(m.bar_ids()) if m else 0
        if not n:
            return
        (x0, x1), (y0, y1) = self.p_fp.viewRange()
        xspan = max(x1 - x0, 2.0)
        right = n + max(0.05 * xspan, 0.8)   # margin so the live bar isn't glued to the edge
        self.p_fp.setXRange(right - xspan, right, padding=0)
        if center_y and m.trades:
            cy = m.trades[-1]["price"]
            yspan = max(y1 - y0, self.fp_item._tickval * 4)
            self.p_fp.setYRange(cy - yspan / 2, cy + yspan / 2, padding=0)

    def _on_manual_range(self, *_):
        """Panning away from the live edge turns Follow off; zooming while still
        at the edge keeps following."""
        if not (self.follow_chk.isChecked() and self.model):
            return
        if self.p_fp.viewRange()[0][1] < len(self.model.bar_ids()) - 0.5:
            self.follow_chk.setChecked(False)

    # ---- settings / cfg application ----
    def _set_colormap(self, name):
        """Set the heatmap LUT. 'bookmap' is the hand-tuned palette; the rest are
        pulled from pyqtgraph/matplotlib, falling back to bookmap if unavailable."""
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
                               [22, 176, 200, 255], [122, 216, 74, 255], [255, 216, 32, 255],
                               [255, 255, 255, 255]], dtype=np.ubyte)
            lut = pg.ColorMap(bm_pos, bm_col).getLookupTable(0.0, 1.0, 256)
        self.hm_img.setLookupTable(lut)

    def _apply_layout(self):
        """Show/hide panels. A QGraphicsGridLayout keeps a hidden cell's *minimum*
        height, so zeroing row stretch alone leaves an empty band — also zero the
        hidden row's min/max height so the visible panels reclaim the space (and
        restore it when shown). Row 0 holds both footprint and vol@price."""
        c = self.cfg
        self.p_fp.setVisible(c["show_footprint"])
        self.p_vap.setVisible(c["show_vap"])
        self.p_delta.setVisible(c["show_delta"])
        self.p_hm.setVisible(c["show_heatmap_panel"])
        self.p_cvd.setVisible(c["show_cvd"])
        self.p_rg.setVisible(c["show_regime"])
        L = self.glw.ci.layout
        vis = {0: c["show_footprint"] or c["show_vap"], 1: c["show_delta"],
               2: c["show_heatmap_panel"], 3: c["show_cvd"]}
        stretch = {0: 6, 1: 1, 2: 3, 3: 2}
        for row, on in vis.items():
            L.setRowStretchFactor(row, stretch[row] if on else 0)
            L.setRowMinimumHeight(row, 0)
            L.setRowMaximumHeight(row, 1_000_000 if on else 0)

    def _apply_cfg(self, rebuild=False):
        """Apply the full cfg: colormap, panel layout, live timer, then refresh.
        Only the model-level keys (heatmap window/throttle) need a full rebuild;
        everything else is a draw-time read of cfg, so a plain refresh() suffices
        and avoids the autoRange snap-back. During __init__ the model is None, so
        nothing here re-renders and the explicit rebuild() runs afterward."""
        self._set_colormap(self.cfg["colormap"])
        self._apply_layout()
        self._apply_dom_mode()
        t = getattr(self, "timer", None)
        if t:
            t.setInterval(max(40, int(1000 / max(self.cfg["live_hz"], 1))))
        if self.model is None:
            return
        if rebuild:
            self.rebuild(preserve_view=True)
        else:
            self.refresh()

    def _open_settings(self):
        SettingsDialog(self.cfg, self._on_settings_applied, self).exec()

    def _on_settings_applied(self, vals):
        old = dict(self.cfg)
        self.cfg.update(vals)
        need_rebuild = any(old.get(k) != self.cfg.get(k) for k in MODEL_CFG_KEYS)
        self._apply_cfg(rebuild=need_rebuild)
        self._save_settings()

    def _restore_settings(self):
        s = self._settings
        for k, default in DEFAULTS.items():     # tunable cfg keys (Settings panel)
            v = s.value(f"cfg/{k}")
            if v is None:
                continue
            kind, spec = SPEC_BY_KEY.get(k, (None, None))
            try:                                 # a stale/garbled/cross-version value must
                if isinstance(default, bool):    # not crash startup -> keep the default
                    val = v in (True, "true", "True", 1, "1")
                elif isinstance(default, int):   # bool checked first (bool subclasses int)
                    val = int(v)
                elif isinstance(default, float):
                    val = float(v)
                else:
                    val = str(v)
            except (TypeError, ValueError):
                continue
            if kind in ("int", "double") and spec:
                val = min(max(val, spec[0]), spec[1])    # clamp to the dialog's range
            elif kind == "choice" and spec and val not in spec:
                continue                                  # ignore an unknown choice
            self.cfg[k] = val
        bk = s.value("bar_kind")
        if bk:
            self.bar_kind = bk
            self.bar_combo.blockSignals(True)
            self.bar_combo.setCurrentText(bk)
            self.bar_combo.blockSignals(False)
        try:
            bs = s.value("bar_size")
            if bs is not None:
                self.bar_size = int(bs)
                self.size_spin.setValue(int(bs))
        except (TypeError, ValueError):
            pass
        if s.value("cell_mode") == "delta":
            self.delta_cells.setChecked(True)
        try:
            bg = s.value("big_lots")
            if bg is not None:
                self.big_spin.setValue(int(bg))
        except (TypeError, ValueError):
            pass
        geo = s.value("geometry")
        if isinstance(geo, (QtCore.QByteArray, bytes, bytearray)):
            try:
                self.restoreGeometry(geo)
            except (TypeError, ValueError):
                pass
        st = s.value("winstate")
        if isinstance(st, (QtCore.QByteArray, bytes, bytearray)):
            try:
                self.restoreState(st)
            except (TypeError, ValueError):
                pass
        self.dock.setVisible(True)        # never let the Book/Tape dock stay hidden
        if self.dock.isFloating():
            self.dock.setFloating(False)
        self.toolbar.setVisible(True)     # nor the toolbar (a saved hidden state)
        self._ensure_on_screen()

    def _ensure_on_screen(self):
        """Clamp/recenter so the window can't open off-screen or larger than the
        screen (e.g. a geometry saved on a bigger or differently-scaled display,
        or a now-disconnected second monitor). Safe to call before and after show."""
        if self.windowState() & QtCore.Qt.WindowState.WindowMaximized:
            return  # maximized already fills the current screen
        app = QtWidgets.QApplication.instance()
        center = self.frameGeometry().center()
        scr = app.screenAt(center) or app.primaryScreen()
        avail = scr.availableGeometry()
        if (avail.contains(center)
                and self.width() <= avail.width()
                and self.height() <= avail.height()):
            return
        w = max(800, min(self.width(), avail.width() - 20))
        h = max(500, min(self.height(), avail.height() - 60))
        self.resize(w, h)
        self.move(avail.left() + (avail.width() - w) // 2,
                  avail.top() + (avail.height() - h) // 2)

    def _save_settings(self):
        s = getattr(self, "_settings", None)
        if s is None:
            return
        s.setValue("bar_kind", self.bar_kind)
        s.setValue("bar_size", self.bar_size)
        s.setValue("cell_mode", self.fp_item.cell_mode)
        s.setValue("big_lots", self.big_spin.value())
        for k, v in self.cfg.items():
            s.setValue(f"cfg/{k}", v)
        mode = getattr(self, "_dom_mode", None)
        if mode is not None and mode[1]:          # persist dragged DOM column widths
            s.setValue(self._dom_header_key(mode[0]),
                       self.dom.horizontalHeader().saveState())
        s.setValue("geometry", self.saveGeometry())
        s.setValue("winstate", self.saveState())

    @staticmethod
    def _fmt_time(r, ms=3):
        t = r.get("trade_time") or r.get("recv_iso") or ""
        if "T" in t:
            t = t.split("T", 1)[1]
        if "." in t:
            hms, frac = t.split(".", 1)
            return hms + ("." + frac[:ms] if ms > 0 else "")   # fractional-second digits
        return t

    def _refresh_tape(self, m):
        c = self.cfg
        recent = m.trades[-c["tape_rows"]:][::-1]
        self.tape.setRowCount(len(recent))
        big = self.big_spin.value() * 100      # lots -> shares
        bigfont = QtGui.QFont()
        bigfont.setBold(True)
        bigbg = QtGui.QColor(74, 62, 30)
        for i, r in enumerate(recent):
            isbig = r["qty"] >= big
            items = [QtWidgets.QTableWidgetItem(self._fmt_time(r, c["time_ms"])),
                     QtWidgets.QTableWidgetItem(f"{r['price']:,.0f}"),
                     QtWidgets.QTableWidgetItem(f"{r['qty']:,.0f}"),
                     QtWidgets.QTableWidgetItem(r.get("side", ""))]
            items[3].setForeground(BULL if r.get("side") == "buy" else BEAR)
            for j, it in enumerate(items):
                if isbig:
                    it.setBackground(bigbg)
                    if j > 0:            # don't bold Time — bold text overflows the column
                        it.setFont(bigfont)
                self.tape.setItem(i, j, it)

    # ---- live feed support ----
    def attach_live(self, symbol, persist=True):
        self.thread = FeedThread(symbol, persist)
        self.thread.batch.connect(self._on_live_batch)
        self.thread.status.connect(lambda s: self.status_lbl.setText("   " + s))
        self.thread.start()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._live_refresh)
        self.timer.start(max(40, int(1000 / max(self.cfg["live_hz"], 1))))  # live redraw rate
        self._dirty = False
        if self.debug:                 # periodic counter dump to stderr (survives a hard kill)
            self.diag_timer = QtCore.QTimer(self)
            self.diag_timer.timeout.connect(lambda: self._refresh_diag(to_stderr=True))
            self.diag_timer.start(3000)

    @QtCore.Slot(list)
    def _on_live_batch(self, evs):
        self.events.extend(evs)
        for ev in evs:                 # feed the existing model incrementally
            self.model.on_event(ev)
        self._dirty = True
        self._last_batch_t = time.monotonic()

    def _live_refresh(self):
        if getattr(self, "_dirty", False):
            self._dirty = False
            self.refresh()             # redraw from current model (no full rebuild)

    def closeEvent(self, ev):
        self._save_settings()
        for attr in ("timer", "diag_timer"):
            t = getattr(self, attr, None)
            if t:
                t.stop()
        th = getattr(self, "thread", None)
        if th:
            th.stop()
            th.wait(2000)
        super().closeEvent(ev)


# ============================================================
#  Live feed thread (asyncio -> Qt signals)
# ============================================================
class FeedThread(QtCore.QThread):
    batch = QtCore.Signal(list)
    status = QtCore.Signal(str)

    def __init__(self, symbol, persist=True):
        super().__init__()
        self.symbol = symbol
        self.persist = persist
        self._loop = None
        self._task = None

    async def _pump(self):
        import asyncio
        buf, last = [], 0.0
        loop = asyncio.get_event_loop()
        async for ev in of_feed.live_feed(self.symbol, persist=self.persist):
            if ev[0] == "status":
                self.status.emit(ev[1])
                continue
            buf.append(ev)
            now = loop.time()
            if now - last > 0.1:
                self.batch.emit(buf); buf = []; last = now
        if buf:
            self.batch.emit(buf)

    def run(self):
        import asyncio
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._pump())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        finally:
            self._loop.close()

    def stop(self):
        if self._loop and self._task and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._task.cancel)


# ============================================================
#  Entry point
# ============================================================
def _event_date(ev):
    if ev[0] == "book":
        return (ev[4] or "")[:10]
    if ev[0] == "trade":
        return (ev[1].get("recv_iso") or ev[1].get("trade_time") or "")[:10]
    return ""


def _event_symbol(ev):
    if ev[0] == "book":
        return ev[1]
    return ev[1].get("symbol") if isinstance(ev[1], dict) else None


def load_history(mode, symbol=None):
    """Preload prior captured CSV history for live mode. 'today' keeps only
    today's events (so old days don't pollute the chart); 'all' keeps everything;
    'none' starts blank. Filtered to `symbol` so a different ticker starts clean."""
    if mode == "none":
        return []
    hist = list(of_feed.replay_feed())
    if mode == "today":
        today = datetime.now().strftime("%Y-%m-%d")
        hist = [e for e in hist if _event_date(e) == today]
    if symbol:
        hist = [e for e in hist if _event_symbol(e) == symbol]
    return hist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="connect + chart live")
    ap.add_argument("--replay", action="store_true", help="chart captured CSVs (default)")
    ap.add_argument("--symbol", default="ASII")
    ap.add_argument("--bars", default="time", choices=["time", "tick", "volume"])
    ap.add_argument("--size", type=int, default=60)
    ap.add_argument("--shot", metavar="PNG", help="render once to PNG and exit (headless)")
    ap.add_argument("--secs", type=int, default=8, help="seconds to run before a live --shot")
    ap.add_argument("--history", choices=["today", "all", "none"], default="today",
                    help="live mode: preload prior captured CSV history (default today)")
    ap.add_argument("--reset-layout", action="store_true",
                    help="forget saved window geometry/dock layout (use if it opens off-screen)")
    ap.add_argument("--reset-settings", action="store_true",
                    help="forget all saved Settings-panel values (recover from a bad config)")
    ap.add_argument("--debug", action="store_true",
                    help="show diagnostics counters (toolbar readout + stderr dump every 3s live)")
    ap.add_argument("--view-only", action="store_true",
                    help="live chart without writing CSVs — use when stockbit_capture.py "
                         "runs separately (two writers would corrupt the files)")
    ap.add_argument("--grab", action="store_true",
                    help="grab a fresh session token on launch (needs `python -m orderflow.token --login` "
                         "done once); falls back to the existing frame if it fails")
    args = ap.parse_args()

    if args.grab and args.live:              # refresh the token as part of activation
        from . import token as grab_token
        ok, msg = grab_token.refresh()
        print(("token: " if ok else "token grab skipped — ") + msg,
              file=sys.stdout if ok else sys.stderr)

    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Segoe UI", 9))
    st = QtCore.QSettings("orderflow", "of_app")
    if args.reset_layout:
        st.remove("geometry")
        st.remove("winstate")
    if args.reset_settings:
        st.remove("cfg")          # the whole cfg/* group

    sym = args.symbol.upper()
    # The General-tab "history" preload is a startup-time decision, so it only takes
    # effect on relaunch: honor the saved value unless --history was passed explicitly.
    hist_mode = args.history
    if "--history" not in sys.argv:
        saved = st.value("cfg/history")
        if saved in ("today", "all", "none"):
            hist_mode = saved
    events = (load_history(hist_mode, sym) if args.live
              else [e for e in of_feed.replay_feed() if _event_symbol(e) == sym])
    win = MainWindow(events, args.bars, args.size, sym, debug=args.debug)
    win.show()
    QtCore.QTimer.singleShot(0, win._ensure_on_screen)   # re-clamp after the WM places it

    if args.debug:                                     # one-shot dump (inspect captured data now)
        print(f"[diag startup] {win._refresh_diag()}")

    if args.shot and not args.live:                    # replay snapshot
        for _ in range(4):
            app.processEvents()
        win.grab().save(args.shot)
        print(f"wrote {args.shot}")
        return

    if args.live:
        win.attach_live(sym, persist=not args.shot and not args.view_only)

    if args.shot:                                      # timed live snapshot
        def finish():
            win.grab().save(args.shot)
            print(f"wrote {args.shot} (live {args.secs}s)")
            win.close()   # triggers closeEvent -> stops the feed thread cleanly
            app.quit()
        QtCore.QTimer.singleShot(args.secs * 1000, finish)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
