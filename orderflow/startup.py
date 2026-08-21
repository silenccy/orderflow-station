"""
startup.py — the dialogs that replace the command line.

    StartDialog    mode, symbols, bar basis, history, record — everything main()
                   used to take as flags
    TokenDialog    guided session-token grab, with a watcher that notices the
                   frame landing in data/ and validates it
    HelpDialog     the command reference, in the app

Flags still work and win when supplied; they are now the override, not the way
you launch. Nothing here ever displays the JWT: token age comes from the frame
file's mtime, which is the honest proxy for a ~24h session.
"""

import csv
import time

from PySide6 import QtCore, QtGui, QtWidgets

from . import feed as of_feed
from .paths import BOOK_CSV, DATA_DIR, TRADES_CSV

# The browser-console catcher, identical to the README's. Kept here so the app
# can hand it over with a Copy button instead of you finding the docs.
CATCHER_JS = """(() => {
  const send = WebSocket.prototype.send;
  let best = 0;
  WebSocket.prototype.send = function (data) {
    try {
      if (data instanceof ArrayBuffer || ArrayBuffer.isView(data)) {
        const b = new Uint8Array(data instanceof ArrayBuffer ? data : data.buffer);
        let sym = null;                       // 0x04 + 4 uppercase letters = a ticker
        for (let i = 0; i + 4 < b.length; i++) {
          if (b[i] === 4 && [1,2,3,4].every(j => b[i+j] >= 65 && b[i+j] <= 90)) {
            sym = String.fromCharCode(b[i+1], b[i+2], b[i+3], b[i+4]); break;
          }
        }
        if (b.length > 200 && sym && b.length > best) {
          best = b.length;
          const a = document.createElement('a');
          a.href = URL.createObjectURL(
            new Blob([Array.from(b).join(',')], { type: 'text/plain' }));
          a.download = `subscribe_${b.length}.txt`;
          a.click();
          console.log(`captured ${b.length}B subscribe frame for ${sym}`);
        }
      }
    } catch (e) {}
    return send.apply(this, arguments);
  };
  console.log('catcher armed - now open a ticker chart');
})();"""

TOKEN_OK, TOKEN_AGING, TOKEN_MISSING = "ok", "aging", "missing"
TOKEN_COLOR = {TOKEN_OK: "#3fe26a", TOKEN_AGING: "#e6b450", TOKEN_MISSING: "#ff5454"}


# ============================================================
#  Archive / token introspection
# ============================================================
def archive_symbols(max_rows=400000):
    """Symbols already captured. A streaming scan of the CSV symbol column —
    replay_feed() would parse and sort the entire archive just to list tickers."""
    for path in (TRADES_CSV, BOOK_CSV):
        syms = set()
        try:
            with open(path, newline="", encoding="utf-8") as f:
                rdr = csv.reader(f)
                header = next(rdr, None)
                if not header or "symbol" not in header:
                    continue
                i = header.index("symbol")
                for n, row in enumerate(rdr):
                    if n >= max_rows:
                        break
                    if len(row) > i and row[i]:
                        syms.add(row[i])
        except OSError:
            continue
        if syms:
            return sorted(syms)
    return []


def newest_frame():
    """The frame load_subscribe_frame() would pick: newest that parses."""
    best = None
    for p in DATA_DIR.glob("subscribe_*.txt"):
        try:
            of_feed.parse_frame_file(p)
            mt = p.stat().st_mtime
        except (OSError, ValueError):
            continue
        if best is None or mt > best[0]:
            best = (mt, p)
    return best[1] if best else None


