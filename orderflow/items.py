"""
items.py — appearance, tunable settings, and the pyqtgraph drawing primitives.

Split out of app.py so panels.py and app.py can both use them without a cycle.
Nothing here knows about MainWindow or the model registry: the graphics items
take a model duck-typed at draw time, exactly as before.
"""

from collections import Counter

from PySide6 import QtCore, QtGui, QtWidgets
import pyqtgraph as pg

pg.setConfigOption("background", "#0b0d0e")
pg.setConfigOption("foreground", "#9aa0a6")
pg.setConfigOption("imageAxisOrder", "row-major")
pg.setConfigOptions(antialias=True)

# The plots are painted dark by pyqtgraph, but Qt widgets (the DOM ladder and the
# trade tape are QTableWidgets) follow the platform palette and came out white
# against them. One stylesheet on the main window covers every child.
DARK_QSS = """
QMainWindow, QDockWidget, QWidget { background: #0b0d0e; color: #d8dde2; }
QTableWidget, QTableView {
    background: #0b0d0e; alternate-background-color: #101416;
    color: #d8dde2; gridline-color: #1c2226;
    selection-background-color: #1d2b3a; selection-color: #ffffff;
    border: none;
}
QHeaderView::section {
    background: #12171a; color: #8b939b; border: 0;
    border-bottom: 1px solid #23282d; padding: 2px 4px;
}
QTableCornerButton::section { background: #12171a; border: 0; }
QToolBar { background: #0e1114; border: 0; spacing: 2px; }
QToolButton { color: #d8dde2; }
QToolButton:hover { background: #1b2126; border-radius: 3px; }
/* QAbstractSpinBox, not QSpinBox: the latter does not match QDoubleSpinBox, so
   half the numeric fields in the settings dialog were left unstyled. */
QComboBox, QAbstractSpinBox, QLineEdit {
    background: #12171a; color: #d8dde2;
    border: 1px solid #23282d; border-radius: 3px; padding: 1px 4px;
}
QPushButton {
    background: #1b2229; color: #d8dde2;
    border: 1px solid #2c343c; border-radius: 3px; padding: 4px 14px;
}
QPushButton:hover { background: #232c35; border-color: #3a444e; }
QPushButton:pressed { background: #161c22; }
QPushButton:default { border-color: #3f6ea8; }
QPushButton:disabled { color: #5f6b76; border-color: #23282d; }
QComboBox QAbstractItemView {
    background: #12171a; color: #d8dde2; selection-background-color: #1d2b3a;
}
QCheckBox, QLabel, QRadioButton { color: #d8dde2; }
QMenu { background: #12171a; color: #d8dde2; border: 1px solid #23282d; }
QMenu::item:selected { background: #1d2b3a; }
QScrollBar:vertical, QScrollBar:horizontal { background: #0b0d0e; border: 0; }
QScrollBar::handle { background: #262d33; border-radius: 4px; min-height: 18px; }
QScrollBar::handle:hover { background: #333c44; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QToolTip { background: #12171a; color: #d8dde2; border: 1px solid #2c343a; }
"""

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


def side_colors(cfg):
    """Buy/sell palette for the footprint and the volume profile.

    Only the two base colours are configurable; the imbalance fill and edge
    shades are DERIVED from them. Hand-tuned constants would stay green while a
    user set the base to blue, which is exactly the incoherence this avoids.
    The ratios are chosen to land near the original palette at the defaults."""
    buy = QtGui.QColor(cfg.get("buy_color") or "#3fe26a")
    sell = QtGui.QColor(cfg.get("sell_color") or "#ff5454")
    if not buy.isValid():
        buy = QtGui.QColor(BULL)
    if not sell.isValid():
        sell = QtGui.QColor(BEAR)
    return {
        "buy": buy,
        "sell": sell,
        "buy_fill": buy.darker(155),      # imbalance cell body
        "sell_fill": sell.darker(175),
        "buy_edge": buy.lighter(145),     # imbalance marker / number
        "sell_edge": sell.lighter(115),
    }
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
    "vap_scale": "sqrt", "vap_mode": "session", "live_hz": 7,
    "buy_color": "#3fe26a", "sell_color": "#ff5454",
    "show_avg_size": True, "avg_size_mult": 3.0, "reload_min_frac": 0.5,
    "reload_freq_tol": 3, "reload_min_count": 3,
    "depth_levels": 30, "signal_rows": 300,
    "reconnect": True, "backoff_max": 30,
    "history": "today",
}

