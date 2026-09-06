"""
settings.py — every tunable value, and the dialog that edits them.

SETTINGS_SPEC is the single source of truth: each row is
(key, label, kind, spec) with an optional 5th help string, and the dialog is
generated from it. Add a row here and the setting appears, is searchable, and is
persisted -- there is no second place to register it.

tests/suites/settings_complete.py enforces that: nothing in DEFAULTS that the
dialog cannot reach, nothing in the dialog missing a default, and no setting that
no code actually reads.
"""

from PySide6 import QtCore, QtGui, QtWidgets

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
        ("fp_style", "Chart style", "choice", ["clusters", "candles"],
         "clusters shows bid|ask volume per price; candles collapses each bar to OHLC."),
        ("imb_ratio", "Imbalance ratio", "double", (1.0, 20.0, 0.5),
         "A cell is imbalanced when it beats the diagonally opposite cell by this multiple."),
        ("imb_min_lots", "Imbalance min (lots)", "int", (0, 100000),
         "Ignore imbalances on cells smaller than this, so thin prints stop lighting up."),
        ("va_coverage", "Value-area %", "int", (10, 95),
         "Share of session volume the value area spans, grown outward from the POC."),
        ("show_poc", "Highlight POC cell (gold)", "bool", None),
        ("show_candle", "Show mid candle (OHLC)", "bool", None),
        ("cell_gap", "Bid|ask gap (% of bar)", "int", (0, 30),
         "Space between the bid and ask halves of a cell, as a share of the bar width."),
        ("show_imbalance", "Show imbalance markers", "bool", None),
        ("show_absorption", "Show absorption (support/resist)", "bool", None),
        ("absorption_min_lots", "Absorption min (lots)", "int", (0, 100000),
         "Floor for absorption, so a quiet bar's extreme does not qualify."),
        ("absorption_ratio", "Absorption ratio", "double", (1.0, 20.0, 0.5),
         "How much heavier than the bar's median a cell must be to count as absorption."),
        ("show_headers", "Show V / D / R headers", "bool", None),
        ("header_units", "Volume units (headers+cells)", "choice", ["lots", "shares"],
         "Units for the per-bar V/D/R headers and the cluster numbers."),
        ("show_va_lines", "Show value-area lines", "bool", None),
        ("show_vwap", "Show VWAP line", "bool", None),
        ("show_regime", "Show regime chip in toolbar", "bool", None),
        ("regime_window", "Regime window (bars)", "int", (5, 200),
         "Bars of lookback for the efficiency ratio and realised volatility."),
        ("regime_warmup_min", "Regime warm-up (min)", "int", (0, 120),
         "Minutes of session required before a regime is shown at all. A ratio computed on half a window is noise."),
        ("er_trend", "Trend threshold (ER)", "double", (0.1, 1.0, 0.05),
         "Efficiency ratio at or above this reads as TREND."),
        ("er_chop", "Chop threshold (ER)", "double", (0.0, 0.9, 0.05),
         "Efficiency ratio at or below this reads as CHOP. Do not loosen these to make the label fire more often; on a small sample that is fitting noise."),
    ]),
    ("Heatmap", [
        ("colormap", "Colormap", "choice", ["bookmap", "inferno", "viridis", "turbo", "magma"],
         "bookmap is the hand-tuned palette; the rest come from matplotlib."),
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
        ("show_price_line", "Show price line", "bool", None,
         "Overlay the traded price on the heatmap."),
        ("show_walls", "Track biggest walls (lines)", "bool", None,
         "Dashed lines tracking the largest resting bid and ask over time."),
        ("show_hm_candles", "Overlay OHLC candles", "bool", None,
         "Overlay OHLC candles on the heatmap columns."),
        ("show_bubbles", "Show trade bubbles", "bool", None,
         "Mark individual trades on the heatmap, sized by volume."),
        ("bubble_ref_pct", "Bubble size percentile", "int", (50, 100),
         "Percentile of trade size treated as a full-size bubble."),
        ("bubble_min_frac", "Bubble min size", "double", (0.0, 1.0, 0.05),
         "Floor on bubble size so small prints stay visible."),
        ("bubble_opacity", "Bubble opacity", "int", (20, 255),
         "Bubble alpha over the heatmap."),
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
        ("dom_resizable", "Resizable columns (drag headers)", "bool", None,
         "Drag column edges to resize. Widths are remembered once you do."),
        ("dom_depth", "DOM depth (rows)", "int", (3, 60),
         "Price levels shown either side of the touch."),
        ("wall_mult", "Wall × median", "double", (1.0, 20.0, 0.5),
         "A level counts as a wall at this multiple of the median resting size."),
        ("show_depth_bars", "Show depth bars", "bool", None,
         "Shade each ladder row in proportion to its resting size."),
        ("tape_rows", "Tape rows", "int", (10, 2000),
         "How many recent prints the tape keeps on screen."),
        ("time_ms", "Tape time decimals", "int", (0, 9),
         "Fractional-second digits in the tape's timestamps."),
    ]),
    ("Layout", [
        # panel visibility lives in the Panels menu now: dock state is the single
        # source of truth and persists in the QMainWindow saveState() blob
        ("vap_scale", "Vol@price width scale", "choice", ["sqrt", "linear"],
         "sqrt keeps a block print from flattening every other row; linear is raw volume."),
        ("vap_mode", "Vol@price range (new panels)", "choice", ["session", "visible"],
         "session profiles the whole day; visible follows the bars in view on the linked footprint. Existing panels keep their own setting."),
        ("depth_levels", "Depth curve levels/side", "int", (5, 200),
         "Levels included in the cumulative depth curve."),
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