def _age_text(seconds):
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return "%dm ago" % (seconds // 60)
    return "%dh ago" % (seconds // 3600)


def token_status():
    """(state, text). Age is the frame file's mtime — the token itself is never
    decoded or displayed."""
    p = newest_frame()
    if p is None:
        return TOKEN_MISSING, "No session token"
    age = time.time() - p.stat().st_mtime
    if age >= 24 * 3600:
        return TOKEN_MISSING, "Token expired (%s)" % _age_text(age)
    if age >= 20 * 3600:
        return TOKEN_AGING, "Token %s - expiring soon" % _age_text(age)
    return TOKEN_OK, "Token grabbed %s" % _age_text(age)


# ============================================================
#  Start dialog
# ============================================================
class StartDialog(QtWidgets.QDialog):
    """Everything main() used to take as flags, as a form."""

    def __init__(self, defaults=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Orderflow Station")
        self.setMinimumWidth(430)
        d = dict(defaults or {})

        lay = QtWidgets.QVBoxLayout(self)
        head = QtWidgets.QLabel("<b>Start a session</b>")
        lay.addWidget(head)

        form = QtWidgets.QFormLayout()
        lay.addLayout(form)

        # mode
        modebox = QtWidgets.QWidget()
        mrow = QtWidgets.QHBoxLayout(modebox)
        mrow.setContentsMargins(0, 0, 0, 0)
        self.rb_replay = QtWidgets.QRadioButton("Replay (offline)")
        self.rb_live = QtWidgets.QRadioButton("Live")
        self.rb_replay.setToolTip("Chart what you have already captured. No token needed.")
        self.rb_live.setToolTip("Connect to the feed. Needs a valid session token.")
        (self.rb_live if d.get("mode") == "live" else self.rb_replay).setChecked(True)
        mrow.addWidget(self.rb_replay)
        mrow.addWidget(self.rb_live)
        mrow.addStretch(1)
        form.addRow("Mode", modebox)

        # symbols
        self.symlist = QtWidgets.QListWidget()
        self.symlist.setMaximumHeight(120)
        self.symlist.setToolTip("Tick the tickers to chart. The first three seed\n"
                                "link groups A, B and C.")
        known = archive_symbols()
        want = [s.upper() for s in (d.get("symbols") or [])]
        for sym in sorted(set(known) | set(want)) or ["ASII"]:
            it = QtWidgets.QListWidgetItem(sym)
            it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
            it.setCheckState(QtCore.Qt.CheckState.Checked if sym in want
                             else QtCore.Qt.CheckState.Unchecked)
            self.symlist.addItem(it)
        if not want and self.symlist.count():
            self.symlist.item(0).setCheckState(QtCore.Qt.CheckState.Checked)
        self.symlist.itemChanged.connect(lambda *_: self._validate())
        form.addRow("Symbols", self.symlist)

        addrow = QtWidgets.QWidget()
        arow = QtWidgets.QHBoxLayout(addrow)
        arow.setContentsMargins(0, 0, 0, 0)
        self.add_edit = QtWidgets.QLineEdit()
        self.add_edit.setPlaceholderText("add a ticker, e.g. BBCA")
        self.add_edit.setMaxLength(4)
        self.add_edit.returnPressed.connect(self._add_symbol)
        add_btn = QtWidgets.QPushButton("Add")
        add_btn.clicked.connect(self._add_symbol)
        arow.addWidget(self.add_edit)
        arow.addWidget(add_btn)
        form.addRow("", addrow)

        # bars
        barbox = QtWidgets.QWidget()
        brow = QtWidgets.QHBoxLayout(barbox)
        brow.setContentsMargins(0, 0, 0, 0)
        self.bars = QtWidgets.QComboBox()
        self.bars.addItems(["time", "tick", "volume"])
        self.bars.setCurrentText(d.get("bars", "time"))
        self.size = QtWidgets.QSpinBox()
        self.size.setRange(1, 100000)
        self.size.setValue(int(d.get("size", 60)))
        brow.addWidget(self.bars)
        brow.addWidget(QtWidgets.QLabel("size"))
        brow.addWidget(self.size)
        brow.addStretch(1)
        form.addRow("Bars", barbox)

        self.history = QtWidgets.QComboBox()
        self.history.addItems(["today", "all", "none"])
        self.history.setCurrentText(d.get("history", "today"))
        self.history.setToolTip("How much of the captured archive to preload in live mode")
        form.addRow("Preload history", self.history)

        self.record = QtWidgets.QCheckBox("Also record to disk (starts the capture daemon)")
        self.record.setChecked(bool(d.get("record", False)))
        self.record.setToolTip("Recording keeps running after you close this window,\n"
                               "so you can reopen charts without losing tape.")
        lay.addWidget(self.record)

        self.debug = QtWidgets.QCheckBox("Show diagnostics bar")
        self.debug.setChecked(bool(d.get("debug", False)))
        lay.addWidget(self.debug)

        self.token_lbl = QtWidgets.QLabel("")
        lay.addWidget(self.token_lbl)

        self.remember = QtWidgets.QCheckBox("Remember these choices and skip this next time")
        self.remember.setChecked(bool(d.get("remember", False)))
        lay.addWidget(self.remember)

        btns = QtWidgets.QDialogButtonBox()
        self.token_btn = btns.addButton("Get token...",
                                        QtWidgets.QDialogButtonBox.ButtonRole.ResetRole)
        self.start_btn = btns.addButton("Start",
                                        QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole)
        btns.addButton(QtWidgets.QDialogButtonBox.StandardButton.Cancel)
        self.token_btn.clicked.connect(self._get_token)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

        for w in (self.rb_live, self.rb_replay):
            w.toggled.connect(self._validate)
        self._validate()

    # ---- helpers ----
    def _add_symbol(self):
        sym = self.add_edit.text().strip().upper()
        if len(sym) != 4 or not sym.isalpha():
            self.add_edit.setStyleSheet("border:1px solid #ff5454;")
            return
        self.add_edit.setStyleSheet("")
        self.add_edit.clear()
        for i in range(self.symlist.count()):
            if self.symlist.item(i).text() == sym:
                self.symlist.item(i).setCheckState(QtCore.Qt.CheckState.Checked)
                return
        it = QtWidgets.QListWidgetItem(sym)
        it.setFlags(it.flags() | QtCore.Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(QtCore.Qt.CheckState.Checked)
        self.symlist.addItem(it)

    def _get_token(self):
        TokenDialog(self).exec()
        self._validate()

    def _validate(self, *_):
        live = self.rb_live.isChecked()
        self.history.setEnabled(live)
        self.record.setEnabled(live)
        state, text = token_status()
        if live:
            self.token_lbl.setText(
                "<span style='color:%s'>%s</span>%s"
                % (TOKEN_COLOR[state], text,
                   "" if state == TOKEN_OK else " - live mode needs a fresh one"))
        else:
            self.token_lbl.setText("<span style='color:#7f8792'>"
                                   "Replay needs no token.</span>")
        self.start_btn.setEnabled(bool(self.symbols()))

    def symbols(self):
        return [self.symlist.item(i).text() for i in range(self.symlist.count())
                if self.symlist.item(i).checkState() == QtCore.Qt.CheckState.Checked]

    def values(self):
        """The same shape main() builds from argparse."""
        return {"mode": "live" if self.rb_live.isChecked() else "replay",
                "symbols": self.symbols(),
                "bars": self.bars.currentText(),
                "size": self.size.value(),
                "history": self.history.currentText(),
                "record": self.record.isChecked() and self.rb_live.isChecked(),
                "debug": self.debug.isChecked(),
                "remember": self.remember.isChecked()}


# ============================================================
#  Token dialog
# ============================================================
class TokenDialog(QtWidgets.QDialog):
    """Walks the browser-console grab and confirms the frame actually landed."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Get a session token")
        self.setMinimumWidth(620)
        self._seen = newest_frame()
        self._seen_mt = self._seen.stat().st_mtime if self._seen else 0

        lay = QtWidgets.QVBoxLayout(self)
        lay.addWidget(QtWidgets.QLabel(
            "<b>The feed authenticates with a frame your own browser sends.</b><br>"
            "It lasts about 24 hours, and one frame works for every ticker."))

        steps = QtWidgets.QLabel(
            "<ol>"
            "<li>Open <b>stockbit.com</b> and log in.</li>"
            "<li>Press <b>F12</b> and go to the <b>Console</b> tab. "
            "If pasting is blocked, type <code>allow pasting</code> first.</li>"
            "<li><b>Copy</b> the snippet below, paste it in the console, press Enter.</li>"
            "<li>Open a ticker chart you have not opened yet this session. "
            "A <code>subscribe_*.txt</code> downloads.</li>"
            "<li>Move it into <b>data/</b> - this window will confirm.</li>"
            "</ol>")
        steps.setWordWrap(True)
        lay.addWidget(steps)

        self.js = QtWidgets.QPlainTextEdit(CATCHER_JS)
        self.js.setReadOnly(True)
        self.js.setMaximumHeight(150)
        self.js.setStyleSheet("font-family:Consolas; font-size:11px;")
        lay.addWidget(self.js)

        row = QtWidgets.QHBoxLayout()
        copy = QtWidgets.QPushButton("Copy snippet")
        copy.clicked.connect(self._copy)
        openb = QtWidgets.QPushButton("Open stockbit.com")
        openb.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl("https://stockbit.com")))
        folder = QtWidgets.QPushButton("Open data folder")
        folder.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(
            QtCore.QUrl.fromLocalFile(str(DATA_DIR))))
        for b in (copy, openb, folder):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.status = QtWidgets.QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)

        # watch data/ so the confirmation is automatic
        self._watcher = QtCore.QFileSystemWatcher([str(DATA_DIR)], self)
        self._watcher.directoryChanged.connect(self._check)
        self._timer = QtCore.QTimer(self)     # a watcher can miss fast moves
        self._timer.timeout.connect(self._check)
        self._timer.start(1500)
        self._check()

    def _copy(self):
        QtWidgets.QApplication.clipboard().setText(CATCHER_JS)
        self.status.setText("<span style='color:#7f8792'>Snippet copied - "
                            "paste it into the browser console.</span>")

    def _check(self):
        p = newest_frame()
        if p is None:
            state, text = token_status()
            self.status.setText("<span style='color:%s'>%s. Waiting for a frame in "
                                "data/ ...</span>" % (TOKEN_COLOR[state], text))
            return
        mt = p.stat().st_mtime
        try:
            frame = of_feed.parse_frame_file(p)
        except (OSError, ValueError):
            return
        sym = of_feed.frame_symbol(frame)
        fresh = mt > self._seen_mt
        state, text = token_status()
        self.status.setText(
            "<span style='color:%s'>%s %s - %d bytes, native symbol %s.</span>"
            "<br><span style='color:#7f8792'>Any 4-letter ticker works from this "
            "one frame.</span>"
            % (TOKEN_COLOR[state], "Captured" if fresh else text, p.name,
               len(frame), sym.decode() if sym else "?"))


# ============================================================
#  Command reference, in the app
# ============================================================
HELP_HTML = """
<h3>You do not need any of this</h3>
<p>Everything below has a button somewhere in the app. The flags exist for
scripting and headless rendering.</p>
<h3>orderflow.app &mdash; the terminal</h3>
<table cellpadding=4>
<tr><td><code>--replay</code> / <code>--live</code></td>
    <td>chart captured CSVs (default) or connect to the feed</td></tr>
<tr><td><code>--symbol ASII BBCA</code></td>
    <td>one or more tickers; the first three seed link groups A, B, C</td></tr>
<tr><td><code>--view-only</code></td>
    <td>live without writing CSVs (applied automatically when something else
        is recording)</td></tr>
<tr><td><code>--bars time|tick|volume</code>, <code>--size N</code></td>
    <td>bar basis and size</td></tr>
<tr><td><code>--history today|all|none</code></td><td>history preload in live mode</td></tr>
<tr><td><code>--debug</code></td><td>diagnostics status bar</td></tr>
<tr><td><code>--shot out.png [--secs N]</code></td><td>render once to PNG and exit</td></tr>
<tr><td><code>--reset-layout</code></td><td>forget geometry, docks and the panel roster</td></tr>
<tr><td><code>--reset-settings</code></td><td>forget saved Settings values</td></tr>
</table>
<h3>orderflow.capture &mdash; the recorder</h3>
<table cellpadding=4>
<tr><td><code>orderflow-capture ASII BBCA</code></td><td>record several tickers at once</td></tr>
<tr><td><code>--status</code></td><td>is anything recording?</td></tr>
<tr><td><code>--stop</code></td><td>ask it to shut down cleanly</td></tr>
</table>
<p>Only one process may write the archive. That is enforced by
<code>data/capture.lock</code>, so a second recorder refuses to start rather than
corrupting your CSVs.</p>
<h3>orderflow.backtest &mdash; regime evaluation</h3>
<table cellpadding=4>
<tr><td><code>--symbol ASII</code></td><td>which captured days to evaluate</td></tr>
<tr><td><code>--window 20 --warmup 20</code></td><td>ER lookback and warm-up gate</td></tr>
<tr><td><code>--er-trend 0.5 --er-chop 0.3</code></td><td>label thresholds</td></tr>
<tr><td><code>--horizons 5,10,20</code></td><td>forward horizons in bars</td></tr>
<tr><td><code>--fees 0.15,0.25</code></td><td>%/side buy,sell</td></tr>
<tr><td><code>--csv out.csv</code></td><td>dump per-signal rows</td></tr>
</table>
"""


class HelpDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Command reference")
        self.resize(640, 560)
        lay = QtWidgets.QVBoxLayout(self)
        view = QtWidgets.QTextBrowser()
        view.setHtml(HELP_HTML)
        lay.addWidget(view)
        btns = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)