# (tab, [(key, label, kind, spec)]) — kind: bool | int | double | choice
SETTINGS_SPEC = [
    ("Footprint", [
        ("buy_color", "Buy side colour", "color", None,
         "Buyer-initiated volume, in the footprint clusters and the volume profile. "
         "The imbalance shades are derived from it."),
        ("sell_color", "Sell side colour", "color", None,
         "Seller-initiated volume. The DOM, tape and delta footer keep their own "
         "fixed green/red."),
        ("fp_style", "Chart style", "choice", ["clusters", "candles"]),
        ("imb_ratio", "Imbalance ratio", "double", (1.0, 20.0, 0.5),
         "A cell is imbalanced when it beats the diagonally opposite cell by this multiple."),
        ("imb_min_lots", "Imbalance min (lots)", "int", (0, 100000),
         "Ignore imbalances on cells smaller than this, so thin prints stop lighting up."),
        ("va_coverage", "Value-area %", "int", (10, 95)),
        ("show_poc", "Highlight POC cell (gold)", "bool", None),
        ("show_candle", "Show mid candle (OHLC)", "bool", None),
        ("cell_gap", "Bid|ask gap (% of bar)", "int", (0, 30)),
        ("show_imbalance", "Show imbalance markers", "bool", None),
        ("show_absorption", "Show absorption (support/resist)", "bool", None),
        ("absorption_min_lots", "Absorption min (lots)", "int", (0, 100000),
         "Floor for absorption, so a quiet bar's extreme does not qualify."),
        ("absorption_ratio", "Absorption ratio", "double", (1.0, 20.0, 0.5),
         "How much heavier than the bar's median a cell must be to count as absorption."),
        ("show_headers", "Show V / D / R headers", "bool", None),
        ("header_units", "Volume units (headers+cells)", "choice", ["lots", "shares"]),
        ("show_va_lines", "Show value-area lines", "bool", None),
        ("show_vwap", "Show VWAP line", "bool", None),
        ("show_regime", "Show regime chip in toolbar", "bool", None),
        ("regime_window", "Regime window (bars)", "int", (5, 200)),
        ("regime_warmup_min", "Regime warm-up (min)", "int", (0, 120)),
        ("er_trend", "Trend threshold (ER)", "double", (0.1, 1.0, 0.05)),
        ("er_chop", "Chop threshold (ER)", "double", (0.0, 0.9, 0.05)),
    ]),
    ("Heatmap", [
        ("colormap", "Colormap", "choice", ["bookmap", "inferno", "viridis", "turbo", "magma"]),
        ("hm_window", "Window (columns)", "int", (60, 10000),
         "How many columns to keep. Older ones are dropped so a long session stays bounded."),
        ("hm_throttle", "Throttle (s / column)", "double", (0.1, 10.0, 0.5),
         "Seconds between heatmap columns. Rebuilds the model, so it is not instant."),
        ("hm_scale", "Contrast scale", "choice", ["equalize", "sqrt", "linear", "log"],
         "equalize ranks sizes so real walls stand out; the others map size directly."),
        ("hm_gamma", "Equalize darkness (γ)", "double", (0.5, 6.0, 0.25),
         "Only used by equalize. Higher pushes more of the range into the dark end."),
        ("hm_pctile", "Clip percentile (non-equalize)", "int", (50, 100),
         "Only used by the non-equalize scales: where to clip the brightest sizes."),
        ("show_price_line", "Show price line", "bool", None),
        ("show_walls", "Track biggest walls (lines)", "bool", None),
        ("show_hm_candles", "Overlay OHLC candles", "bool", None),
        ("show_bubbles", "Show trade bubbles", "bool", None),
        ("bubble_ref_pct", "Bubble size percentile", "int", (50, 100)),
        ("bubble_min_frac", "Bubble min size", "double", (0.0, 1.0, 0.05)),
        ("bubble_opacity", "Bubble opacity", "int", (20, 255)),
    ]),
    ("DOM & Tape", [
        ("show_avg_size", "Show average order size", "bool", None,
         "value / order-count per level: one large order versus a crowd of small "
         "ones at the same price. Depth alone cannot tell them apart."),
        ("avg_size_mult", "Big-order highlight (x median)", "double", (1.0, 20.0, 0.5),
         "Emphasise a level whose average order is this many times the ladder median."),
        ("reload_min_frac", "Reload min share of consumed", "double", (0.1, 2.0, 0.1),
         "A level counts as replenished when it is topped back up by at least this "
         "share of what trades just took."),
        ("reload_freq_tol", "Reload order-count tolerance", "int", (0, 50),
         "How much the order count may rise and still look like ONE order reloading "
         "rather than new participants arriving."),
        ("reload_min_count", "Reload min occurrences", "int", (1, 100),
         "How many times a level must replenish before it is marked."),
        ("dom_pro", "Pro mode (centered ladder)", "bool", None,
         "Centred ladder with Chg and per-level volume, versus the classic side-by-side book."),
        ("dom_resizable", "Resizable columns (drag headers)", "bool", None),
        ("dom_depth", "DOM depth (rows)", "int", (3, 60)),
        ("wall_mult", "Wall × median", "double", (1.0, 20.0, 0.5),
         "A level counts as a wall at this multiple of the median resting size."),
        ("show_depth_bars", "Show depth bars", "bool", None),
        ("tape_rows", "Tape rows", "int", (10, 2000)),
        ("time_ms", "Tape time decimals", "int", (0, 9),
         "Fractional-second digits in the tape's timestamps."),
    ]),
    ("Layout", [
        # panel visibility lives in the Panels menu now: dock state is the single
        # source of truth and persists in the QMainWindow saveState() blob
        ("vap_scale", "Vol@price width scale", "choice", ["sqrt", "linear"],
         "sqrt keeps a block print from flattening every other row; linear is raw volume."),
        ("vap_mode", "Vol@price range (new panels)", "choice", ["session", "visible"]),
        ("depth_levels", "Depth curve levels/side", "int", (5, 200)),
        ("signal_rows", "Signal log rows", "int", (20, 2000),
         "Rows kept in the signal log and the capture-integrity panel."),
        ("reconnect", "Reconnect when the feed drops", "bool", None,
         "Retry automatically when the feed drops. Missed tape cannot be recovered."),
        ("backoff_max", "Max reconnect wait (s)", "int", (5, 300),
         "Longest wait between reconnect attempts. Retries back off up to this."),
        ("live_hz", "Live redraw (Hz)", "int", (1, 30),
         "Redraw rate while live. Lower it if the window feels heavy on a long session."),
    ]),
    ("General", [
        ("history", "Live history preload (restart)", "choice", ["today", "all", "none"],
         "How much of the archive to preload. Takes effect on the next launch."),
    ]),
]

