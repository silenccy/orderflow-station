"""
app.py — IDX orderflow workstation (PySide6 + pyqtgraph).

Every widget is a dockable Panel (see panels.py), so the layout snaps, tabs,
floats and persists. Panels resolve their data through a LINK GROUP; the window
owns a registry of models keyed by (symbol, bar_kind, bar_size) and one live feed
thread per symbol actually on screen.

    python -m orderflow.app --replay --symbol ASII
    python -m orderflow.app --live --symbol ASII BBCA --view-only
    python -m orderflow.app --shot out.png            # headless render, then exit
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

from . import __version__
from . import diagnostics
from . import feed as of_feed
from . import model as of_model
from .paths import REPO_ROOT

# Before ANY Qt import: under pythonw.exe stderr is None, so without this a
# failure — including a failure in the PySide6 import itself — would kill the
# process with no window and no message. See diagnostics.py.
diagnostics.install()

# Qt platform must be chosen before QApplication is constructed — and before
# anything that imports PySide6, which includes items/panels below.
if "--shot" in sys.argv and "QT_QPA_PLATFORM" not in os.environ:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    # The offscreen plugin ships no font backend on Windows: without a font dir
    # it loads ZERO families and every label renders as a tofu box, which is why
    # headless screenshots looked broken. Point it at the system fonts.
    if "QT_QPA_FONTDIR" not in os.environ:
        _fonts = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Fonts")
        if os.path.isdir(_fonts):
            os.environ["QT_QPA_FONTDIR"] = _fonts

from PySide6 import QtCore, QtGui, QtWidgets  # noqa: E402

from . import capture as of_capture  # noqa: E402
from . import panels as of_panels  # noqa: E402
from . import startup as of_startup  # noqa: E402
from .items import (BEAR, BULL, DARK_QSS, DEFAULTS,  # noqa: E402
                    MODEL_CFG_KEYS, SPEC_BY_KEY, SettingsDialog)

def _daemon_exe():
    """pythonw.exe when we can find it, so the detached recorder never flashes
    a console window. Falls back to whatever is running us."""
    from pathlib import Path
    exe = Path(sys.executable)
    if sys.platform == "win32":
        cand = exe.with_name("pythonw.exe")
        if cand.exists():
            return str(cand)
    return str(exe)


# panel kind -> menu label, in the order the Add-panel menu lists them
PANEL_MENU = [("footprint", "Footprint"), ("delta", "Delta footer"),
              ("vap", "Volume @ Price"), ("heatmap", "Liquidity heatmap"),
              ("cvd", "CVD"), ("regime", "Regime"), ("dom", "Order book (DOM)"),
              ("tape", "Trade tape"), ("depth", "Depth curve"),
              ("watchlist", "Watchlist"), ("signals", "Signal log"),
              ("integrity", "Capture integrity")]


# ============================================================
#  Live feed thread (asyncio -> Qt signals), one per symbol
# ============================================================
class FeedThread(QtCore.QThread):
    batch = QtCore.Signal(str, list)
    status = QtCore.Signal(str, str)

    def __init__(self, symbol, sink=None, reconnect=True, backoff_max=30.0):
        super().__init__()
        self.symbol = symbol
        self.sink = sink            # shared, mutex-guarded; the window owns it
        self.reconnect = reconnect
        self.backoff_max = float(backoff_max)
        self._loop = None
        self._task = None

    async def _pump(self):
        import asyncio
        buf, last = [], 0.0
        loop = asyncio.get_event_loop()
        async for ev in of_feed.live_feed(self.symbol, persist=self.sink is not None,
                                          sink=self.sink, reconnect=self.reconnect,
                                          backoff_max=self.backoff_max):
            if ev[0] == "status":
                self.status.emit(self.symbol, ev[1])
                continue
            buf.append(ev)
            now = loop.time()
            if now - last > 0.1:
                self.batch.emit(self.symbol, buf)
                buf = []
                last = now
        if buf:
            self.batch.emit(self.symbol, buf)

    def _explain(self, e):
        """Turn a feed exception into something worth reading in the status bar."""
        if isinstance(e, FileNotFoundError):
            return "no session token — click Token to grab one"
        return "%s: %s" % (type(e).__name__, e)

    def run(self):
        import asyncio
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._task = self._loop.create_task(self._pump())
        try:
            self._loop.run_until_complete(self._task)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            # Anything escaping here used to be printed to a None stderr under
            # pythonw and lost — a dead feed with no explanation anywhere.
            diagnostics.record(type(e), e, e.__traceback__,
                               where="feed thread %s" % self.symbol)
            self.status.emit(self.symbol, self._explain(e))
        finally:
            self._loop.close()

    def stop(self):
        if self._loop and self._task and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._task.cancel)


# ============================================================
#  Event helpers
# ============================================================
def _event_date(ev):
    if ev[0] == "book":
        return (ev[4] or "")[:10]
    if ev[0] in ("trade", "summary"):
        d = ev[1]
        return (d.get("recv_iso") or d.get("trade_time") or "")[:10]
    if ev[0] == "gap":                 # or --history today drops them silently
        return (ev[1].get("started") or "")[:10]
    return ""


def _event_symbol(ev):
    if ev[0] == "book":
        return ev[1]
    return ev[1].get("symbol") if isinstance(ev[1], dict) else None


def load_history(mode):
    """Preload captured CSV history, bucketed by symbol. 'today' keeps only
    today's events (so old days don't pollute the chart); 'all' keeps
    everything; 'none' starts blank."""
    if mode == "none":
        return {}
    hist = list(of_feed.replay_feed())
    if mode == "today":
        today = datetime.now().strftime("%Y-%m-%d")
        hist = [e for e in hist if _event_date(e) == today]
    out = {}
    for ev in hist:
        sym = _event_symbol(ev)
        if sym:
            out.setdefault(sym, []).append(ev)
    return out


# ============================================================
#  Main window
# ============================================================
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, events, bar_kind="time", bar_size=60, symbols=("ASII",),
                 debug=False, live=False, persist=False, settings=None):
        super().__init__()
        self.setWindowTitle("Orderflow Station")
        self.setStyleSheet(DARK_QSS)   # Qt widgets default to the light platform
        self.resize(1700, 1000)      # clamped to the real screen by _ensure_on_screen
        self.events = dict(events)   # symbol -> [event]
        self.models = {}             # (symbol, bar_kind, bar_size) -> OrderflowModel
        self.panels = []
        self.feeds = {}              # symbol -> FeedThread
        self.cfg = dict(DEFAULTS)
        self.debug = debug
        self.live = live
        self._persist = persist
        self._sink = None
        self._holds_lock = False     # did THIS window take the writer lock?
        self._rec_asked_t = None      # when Record was pressed (to catch instant death)
        self._next_uid = 1
        self._dirty = False
        self._last_batch_t = None    # wall-clock of the last live batch (stall detector)
        self._last_diag_t = 0.0
        self._derived = {}           # symbol -> running last/trades/vol
        self._summaries = {}         # symbol -> latest summary dict
        self._measure = False

        syms = list(symbols) or ["ASII"]
        self.groups = {g: {"symbol": syms[min(i, len(syms) - 1)],
                           "bar_kind": bar_kind, "bar_size": bar_size}
                       for i, g in enumerate(of_panels.GROUPS)}
        for sym, evs in self.events.items():
            self._tally(sym, evs)

        # injectable so tests get their own scope instead of the real config
        self.settings = settings or QtCore.QSettings("orderflow", "of_app")
        self.setDockNestingEnabled(True)
        O = QtWidgets.QMainWindow.DockOption
        opts = O.AnimatedDocks | O.AllowNestedDocks | O.AllowTabbedDocks
        if hasattr(O, "GroupedDragging"):        # drag a whole tab group as a unit
            opts |= O.GroupedDragging
        self.setDockOptions(opts)

        self._build_toolbar()
        self._build_statusbar()
        # No central widget: docks then own the entire window, so nothing is pinned.
        holder = QtWidgets.QWidget()
        self.setCentralWidget(holder)
        self.takeCentralWidget()

        self._restore_cfg()
        self._restore_panels()
        self._rebuild_panels_menu()
        self.refresh()

    # ------------------------------------------------------------------
    #  Toolbar / status bar
    # ------------------------------------------------------------------
    def _build_toolbar(self):
        tb = self.addToolBar("controls")
        tb.setObjectName("controls")
        self.toolbar = tb
        # keep "controls" out of the right-click toolbar/dock menu — unchecking it
        # there hides the toolbar and saveState() would persist the hidden state
        tb.toggleViewAction().setVisible(False)

        tb.addWidget(QtWidgets.QLabel("  Group: "))
        self.group_combo = QtWidgets.QComboBox()
        self.group_combo.addItems(of_panels.GROUPS)
        self.group_combo.setToolTip("Which link group the controls below drive")
        self.group_combo.currentTextChanged.connect(self._sync_group_controls)
        tb.addWidget(self.group_combo)

        self.sym_combo = QtWidgets.QComboBox()
        self.sym_combo.setEditable(True)
        self.sym_combo.setFixedWidth(90)
        self.sym_combo.setToolTip("Symbol for the active group (4-letter IDX ticker)")
        self.sym_combo.currentTextChanged.connect(self._on_group_source_changed)
        tb.addWidget(self.sym_combo)

        tb.addWidget(QtWidgets.QLabel("  Bars: "))
        self.bar_combo = QtWidgets.QComboBox()
        self.bar_combo.addItems(["time", "tick", "volume"])
        self.bar_combo.currentTextChanged.connect(self._on_group_source_changed)
        tb.addWidget(self.bar_combo)
        tb.addWidget(QtWidgets.QLabel("  Size: "))
        self.size_spin = QtWidgets.QSpinBox()
        self.size_spin.setRange(1, 100000)
        self.size_spin.editingFinished.connect(self._on_group_source_changed)
        tb.addWidget(self.size_spin)

        self.delta_cells = QtWidgets.QCheckBox("Δ cells")
        self.delta_cells.toggled.connect(lambda *_: self.refresh())
        tb.addWidget(self.delta_cells)
        tb.addWidget(QtWidgets.QLabel("  Big≥(lots): "))
        self.big_spin = QtWidgets.QSpinBox()
        self.big_spin.setRange(1, 1000000)
        self.big_spin.setValue(50)
        self.big_spin.editingFinished.connect(self.refresh)
        tb.addWidget(self.big_spin)

        center = QtWidgets.QToolButton()
        center.setText(" ⌖ Center ")
        center.setToolTip("Jump to the latest bars, keeping your zoom (Home)")
        center.setAutoRaise(True)
        center.clicked.connect(lambda: self._center_latest())
        tb.addWidget(center)
        self.follow_chk = QtWidgets.QCheckBox("Follow")
        self.follow_chk.setToolTip("Auto-scroll to new bars; panning back turns it off")
        tb.addWidget(self.follow_chk)
        QtGui.QShortcut(QtGui.QKeySequence("Home"), self).activated.connect(self._center_latest)

        self.measure_btn = QtWidgets.QToolButton()
        self.measure_btn.setText(" ↔ Measure ")
        self.measure_btn.setCheckable(True)
        self.measure_btn.setAutoRaise(True)
        self.measure_btn.setToolTip("Click two points on a footprint to measure\n"
                                    "ticks, %, elapsed time and volume between them")
        self.measure_btn.toggled.connect(self._toggle_measure)
        tb.addWidget(self.measure_btn)

        self.panels_btn = QtWidgets.QToolButton()
        self.panels_btn.setText(" ▦ Panels ")
        self.panels_btn.setAutoRaise(True)
        self.panels_btn.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.panels_menu = QtWidgets.QMenu(self)
        self.panels_btn.setMenu(self.panels_menu)
        # Submenus are created ONCE and parented to the window: QMenu.clear()
        # deletes actions it owns, which would take a submenu built inside the
        # rebuild down with it (shiboken then raises "C++ object already deleted").
        self._add_menu = QtWidgets.QMenu("Add panel", self)
        for kind, label in PANEL_MENU:
            self._add_menu.addAction(label, lambda k=kind: self._add_from_menu(k))
        self._rem_menu = QtWidgets.QMenu("Remove panel", self)
        tb.addWidget(self.panels_btn)

        gear = QtWidgets.QToolButton()
        gear.setText("  ⚙  ")
        gear.setToolTip("Settings")
        gear.setAutoRaise(True)
        gear.clicked.connect(self._open_settings)
        tb.addWidget(gear)

        self.rec_btn = QtWidgets.QToolButton()
        self.rec_btn.setText(" ● Record ")
        self.rec_btn.setAutoRaise(True)
        self.rec_btn.setToolTip("Start or stop recording the archive to disk")
        self.rec_btn.clicked.connect(self._toggle_recording)
        tb.addWidget(self.rec_btn)

        tok_btn = QtWidgets.QToolButton()
        tok_btn.setText(" Token ")
        tok_btn.setAutoRaise(True)
        tok_btn.setToolTip("Grab a fresh session token from your browser")
        tok_btn.clicked.connect(self._get_token)
        tb.addWidget(tok_btn)

        help_btn = QtWidgets.QToolButton()
        help_btn.setText("  ?  ")
        help_btn.setAutoRaise(True)
        help_btn.setToolTip("Command reference")
        help_btn.clicked.connect(lambda: of_startup.HelpDialog(self).exec())
        tb.addWidget(help_btn)

        self.regime_lbl = QtWidgets.QLabel("")   # intraday regime chip
        tb.addWidget(self.regime_lbl)
        self.status_lbl = QtWidgets.QLabel("   ")
        tb.addWidget(self.status_lbl)

        # second row: session summary, always visible (there is no central widget)
        self.addToolBarBreak()
        tb2 = self.addToolBar("summary")
        tb2.setObjectName("summary")
        tb2.toggleViewAction().setVisible(False)
        tb2.setMovable(False)
        self.summary_lbl = QtWidgets.QLabel("")
        self.summary_lbl.setStyleSheet("font-family:Consolas; font-size:12px; color:#c8ccd0")
        tb2.addWidget(self.summary_lbl)
        spacer = QtWidgets.QWidget()
        spacer.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding,
                             QtWidgets.QSizePolicy.Policy.Preferred)
        tb2.addWidget(spacer)
        self.session_lbl = QtWidgets.QLabel("")   # token age + who is recording
        self.session_lbl.setTextFormat(QtCore.Qt.TextFormat.RichText)
        self.session_lbl.setStyleSheet("font-size:12px;")
        tb2.addWidget(self.session_lbl)
        # 1 Hz: reports the writer/token state AND heartbeats our own lock
        self.session_timer = QtCore.QTimer(self)
        self.session_timer.timeout.connect(self._refresh_session)
        self.session_timer.start(1000)
        self._refresh_session()

    def _build_statusbar(self):
        """--debug: diagnostics in a bottom status bar (grouped, health-coloured).
        Four equal-stretch labels (FLOW/BOOK/FEED/CHECK) fill the window width."""
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

    # ------------------------------------------------------------------
    #  Panel host interface (called by panels.py)
    # ------------------------------------------------------------------
    def group_spec(self, name):
        return self.groups[name]

    def set_group_spec(self, name, **upd):
        self.groups[name].update(upd)
        for p in self.panels:
            if p.group == name:
                p.titlebar.sync()
        self._sync_group_controls()
        self.on_panel_source_changed(None)

    def symbols(self):
        """Every symbol we could chart: whatever is in the archive, plus any
        symbol a group already points at."""
        out = set(self.events) | {g["symbol"] for g in self.groups.values() if g["symbol"]}
        return sorted(s for s in out if s)

    def symbol_stats(self):
        out = {}
        for sym in self.symbols():
            st = dict(self._derived.get(sym, {}))
            for k, v in (self._summaries.get(sym) or {}).items():
                if v is not None:
                    st[k] = v
            out[sym] = st
        return out

    def model_for(self, symbol, bar_kind, bar_size):
        key = (symbol, bar_kind, int(bar_size))
        m = self.models.get(key)
        if m is None:
            m = of_model.build_model(self.events.get(symbol, []), bar_kind, bar_size,
                                     heatmap_every_sec=self.cfg["hm_throttle"],
                                     heatmap_max=self.cfg["hm_window"])
            self.models[key] = m
        return m

    def _gc_models(self):
        """Drop models no visible panel is bound to — memory tracks the screen."""
        live = {p.model_key() for p in self.panels
                if p.wants_model and not p.isHidden() and p.spec()["symbol"]}
        for k in list(self.models):
            if k not in live:
                del self.models[k]

    def cell_mode(self):
        return "delta" if self.delta_cells.isChecked() else "bidask"

    def big_lots(self):
        return self.big_spin.value()

    def measure_active(self):
        return self._measure

    def request_refresh(self):
        self.refresh()

    def footprint_for(self, panel):
        """The footprint panel a dependent panel follows: same link group."""
        if not panel.group:
            return None
        for p in self.panels:
            if (isinstance(p, of_panels.FootprintPanel) and p.group == panel.group
                    and not p.isHidden()):
                return p
        return None

    def broadcast_cross(self, source, x, y):
        for p in self.panels:
            if not isinstance(p, of_panels.ChartPanel) or p.isHidden():
                continue
            if p is source or (source.group and p.group == source.group):
                p.set_cross(x, y)

    def center_on_bar(self, fp, index):
        (x0, x1), _y = fp.p.viewRange()
        span = max(x1 - x0, 4.0)
        fp.p.setXRange(index - span / 2, index + span / 2, padding=0)

    def on_panel_source_changed(self, _panel=None):
        self._gc_models()
        self._relink()
        self._sync_feeds()
        self._save_roster()
        self.refresh()

    def on_panel_closed(self, _panel):
        # a dock "close" hides it (Qt semantics); the menu action unchecks in step
        self._gc_models()
        self._sync_feeds()
        self._save_roster()

    def on_manual_range(self, panel):
        """Panning away from the live edge turns Follow off; zooming at the edge
        keeps following."""
        if not self.follow_chk.isChecked():
            return
        m = panel.model()
        if m and panel.p.viewRange()[0][1] < len(m.bar_ids()) - 0.5:
            self.follow_chk.setChecked(False)

    # ------------------------------------------------------------------
    #  Panels menu / roster
    # ------------------------------------------------------------------
    def add_panel(self, kind, group="A", spec=None, uid=None, area=None, show=True):
        cls = of_panels.PANEL_TYPES[kind]
        if uid is None:
            uid = self._next_uid
        self._next_uid = max(self._next_uid, uid + 1)
        p = cls(self, uid, group)
        if spec:
            p._spec.update(spec)
            p.titlebar.sync()
        self.panels.append(p)
        self.addDockWidget(area or QtCore.Qt.DockWidgetArea.LeftDockWidgetArea, p)
        p.setVisible(show)
        return p

    def remove_panel(self, panel):
        if panel in self.panels:
            self.panels.remove(panel)
        self.removeDockWidget(panel)
        panel.deleteLater()
        self._gc_models()
        self._sync_feeds()
        self._rebuild_panels_menu()
        self._save_roster()

    def _rebuild_panels_menu(self):
        mnu = self.panels_menu
        mnu.clear()
        for p in self.panels:
            act = p.toggleViewAction()
            # toggleViewAction is already bound to visibility in BOTH directions,
            # so closing a panel with its X unchecks this with no manual sync
            act.setText(p.titlebar.label.text() or p.title)
            mnu.addAction(act)
        mnu.addSeparator()
        mnu.addMenu(self._add_menu)
        self._rem_menu.clear()
        for p in self.panels:
            self._rem_menu.addAction(p.titlebar.label.text() or p.title,
                                     lambda pp=p: self.remove_panel(pp))
        mnu.addMenu(self._rem_menu)
        mnu.addSeparator()
        mnu.addAction("Reset to default layout", self._reset_layout)

    def _add_from_menu(self, kind):
        p = self.add_panel(kind, group=self.group_combo.currentText())
        p.setFloating(True)          # let the user drop it where they want
        self._relink()
        self._sync_feeds()
        self._rebuild_panels_menu()
        self._save_roster()
        self.refresh()

    def _default_panels(self):
        for p in list(self.panels):
            self.remove_panel(p)
        self.panels = []
        self._next_uid = 1
        g = self.group_combo.currentText() or "A"
        mk = lambda k: self.add_panel(k, group=g)
        self.fp = mk("footprint")
        self.dl = mk("delta")
        self.vp = mk("vap")
        self.hm = mk("heatmap")
        self.cv = mk("cvd")
        self.rg = mk("regime")
        self.dm = mk("dom")
        self.tp = mk("tape")
        self._default_layout()

    def _default_layout(self):
        """Reproduce the original 4x2 arrangement with real docks."""
        by = {p.kind: p for p in self.panels}
        fp, dl, vp = by.get("footprint"), by.get("delta"), by.get("vap")
        hm, cv, rg = by.get("heatmap"), by.get("cvd"), by.get("regime")
        dm, tp = by.get("dom"), by.get("tape")
        L = QtCore.Qt.DockWidgetArea.LeftDockWidgetArea
        R = QtCore.Qt.DockWidgetArea.RightDockWidgetArea
        H = QtCore.Qt.Orientation.Horizontal
        V = QtCore.Qt.Orientation.Vertical
        if not fp:
            return
        self.addDockWidget(L, fp)
        if vp:
            self.splitDockWidget(fp, vp, H)
        if dl:
            self.splitDockWidget(fp, dl, V)
        if hm:
            self.splitDockWidget(dl or fp, hm, V)
        if cv:
            self.splitDockWidget(hm or dl or fp, cv, V)
        if rg and vp:
            self.splitDockWidget(vp, rg, V)
        elif rg:
            self.addDockWidget(R, rg)
        if dm:
            self.addDockWidget(R, dm)
        if tp:
            if dm:
                self.splitDockWidget(dm, tp, V)
            else:
                self.addDockWidget(R, tp)
        for p in self.panels:
            p.setVisible(True)
            p.setFloating(False)
        col = [x for x in (fp, dl, hm, cv) if x]
        if len(col) > 1:                          # the original 6:1:3:2 row stretch
            self.resizeDocks(col, [6, 1, 3, 2][:len(col)], V)
        row = [x for x in (fp, vp, dm) if x]
        if len(row) > 1:
            self.resizeDocks(row, [1000, 230, 340][:len(row)], H)

    def _reset_layout(self):
        self.settings.remove("winstate")
        self._default_panels()
        self._rebuild_panels_menu()
        self.on_panel_source_changed()

    def _save_roster(self):
        try:
            self.settings.setValue("panels/roster", json.dumps({
                "panels": [dict(p.state(), visible=p.isVisible()) for p in self.panels],
                "groups": self.groups,
                "next_uid": self._next_uid,
            }))
        except (TypeError, ValueError):
            pass

    def _restore_panels(self):
        """Rebuild the roster BEFORE restoreState(): Qt silently drops saved
        geometry for docks that do not exist yet, so order matters."""
        data = None
        blob = self.settings.value("panels/roster")
        if isinstance(blob, str) and blob:
            try:
                data = json.loads(blob)
            except ValueError:
                data = None
        ok = False
        if data and data.get("panels"):
            for name, spec in (data.get("groups") or {}).items():
                if name in self.groups and isinstance(spec, dict):
                    self.groups[name].update(spec)
            self._next_uid = int(data.get("next_uid") or 1)
            for d in data["panels"]:
                cls = of_panels.PANEL_TYPES.get(d.get("kind"))
                if cls is None:
                    continue                      # unknown kind (older/newer build)
                p = self.add_panel(d["kind"], group=d.get("group"),
                                   uid=int(d.get("uid") or self._next_uid),
                                   show=bool(d.get("visible", True)))
                p.apply_state(d)
            ok = bool(self.panels)
        if not ok:
            self._default_panels()

        self._sync_group_controls()
        geo = self.settings.value("geometry")
        if isinstance(geo, (QtCore.QByteArray, bytes, bytearray)):
            try:
                self.restoreGeometry(geo)
            except (TypeError, ValueError):
                pass
        restored = False
        st = self.settings.value("winstate")
        if isinstance(st, (QtCore.QByteArray, bytes, bytearray)):
            try:
                restored = bool(self.restoreState(st))
            except (TypeError, ValueError):
                restored = False
        if not restored:
            self._default_layout()
        # safety net: a saved state where EVERY panel is hidden leaves a blank
        # window with no way back, so fall back to the default arrangement
        if self.panels and not any(p.isVisible() for p in self.panels):
            self._default_layout()
        self.toolbar.setVisible(True)             # never let a saved state hide it
        self._relink()
        self._sync_feeds()
        self._ensure_on_screen()

    # ------------------------------------------------------------------
    #  Linking
    # ------------------------------------------------------------------
    def _relink(self):
        """Within a link group, the footprint is the anchor: the delta footer
        shares its x-axis, and the price-axis panels share its y."""
        for g in of_panels.GROUPS:
            anchor = None
            for p in self.panels:
                if isinstance(p, of_panels.FootprintPanel) and p.group == g:
                    anchor = p
                    break
            for p in self.panels:
                if p.group != g or p is anchor or not isinstance(p, of_panels.ChartPanel):
                    continue
                tgt = anchor.p if anchor else None
                if isinstance(p, of_panels.DeltaPanel):
                    p.p.setXLink(tgt)
                elif isinstance(p, (of_panels.VapPanel, of_panels.HeatmapPanel,
                                    of_panels.DepthPanel)):
                    p.p.setYLink(tgt)

    # ------------------------------------------------------------------
    #  Toolbar <-> active group
    # ------------------------------------------------------------------
    def _sync_group_controls(self, *_):
        g = self.group_combo.currentText() or "A"
        spec = self.groups[g]
        for w in (self.sym_combo, self.bar_combo, self.size_spin):
            w.blockSignals(True)
        try:
            syms = self.symbols()
            cur = spec["symbol"]
            if cur and cur not in syms:
                syms = sorted(set(syms) | {cur})
            if [self.sym_combo.itemText(i) for i in range(self.sym_combo.count())] != syms:
                self.sym_combo.clear()
                self.sym_combo.addItems(syms)
            self.sym_combo.setCurrentText(cur or "")
            self.bar_combo.setCurrentText(spec["bar_kind"])
            self.size_spin.setValue(int(spec["bar_size"]))
        finally:
            for w in (self.sym_combo, self.bar_combo, self.size_spin):
                w.blockSignals(False)

    def _on_group_source_changed(self, *_):
        g = self.group_combo.currentText() or "A"
        sym = (self.sym_combo.currentText() or "").strip().upper()
        if sym and (len(sym) != 4 or not sym.isalpha()):
            return                                # mid-typing; wait for a valid ticker
        self.set_group_spec(g, symbol=sym or self.groups[g]["symbol"],
                            bar_kind=self.bar_combo.currentText(),
                            bar_size=self.size_spin.value())

    def _toggle_measure(self, on):
        self._measure = on
        if not on:
            for p in self.panels:
                if isinstance(p, of_panels.FootprintPanel):
                    p.clear_measure()

    # ------------------------------------------------------------------
    #  Data intake
    # ------------------------------------------------------------------
    def _tally(self, symbol, evs):
        """Running per-symbol stats for the watchlist, updated incrementally."""
        d = self._derived.setdefault(symbol, {"last": None, "trades": 0, "vol_sh": 0.0})
        for ev in evs:
            if ev[0] == "trade":
                r = ev[1]
                d["last"] = r["price"]
                d["trades"] += 1
                d["vol_sh"] += r.get("qty") or 0
            elif ev[0] == "summary":
                self._summaries[symbol] = ev[1]

    def rebuild(self):
        """Drop every cached model; they rebuild lazily from self.events, which
        holds the live stream too, so nothing is lost."""
        self.models.clear()
        self.refresh()

    def refresh(self):
        for p in self.panels:
            if p.isVisible():        # repaint only what is on screen; a tab behind
                p.refresh()          # another repaints on its visibilityChanged
        self._refresh_summary()
        self._refresh_regime_chip()
        g = self.group_combo.currentText() or "A"
        m = self._active_model()
        if m is not None:
            unit = 100 if self.cfg["header_units"] == "lots" else 1
            self.status_lbl.setText(
                "   %s  bars=%d  trades=%d  vol=%s %s  CVD=%s"
                % (self.groups[g]["symbol"], len(m.bar_ids()), len(m.trades),
                   format(m.total_volume() / unit, ",.0f"), self.cfg["header_units"],
                   format((m.cvd_y[-1] if m.cvd_y else 0) / unit, ",.0f")))
        if self.follow_chk.isChecked():
            self._center_latest(center_y=False)
        if self.debug:
            now = time.monotonic()
            if now - self._last_diag_t > 1.0:
                self._last_diag_t = now
                self._refresh_diag()

    def _active_model(self):
        g = self.group_combo.currentText() or "A"
        spec = self.groups[g]
        if not spec["symbol"]:
            return None
        return self.model_for(spec["symbol"], spec["bar_kind"], spec["bar_size"])

    def _active_footprints(self):
        g = self.group_combo.currentText() or "A"
        return [p for p in self.panels
                if isinstance(p, of_panels.FootprintPanel) and not p.isHidden()
                and (p.group == g or p.group is None)]

    def _center_latest(self, center_y=True):
        """Snap X to the newest bars and (optionally) centre Y on the last trade,
        preserving the current zoom spans — unlike autoRange."""
        for fp in self._active_footprints():
            m = fp.model()
            n = len(m.bar_ids()) if m else 0
            if not n:
                continue
            (x0, x1), (y0, y1) = fp.p.viewRange()
            xspan = max(x1 - x0, 2.0)
            right = n + max(0.05 * xspan, 0.8)   # margin so the live bar isn't glued on
            fp.p.setXRange(right - xspan, right, padding=0)
            if center_y and m.trades:
                cy = m.trades[-1]["price"]
                yspan = max(y1 - y0, fp.fp_item._tickval * 4)
                fp.p.setYRange(cy - yspan / 2, cy + yspan / 2, padding=0)

    def _refresh_summary(self):
        g = self.group_combo.currentText() or "A"
        s = self._summaries.get(self.groups[g]["symbol"])
        if not s:
            self.summary_lbl.setText("")
            return

        def n(k):
            v = s.get(k)
            return format(v, ",.0f") if isinstance(v, (int, float)) else "—"

        val = s.get("val") or 0
        lot = (s.get("vol_sh") or 0) / 100
        self.summary_lbl.setText(
            "  %s   Open %s   Prev %s   High %s   Low %s   Avg %s   Last %s   "
            "Lot %s   Val %sB   Trades %s"
            % (self.groups[g]["symbol"], n("open"), n("prev"), n("high"), n("low"),
               n("avg"), n("last"), format(lot, ",.0f"),
               format(val / 1e9, ",.2f"), n("trades")))

    def _refresh_regime_chip(self):
        c = self.cfg
        if not c["show_regime"]:
            self.regime_lbl.setVisible(False)
            return
        self.regime_lbl.setVisible(True)
        m = self._active_model()
        if m is None:
            self.regime_lbl.setText("")
            return
        r = m.regime(c["regime_window"], c["regime_warmup_min"], c["er_trend"], c["er_chop"])
        if not r["ready"]:
            mins = int(r.get("span", 0) // 60)
            self.regime_lbl.setText("REGIME: warming up (%d/%d min)"
                                    % (mins, c["regime_warmup_min"]))
            self._style_regime("#22262b", "#7a828c")
            return
        bg, fg = {"TREND↑": ("#173e28", "#4fe27a"), "TREND↓": ("#3e1a1a", "#ff6a6a"),
                  "CHOP": ("#22262b", "#c9ced4")}.get(r["core"], ("#2c2620", "#e6b450"))
        vr = ("  VR %.2f" % r["vr"]) if r.get("vr") is not None else ""
        self.regime_lbl.setText("%s   ER %.2f%s" % (r["label"], r["er"], vr))
        self.regime_lbl.setToolTip(
            "Efficiency Ratio %.2f (trend>=%s, chop<=%s)  ·  realized-vol pctile %.0f  ·  "
            "variance-ratio %s (>1 trend, <1 mean-revert)"
            % (r["er"], c["er_trend"], c["er_chop"], r["rv_pct"],
               ("%.2f" % r["vr"]) if r.get("vr") is not None else "—"))
        self._style_regime(bg, fg)

    def _style_regime(self, bg, fg):
        self.regime_lbl.setStyleSheet(
            "background:%s; color:%s; font-family:Consolas; font-weight:600; "
            "border-radius:3px; padding:1px 7px; margin:0 4px;" % (bg, fg))

    # ------------------------------------------------------------------
    #  Diagnostics (--debug)
    # ------------------------------------------------------------------
    @staticmethod
    def _diag_text(d, age=None):
        def f0(x):
            return "—" if x is None else "%.0f" % x
        parts = ["bars=%d" % d["bars"], "tpb=%.0f/%d" % (d["tpb_mean"], d["tpb_last"]),
                 "tr=%d" % d["trade_frames"], "buy%%=%.0f" % d["buy_pct"],
                 "(Q%d/T%d/C%d)" % (d["cls_quote"], d["cls_tick"], d["cls_carry"]),
                 "book=%df" % d["book_frames"], "crossed=%d" % d["crossed_book"],
                 "noBook=%d" % d["no_book"], "dedup=%d" % d["dedup_skips"],
                 "hm=%dc/trim%d" % (d["heatmap_cols"], d["heatmap_trimmed"]),
                 "spr=%s/%s/%s" % (f0(d["spread"]), f0(d["spread_med"]), f0(d["spread_p90"])),
                 "vap=%.2fM" % (d["vap_sh"] / 1e6), "fp=%.2fM" % (d["fp_sh"] / 1e6)]
        if abs(d["vap_sh"] - d["fp_sh"]) > 0.5:
            parts.append("‼FP≠VAP")               # aggregation invariant broken
        if age is not None:
            parts.append("age=%.1fs" % age)
        return " ".join(parts)

    @staticmethod
    def _diag_html(d, age=None):
        DIM, VAL, WARN, BAD = "#5f6b76", "#c9ced4", "#e6b450", "#ff5454"
        GRN, RED = "#3fe26a", "#ff5454"

        def v(txt, color=VAL, bold=False):
            w = "font-weight:600;" if bold else ""
            return "<span style='color:%s;%s'>%s</span>" % (color, w, txt)

        def lab(txt):
            return "<span style='color:%s'>%s</span>" % (DIM, txt)

        DOT = "<span style='color:%s'>&nbsp;·&nbsp;</span>" % DIM
        bp = d["buy_pct"]
        bp_c = GRN if bp >= 55 else RED if bp <= 45 else VAL
        burst = d["tpb_last"] > 3 * max(d["tpb_mean"], 1)
        flow = (lab("FLOW") + "&nbsp;" + v("tr %s" % format(d["trade_frames"], ",")) + DOT
                + v("buy %.0f%%" % bp, bp_c) + DOT
                + v("Q/T/C %d/%d/%d" % (d["cls_quote"], d["cls_tick"], d["cls_carry"])) + DOT
                + v("tpb %.0f/%d" % (d["tpb_mean"], d["tpb_last"]),
                    WARN if burst else VAL, bold=burst))
        s_now = "—" if d["spread"] is None else "%.0f" % d["spread"]
        wide = (d["spread_p90"] or 0) > 2 * (d["spread_med"] or 1)
        crossed_hot = d["crossed_book"] > max(5, 0.01 * max(d["book_frames"], 1))
        book = (lab("BOOK") + "&nbsp;"
                + v("spr %s/%.0f/%.0f" % (s_now, d["spread_med"] or 0, d["spread_p90"] or 0),
                    WARN if wide else VAL) + DOT
                + v("crossed %d" % d["crossed_book"], WARN if crossed_hot else VAL) + DOT
                + v("frames %s" % format(d["book_frames"], ",")))
        if age is None:
            age_h = v("age —")
        else:
            a_c = VAL if age < 3 else WARN if age < 10 else BAD
            age_h = v("age %.1fs" % age, a_c, bold=age >= 10)
        feed = (lab("FEED") + "&nbsp;" + age_h + DOT
                + v("dedup %d" % d["dedup_skips"]) + DOT
                + v("noBook %d" % d["no_book"]) + DOT
                + v("hm %d/%d" % (d["heatmap_cols"], d["heatmap_trimmed"])))
        ok = abs(d["vap_sh"] - d["fp_sh"]) <= 0.5
        chk = (lab("CHECK") + "&nbsp;"
               + (v("vap %.2fM = fp %.2fM ✓" % (d["vap_sh"] / 1e6, d["fp_sh"] / 1e6),
                    "#7fd18b") if ok else
                  v("‼ FP≠VAP (%.2fM vs %.2fM)" % (d["fp_sh"] / 1e6, d["vap_sh"] / 1e6),
                    BAD, bold=True)))
        return flow, book, feed, chk

    def _refresh_diag(self, to_stderr=False):
        m = self._active_model()
        if m is None:
            return ""
        age = (time.monotonic() - self._last_batch_t) if self._last_batch_t else None
        d = m.diag()
        text = self._diag_text(d, age)
        if self.diag_lbls:
            for lbl, html in zip(self.diag_lbls, self._diag_html(d, age)):
                lbl.setText(html)
        if to_stderr:
            print("[diag %s] %s" % (datetime.now().strftime("%H:%M:%S"), text),
                  file=sys.stderr, flush=True)
        return text

    # ------------------------------------------------------------------
    #  Settings
    # ------------------------------------------------------------------
    def _open_settings(self):
        SettingsDialog(self.cfg, self._on_settings_applied, self).exec()

    def _on_settings_applied(self, vals):
        old = dict(self.cfg)
        self.cfg.update(vals)
        if any(old.get(k) != self.cfg.get(k) for k in MODEL_CFG_KEYS):
            self.rebuild()
        else:
            self.refresh()
        t = getattr(self, "timer", None)
        if t:
            t.setInterval(max(40, int(1000 / max(self.cfg["live_hz"], 1))))
        self._save_settings()

    def _restore_cfg(self):
        s = self.settings
        for k, default in DEFAULTS.items():
            v = s.value("cfg/%s" % k)
            if v is None:
                continue
            kind, spec = SPEC_BY_KEY.get(k, (None, None))
            try:                                  # a stale/garbled/cross-version value
                if isinstance(default, bool):     # must not crash startup
                    val = v in (True, "true", "True", 1, "1")
                elif isinstance(default, int):    # bool first (bool subclasses int)
                    val = int(v)
                elif isinstance(default, float):
                    val = float(v)
                else:
                    val = str(v)
            except (TypeError, ValueError):
                continue
            if kind in ("int", "double") and spec:
                val = min(max(val, spec[0]), spec[1])
            elif kind == "choice" and spec and val not in spec:
                continue
            self.cfg[k] = val
        if s.value("cell_mode") == "delta":
            self.delta_cells.setChecked(True)
        try:
            bg = s.value("big_lots")
            if bg is not None:
                self.big_spin.setValue(int(bg))
        except (TypeError, ValueError):
            pass

    def _save_settings(self):
        s = self.settings
        s.setValue("cell_mode", self.cell_mode())
        s.setValue("big_lots", self.big_spin.value())
        for k, v in self.cfg.items():
            s.setValue("cfg/%s" % k, v)
        for p in self.panels:
            if isinstance(p, of_panels.DomPanel):
                p.save_header()
        s.setValue("geometry", self.saveGeometry())
        s.setValue("winstate", self.saveState())
        self._save_roster()

    def _ensure_on_screen(self):
        """Clamp/recentre so the window can't open off-screen or larger than the
        screen (a geometry saved on a bigger display, or a vanished monitor)."""
        if self.windowState() & QtCore.Qt.WindowState.WindowMaximized:
            return
        app = QtWidgets.QApplication.instance()
        center = self.frameGeometry().center()
        scr = app.screenAt(center) or app.primaryScreen()
        avail = scr.availableGeometry()
        if (avail.contains(center) and self.width() <= avail.width()
                and self.height() <= avail.height()):
            return
        w = max(800, min(self.width(), avail.width() - 20))
        h = max(500, min(self.height(), avail.height() - 60))
        self.resize(w, h)
        self.move(avail.left() + (avail.width() - w) // 2,
                  avail.top() + (avail.height() - h) // 2)

    # ------------------------------------------------------------------
    #  Session: token + who is recording
    # ------------------------------------------------------------------
    def _wanted_symbols(self):
        """The symbols on screen right now — what the feeds serve, what the
        writer lock records, and what the recorder is asked to capture."""
        return sorted({p.spec()["symbol"] for p in self.panels
                       if p.wants_model and not p.isHidden() and p.spec()["symbol"]})

    def _we_are_writer(self):
        """True only if this window took the lock. Deliberately not a pid
        comparison: pids are recycled, and a lock some other part of this
        process wrote is still not ours to release."""
        return self._holds_lock and of_capture.writer_status()[0]

    def _refresh_session(self):
        """Token age and who owns the archive, in plain English. Also heartbeats
        our own lock when this window is the writer — miss it for
        STALE_AFTER_SEC and other processes would rightly treat us as dead."""
        alive, info = of_capture.writer_status()
        mine = alive and self._we_are_writer()
        if mine:
            of_capture.touch_lock()
        state, ttext = of_startup.token_status()
        if alive:
            self._rec_asked_t = None
            syms = ", ".join(info.get("symbols") or []) or "?"
            where = "this window" if mine else "recorder"
            rec = ("<span style='color:#3fe26a'>&#9679; Recording %s (%s)</span>"
                   % (syms, where))
        else:
            rec = "<span style='color:#7f8792'>&#9675; Not recording</span>"
            # Pressed Record but nothing is holding the lock a moment later: the
            # recorder started and died (usually a missing token). Say why —
            # otherwise the chip just flips back with no explanation.
            if self._rec_asked_t is not None:
                waited = time.monotonic() - self._rec_asked_t
                if waited > 4.0:
                    self._rec_asked_t = None
                    why = next((ln for ln in reversed(of_capture.tail_log(25))
                                if "ENDED" in ln or "failed" in ln or "No parseable" in ln),
                               None)
                    self.status_lbl.setText(
                        "   recorder stopped immediately — %s"
                        % (why.strip() if why else "see data/capture.log"))
        self.session_lbl.setText(
            "<span style='color:%s'>%s</span>"
            "<span style='color:#5f6b76'> &nbsp;&middot;&nbsp; </span>%s%s&nbsp;&nbsp;"
            % (of_startup.TOKEN_COLOR[state], ttext, rec, self._integrity_chip()))
        self.rec_btn.setText(" ■ Stop " if alive else " ● Record ")

    def _integrity_chip(self):
        """Gaps across every open model, deduped: the same hole reaches every model
        on that symbol, so counting per-model would multiply it by the bar sizes
        you happen to have open."""
        seen, lost = set(), 0.0
        for m in self.models.values():
            for g in m.gaps:
                key = (g.get("symbol"), g.get("started"))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    lost += float(g.get("seconds") or 0.0)
                except (TypeError, ValueError):
                    pass
        if not seen:
            return ""
        return ("<span style='color:#5f6b76'> &nbsp;&middot;&nbsp; </span>"
                "<span style='color:#ff9f43'>%d gap%s, %.0fs lost</span>"
                % (len(seen), "" if len(seen) == 1 else "s", lost))

    def _get_token(self):
        of_startup.TokenDialog(self).exec()
        self._refresh_session()

    def _toggle_recording(self):
        alive, _info = of_capture.writer_status()
        if not alive:
            self._start_daemon()
        elif self._we_are_writer():
            self._release_writer()
            self.status_lbl.setText("   stopped recording — this window is view-only")
        else:
            of_capture.request_stop()      # cooperative: it closes its sink cleanly
            self.status_lbl.setText("   asked the recorder to stop...")
        self._refresh_session()

    def _start_daemon(self):
        """Launch the recorder DETACHED so it outlives this window — that is the
        whole point of a capture daemon. It writes its own lock; we only ever
        talk to it through data/capture.lock and data/capture.stop."""
        syms = self._wanted_symbols() or self.symbols()
        if self._sink is not None:         # hand the archive over rather than fight
            self._release_writer()
        self._rec_asked_t = time.monotonic()
        res = QtCore.QProcess.startDetached(
            _daemon_exe(), ["-m", "orderflow.capture"] + syms, str(REPO_ROOT))
        ok = res[0] if isinstance(res, (tuple, list)) else bool(res)
        if not ok:
            QtWidgets.QMessageBox.warning(
                self, "Could not start recording",
                "Failed to launch the recorder.\n\nYou can still start it "
                "yourself:\n    orderflow-capture %s" % " ".join(syms))
            return
        self.status_lbl.setText("   recording %s..." % ", ".join(syms))

    def _release_writer(self):
        """Stop writing from this window: close the sink, drop the lock, and
        restart the feeds without it (live_feed captured the sink by reference,
        so the threads must be rebuilt or they would write to a closed file)."""
        for sym in list(self.feeds):
            th = self.feeds.pop(sym)
            th.stop()
            th.wait(2000)
        if self._sink is not None:
            self._sink.close()
            self._sink = None
        self._persist = False
        if self._holds_lock:
            of_capture.release_lock()
            self._holds_lock = False
        self._sync_feeds()

    # ------------------------------------------------------------------
    #  Live feeds
    # ------------------------------------------------------------------
    def start_live(self):
        if self._persist:
            alive, info = of_capture.writer_status()
            if alive:
                # Someone already owns the archive. Chart read-only instead of
                # corrupting it — this is the --view-only you used to have to
                # remember, now decided for you.
                self._persist = False
                self.status_lbl.setText(
                    "   %s is already recording — this window is view-only"
                    % (info.get("owner") or "another writer"))
            else:
                # ONE mutex-guarded sink for every symbol: separate sinks (or
                # separate writer processes) interleave rows mid-snapshot and
                # corrupt the archive
                of_capture.take_lock(self._wanted_symbols() or self.symbols(),
                                     "chart")
                self._holds_lock = True
                self._sink = of_feed.CsvSink()
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._live_refresh)
        self.timer.start(max(40, int(1000 / max(self.cfg["live_hz"], 1))))
        if self.debug:
            self.diag_timer = QtCore.QTimer(self)
            self.diag_timer.timeout.connect(lambda: self._refresh_diag(to_stderr=True))
            self.diag_timer.start(3000)
        self._sync_feeds()

    def _sync_feeds(self):
        """One feed thread per symbol actually on screen; stopped when the last
        panel referencing it goes away."""
        if not self.live:
            return
        want = set(self._wanted_symbols())
        for sym in list(self.feeds):
            if sym not in want:
                self.feeds.pop(sym).stop()
        for sym in sorted(want - set(self.feeds)):
            th = FeedThread(sym, self._sink, reconnect=self.cfg["reconnect"],
                            backoff_max=self.cfg["backoff_max"])
            th.batch.connect(self._on_live_batch)
            th.status.connect(self._on_status)
            th.start()
            self.feeds[sym] = th

    @QtCore.Slot(str, str)
    def _on_status(self, symbol, text):
        self.status_lbl.setText("   %s: %s" % (symbol, text))

    @QtCore.Slot(str, list)
    def _on_live_batch(self, symbol, evs):
        self.events.setdefault(symbol, []).extend(evs)
        self._tally(symbol, evs)
        for key, m in self.models.items():        # fan out to every model on this symbol
            if key[0] == symbol:
                for ev in evs:
                    m.on_event(ev)
        self._dirty = True
        self._last_batch_t = time.monotonic()

    def _live_refresh(self):
        if self._dirty:
            self._dirty = False
            self.refresh()

    def closeEvent(self, ev):
        self._save_settings()
        for attr in ("timer", "diag_timer"):
            t = getattr(self, attr, None)
            if t:
                t.stop()
        for th in list(self.feeds.values()):
            th.stop()
            th.wait(2000)
        if self._sink:
            self._sink.close()
        if self._holds_lock:             # never leave a lock behind for a dead pid,
            of_capture.release_lock()    # and never drop one we did not take
        super().closeEvent(ev)


# ============================================================
#  Entry point
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="IDX orderflow workstation")
    ap.add_argument("--version", action="version",
                    version="orderflow-station %s" % __version__)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true", help="connect + chart live")
    mode.add_argument("--replay", action="store_true",
                      help="chart captured CSVs (default)")
    ap.add_argument("--symbol", nargs="+", default=["ASII"],
                    help="one or more 4-letter IDX tickers; the first three seed "
                         "link groups A, B and C")
    ap.add_argument("--bars", default="time", choices=["time", "tick", "volume"])
    ap.add_argument("--size", type=int, default=60)
    ap.add_argument("--shot", metavar="PNG", help="render once to PNG and exit (headless)")
    ap.add_argument("--secs", type=int, default=8, help="seconds to run before a live --shot")
    ap.add_argument("--history", choices=["today", "all", "none"], default="today",
                    help="live mode: preload prior captured CSV history (default today)")
    ap.add_argument("--reset-layout", action="store_true",
                    help="forget saved geometry, dock layout and panel roster")
    ap.add_argument("--reset-settings", action="store_true",
                    help="forget all saved Settings-panel values")
    ap.add_argument("--debug", action="store_true",
                    help="diagnostics counters (status bar + stderr every 3s live)")
    ap.add_argument("--doctor", action="store_true",
                    help="print environment/token/recorder diagnostics and exit — "
                         "run this first when it will not start")
    ap.add_argument("--ask", action="store_true",
                    help="show the Start dialog even if you ticked 'remember' "
                         "(holding Shift at launch does the same)")
    ap.add_argument("--view-only", action="store_true",
                    help="live chart without writing CSVs — use when the capture "
                         "daemon runs separately (two writers corrupt the archive)")
    args = ap.parse_args()

    if args.doctor:                      # before QApplication: doctor makes its own
        report = diagnostics.doctor()
        if sys.stdout is not None:       # always true now: install() adopted them
            sys.stdout.write(report)
        return

    app = QtWidgets.QApplication(sys.argv)
    app.setFont(QtGui.QFont("Segoe UI", 9))
    st = QtCore.QSettings("orderflow", "of_app")

    # Launched with no flags at all (double-clicked, or `orderflow-app`): ask,
    # rather than silently assuming replay/ASII. Any flag skips the dialog, so
    # scripts and --shot keep working exactly as before.
    want_record = False
    if len(sys.argv) == 1:
        saved = st.value("startup/values")
        try:
            prev = json.loads(saved) if saved else None
        except (TypeError, ValueError):
            prev = None
        remembered = st.value("startup/remember") in (True, "true", "True", 1, "1")
        # Shift at launch is the escape hatch when a remembered choice is what
        # is broken — otherwise the app repeats the failing config every time.
        # Guarded: a keyboard probe must never be the reason startup fails,
        # which is the exact class of bug this whole change exists to kill.
        try:
            shift = bool(QtWidgets.QApplication.queryKeyboardModifiers()
                         & QtCore.Qt.KeyboardModifier.ShiftModifier)
        except Exception:
            shift = False
        vals = prev if (remembered and prev and not args.ask and not shift) else None
        if vals is None:
            dlg = of_startup.StartDialog(prev)
            if dlg.exec() != QtWidgets.QDialog.DialogCode.Accepted:
                return
            vals = dlg.values()
            st.setValue("startup/remember", bool(vals.get("remember")))
            st.setValue("startup/values", json.dumps(vals))
        args.live = vals.get("mode") == "live"
        args.symbol = vals.get("symbols") or ["ASII"]
        args.bars = vals.get("bars", "time")
        args.size = int(vals.get("size", 60))
        args.history = vals.get("history", "today")
        args.debug = bool(vals.get("debug"))
        want_record = bool(vals.get("record"))

    syms = [s.upper() for s in args.symbol]
    bad = [s for s in syms if len(s) != 4 or not s.isalpha()]
    if bad:
        ap.error("not 4-letter tickers: %s" % ", ".join(bad))

    if args.reset_layout:
        st.remove("geometry")
        st.remove("winstate")
        st.remove("panels")
    if args.reset_settings:
        st.remove("cfg")

    # The General-tab "history" preload is a startup decision, so it only takes
    # effect on relaunch: honour the saved value unless --history was explicit.
    hist_mode = args.history
    if "--history" not in sys.argv:
        saved = st.value("cfg/history")
        if saved in ("today", "all", "none"):
            hist_mode = saved

    if args.live:
        events = load_history(hist_mode)
    else:
        events = {}
        for ev in of_feed.replay_feed():
            sym = _event_symbol(ev)
            if sym:
                events.setdefault(sym, []).append(ev)

    persist = bool(args.live and not args.shot and not args.view_only)
    if persist and len(syms) > 1:
        print("multi-symbol live: writing CSVs from the chart is risky beside the "
              "capture daemon — pass --view-only unless this is the only writer",
              file=sys.stderr)

    try:
        win = MainWindow(events, args.bars, args.size, syms, debug=args.debug,
                         live=args.live, persist=persist)
        win.show()
    except Exception:
        # A remembered startup that crashes would crash identically forever.
        # Forget it so the next launch asks, then let the handler report.
        st.setValue("startup/remember", False)
        st.sync()
        raise
    QtCore.QTimer.singleShot(0, win._ensure_on_screen)   # re-clamp after the WM places it

    if args.debug:
        print("[diag startup] %s" % win._refresh_diag())

    if args.shot and not args.live:               # replay snapshot
        for _ in range(4):
            app.processEvents()
        win.grab().save(args.shot)
        print("wrote %s" % args.shot)
        return

    if want_record:            # dialog asked for recording: daemon first, so
        win._start_daemon()    # start_live() then correctly sees it and goes view-only

    if args.live:
        win.start_live()

    if args.shot:                                 # timed live snapshot
        def finish():
            win.grab().save(args.shot)
            print("wrote %s (live %ds)" % (args.shot, args.secs))
            win.close()      # triggers closeEvent -> stops the feed threads cleanly
            app.quit()
        QtCore.QTimer.singleShot(args.secs * 1000, finish)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