# key -> (kind, spec) for range-clamp / choice-validation on restore
# rows are (key, label, kind, spec) with an OPTIONAL 5th help string, so unpack
# by index rather than by shape -- a fixed 4-tuple unpack breaks every setting at
# once the moment one row gains help text
SPEC_BY_KEY = {row[0]: (row[2], row[3])
               for _tab, items in SETTINGS_SPEC for row in items}
HELP_BY_KEY = {row[0]: (row[4] if len(row) > 4 else "")
               for _tab, items in SETTINGS_SPEC for row in items}
MODEL_CFG_KEYS = {"hm_throttle", "hm_window"}   # only these need a model rebuild to apply


class ColorButton(QtWidgets.QPushButton):
    """A swatch that opens a colour picker and shows what it currently holds."""

    def __init__(self, value, parent=None):
        super().__init__(parent)
        self.setFixedSize(58, 22)
        self.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
        self._color = QtGui.QColor(value)
        if not self._color.isValid():
            self._color = QtGui.QColor("#3fe26a")
        self._paint()
        self.clicked.connect(self._pick)

    def _paint(self):
        c = self._color
        # a light border on dark colours and vice versa, so the swatch never
        # disappears into the dialog background
        edge = "#0d1117" if c.lightness() > 128 else "#5f6b76"
        self.setStyleSheet("background:%s; border:1px solid %s; border-radius:3px;"
                           % (c.name(), edge))
        self.setToolTip(c.name())

    def _pick(self):
        c = QtWidgets.QColorDialog.getColor(self._color, self, "Pick a colour")
        if c.isValid():
            self._color = c
            self._paint()

    def value(self):
        return self._color.name()


class SettingsDialog(QtWidgets.QDialog):
    """Sidebar + search over a cfg dict. Calls `on_apply(new_values)` on Apply/OK."""

    def __init__(self, cfg, on_apply, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(720, 560)
        self._on_apply = on_apply
        self.widgets = {}
        self._rows = []          # (page_index, key, label, row_widgets) for the filter

        self.nav = QtWidgets.QListWidget()
        self.nav.setFixedWidth(150)
        self.nav.setSpacing(1)
        self.stack = QtWidgets.QStackedWidget()

        for tabname, items in SETTINGS_SPEC:
            page = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(page)
            form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignRight)
            form.setVerticalSpacing(9)
            idx = self.stack.count()
            for row in items:
                key, label, kind, spec = row[0], row[1], row[2], row[3]
                help_text = row[4] if len(row) > 4 else ""
                w = self._make(kind, spec, cfg.get(key, DEFAULTS[key]))
                self.widgets[key] = (w, kind)
                lab = QtWidgets.QLabel(label)
                if help_text:
                    holder = QtWidgets.QWidget()
                    v = QtWidgets.QVBoxLayout(holder)
                    v.setContentsMargins(0, 0, 0, 0)
                    v.setSpacing(1)
                    v.addWidget(w)
                    hint = QtWidgets.QLabel(help_text)
                    hint.setWordWrap(True)
                    hint.setStyleSheet("color:#7f8792; font-size:11px;")
                    v.addWidget(hint)
                    field = holder
                else:
                    field = w
                form.addRow(lab, field)
                self._rows.append((idx, key, label.lower(), lab, field))
            page_scroll = QtWidgets.QScrollArea()
            page_scroll.setWidgetResizable(True)
            page_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            page_scroll.setWidget(page)
            self.stack.addWidget(page_scroll)
            self.nav.addItem(tabname)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)

        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Search settings...")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)

        B = QtWidgets.QDialogButtonBox
        btns = B(B.StandardButton.Ok | B.StandardButton.Apply | B.StandardButton.Cancel)
        reset = btns.addButton("Reset page", B.ButtonRole.ResetRole)
        reset.setToolTip("Restore every setting on this page to its default")
        reset.clicked.connect(self._reset_page)
        btns.accepted.connect(self._ok)        # OK = apply once, then close
        btns.rejected.connect(self.reject)     # Cancel discards (values read on _apply)
        btns.button(B.StandardButton.Apply).clicked.connect(self._apply)

        grid = QtWidgets.QGridLayout(self)
        grid.addWidget(self.search, 0, 0, 1, 2)
        grid.addWidget(self.nav, 1, 0)
        grid.addWidget(self.stack, 1, 1)
        grid.addWidget(btns, 2, 0, 1, 2)
        grid.setColumnStretch(1, 1)

    # ---- filtering ----
    def _filter(self, text):
        q = (text or "").strip().lower()
        hits = {}
        for idx, key, label, lab, field in self._rows:
            show = (not q) or q in label or q in key.replace("_", " ")
            lab.setVisible(show)
            field.setVisible(show)
            hits[idx] = hits.get(idx, 0) + (1 if show else 0)
        for i in range(self.nav.count()):
            it = self.nav.item(i)
            empty = q and not hits.get(i)
            it.setForeground(QtGui.QColor("#4c545c") if empty else QtGui.QColor("#d8dee4"))
        if q:                                  # jump to the first page with a match
            for i in range(self.nav.count()):
                if hits.get(i):
                    self.nav.setCurrentRow(i)
                    break

    # ---- widget factory ----
    @staticmethod
    def _make(kind, spec, val):
        if kind == "bool":
            w = QtWidgets.QCheckBox(); w.setChecked(bool(val))
        elif kind == "int":
            w = QtWidgets.QSpinBox(); w.setRange(spec[0], spec[1]); w.setValue(int(val))
        elif kind == "double":
            w = QtWidgets.QDoubleSpinBox()
            w.setRange(spec[0], spec[1]); w.setSingleStep(spec[2]); w.setValue(float(val))
        elif kind == "color":
            w = ColorButton(val)
        else:
            w = QtWidgets.QComboBox(); w.addItems(spec); w.setCurrentText(str(val))
        return w

    @staticmethod
    def _set(w, kind, val):
        if kind == "bool":
            w.setChecked(bool(val))
        elif kind in ("int", "double"):
            w.setValue(type(w.value())(val))
        elif kind == "color":
            w._color = QtGui.QColor(val); w._paint()
        else:
            w.setCurrentText(str(val))

    def values(self):
        out = {}
        for key, (w, kind) in self.widgets.items():
            if kind == "bool":
                out[key] = w.isChecked()
            elif kind in ("int", "double"):
                out[key] = w.value()
            elif kind == "color":
                out[key] = w.value()
            else:
                out[key] = w.currentText()
        return out

    def _reset_page(self):
        page = self.nav.currentRow()
        for _tab, items in [SETTINGS_SPEC[page]]:
            for row in items:
                key, kind = row[0], row[2]
                w, _k = self.widgets[key]
                self._set(w, kind, DEFAULTS[key])

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
        sc = side_colors(cfg)               # configurable buy/sell + derived shades
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
                    col = QtGui.QColor(sc["buy"] if delta >= 0 else sc["sell"])
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
                            lcol = QtGui.QColor(sc["sell_fill"])
                            lcol.setAlpha(110 + int(90 * min(total / ref, 1.0)))
                        if buy_imb:
                            rcol = QtGui.QColor(sc["buy_fill"])
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
                    col = QtGui.QColor(sc["buy"] if cl >= o else sc["sell"])
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
                                else sc["sell_edge"] if sell_imb else CELL_NUM))
            p.drawText(QtCore.QRectF(x0 + 2, ytop, xg0 - x0 - 4, hpx), A_R,
                       f"{sell / unit:,.0f}")
            p.setFont(boldf if buy_imb else font)
            p.setPen(QtGui.QPen(poc_num if is_poc
                                else sc["buy_edge"] if buy_imb else CELL_NUM))
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
                p.setPen(QtGui.QPen(sc["buy"] if D >= 0 else sc["sell"]))
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
