# Orderflow Station — architecture & research notes

Notes from a full read of the source at commit `b80e257`. Untracked file (`.gitignore`
doesn't cover `*.md`) — delete freely.

**What it is:** an unofficial orderflow terminal for Indonesian equities (IDX). It speaks
Stockbit Pro's private, undocumented market-data websocket, hand-decodes the protobuf, and
renders footprint charts, a liquidity heatmap, volume profile, CVD and a DOM+tape in
PySide6/pyqtgraph. MIT, v1.0.0, 2 commits, ~152 KB of source across 9 files.

---

## 1. Layout

```
orderflow/
  paths.py     1.2 KB   every file location, one place
  feed.py     17.9 KB   websocket protocol, frame parsing, CSV persistence, live+replay
  model.py    20.7 KB   pure aggregation, no Qt
  app.py      86.9 KB   PySide6/pyqtgraph GUI — 9 classes, ~1840 lines
  backtest.py 13.8 KB   walk-forward regime evaluation
  capture.py   2.2 KB   headless daemon (61 lines, thin wrapper)
  token.py     6.5 KB   Playwright session-frame grabber
tools/decode_frame.py   protobuf frame pretty-printer (the tool used to reverse the format)
docs/protocol.md        the reverse-engineered protocol reference
data/                   CSVs, session frames, browser profile — gitignored
```

**Dependency direction is strictly one-way** and there are no cycles:

```
paths ──▶ feed ──▶ model ──▶ app
             │        │
             ├────────┴──▶ backtest
             ├──▶ capture
             └──▶ token   (imports feed only to reuse frame_symbol())
```

The load-bearing design decision: **`model.py` imports no Qt.** `feed → model` runs headless,
which is exactly how `backtest.py` and `capture.py` work. Live and replay emit the identical
`("book" | "trade" | "summary", …)` event stream, so the GUI genuinely cannot tell them apart —
that's what makes `--replay` a faithful rehearsal rather than a separate code path.

---

## 2. The wire protocol (`docs/protocol.md` + `feed.py`)

| | |
|---|---|
| URL | `wss://wss-jkt.trading.stockbit.com/ws` |
| Subprotocol | `web` — handshake is rejected without it |
| Headers | `Origin: https://stockbit.com` + a Firefox UA |
| Auth | **no cookie on the handshake** — the JWT rides *inside* the subscribe frame |
| Keepalive | app-level `bytes([34,6,10,4,112,105,110,103])` (protobuf field 4 = `"ping"`) every 10 s, with the library's own WS ping disabled (`ping_interval=None`) |

### The subscribe frame

~916–922 bytes of protobuf: field 1 user id, field 2 the symbol repeated across channel
sub-fields, field 3 a 44-byte base64 device key, field 5 the ~838-byte JWT (~24 h life).
The 922-byte variant carries 4 channels (book **and** trades); 916 carries 3 — which is why
the browser catcher keeps only the largest frame it sees.

### The symbol-swap trick

The single cleverest thing in the codebase ([feed.py:93-111](orderflow/feed.py:93)):

```python
if base[i] == 0x04 and all(65 <= base[i + 1 + j] <= 90 for j in range(4)):
```

The ticker is length-prefixed `0x04` + 4 uppercase ASCII. `0x04` is a control byte that
cannot appear inside base64, so scanning for that pattern can only ever hit a real symbol
field — never the token. `make_subscribe_for()` does a blind `bytes.replace`, and one
captured frame therefore subscribes **any** 4-letter IDX ticker. You re-grab when the
*token* expires, never when you change symbol.

### Frame types — dispatched on shape, not order

`live_feed` tries three parsers in sequence and takes the first that matches
([feed.py:365-392](orderflow/feed.py:365)):

1. **Book** — a protobuf wrapper around *pipe-delimited ASCII*, matched by regex
   `([A-Z]{4})\|(OFFER|BID)\|([0-9;|]+)`. Levels are `price;freq;value` triplets, ~60 per
   side (not the 5-level retail cap). `freq` is the **number of resting orders** at that
   level — genuinely unusual for a retail feed. Each frame is a **full snapshot of one
   side**, not a delta.
2. **Trade** — pure protobuf. Wrapper field 8 holds records in field 1; per record
   #3 price, #4 qty (shares), #9 trade id, #11 value, #5 an unresolved flag.
3. **Summary** — field-9 wrapper with session OHLC, volume, trade count, and the
   exchange's own VWAP in #10.

Anything else (pong, analytics) is silently dropped.

### Two units traps the author calls out, and they're right

- **`value` is shares, so `lots = value / 100`** — not `value / price / 100`. `docs/protocol.md`
  documents an earlier revision of the notes getting this wrong and yielding ~0.6 lots.
- **Tick size comes from the book ladder, not traded prices.** The book is dense and exactly
  tick-spaced by the exchange; a sparse day or one off-tick negotiated print would otherwise
  corrupt the price grid. See §4.

### Aggressor side is not transmitted

Field 5 exists but does not reliably indicate direction. Side is inferred client-side —
see `classify()` below. Consequence worth internalizing: **trades arriving in the reconnect
backfill, before the first book snapshot, classify by tick/carry only.** Their buy/sell
split is materially less trustworthy than live ticks.

---

## 3. Persistence & replay (`feed.py`)

`CsvSink` appends to three CSVs plus a raw JSONL, **flushing on every single write**
([feed.py:285-307](orderflow/feed.py:285)) so a hard kill never loses data.

| File | Columns |
|---|---|
| `book.csv` | recv_time, symbol, side, price, freq, value |
| `trades.csv` | recv_time, symbol, trade_time, price, qty, lots, value, trade_id, flag |
| `summary.csv` | recv_time, symbol, last, open, prev, high, low, avg, vol_sh, val, trades |
| `capture_raw.jsonl` | `{"t": iso, "hex": …}` — every **received** frame |

`capture_raw.jsonl` records inbound frames only. The subscribe frame is outbound and never
logged, so the raw log carries no token. That's deliberate and correct.

`replay_feed()` reconstructs book snapshots from per-level rows with `itertools.groupby` on
`(recv_time, symbol, side)`. **This is why the one-writer rule in the README is not advisory.**
`groupby` only groups *adjacent* rows — two processes interleaving writes doesn't just
duplicate data, it silently shreds book snapshots into fragments that still parse. That's a
corrupt archive you won't notice until you replay it.

---

## 4. Aggregation (`model.py`) — the analytical core

### Trade classification — Lee-Ready, isolated in one function

```python
def classify(price, bid, ask, last_price, last_side):
    if ask is not None and price >= ask: return "buy"    # quote rule
    if bid is not None and price <= bid: return "sell"
    if last_price is not None:                            # tick rule inside the spread
        if price > last_price: return "buy"
        if price < last_price: return "sell"
    return last_side                                      # zero tick: carry
```

Twelve lines, one responsibility, no state. `_on_trade` then *mirrors* the same branch
structure purely to increment `cls_quote` / `cls_tick` / `cls_carry` counters — so `--debug`
tells you what fraction of your delta is well-founded (quote rule) versus inferred (carry).
A high carry ratio means your delta is mostly guesswork. That's an honest diagnostic most
retail tools don't expose.

### Bar strategies

`TimeBars` (bar id = start epoch), `TickBars` (N trades), `VolumeBars` (N lots × 100 shares).
Note `bar_for()` on the latter two is **stateful and mutating** — it must be called exactly
once per trade. It is (`_on_trade`), but it's a sharp edge if anyone refactors.

### Bar metadata is out-of-order-robust

```python
if ep < m["t0"]: m["t0"] = ep; m["o"] = price
if ep >= m["t1"]: m["t1"] = ep; m["c"] = price
```

Open/close track the earliest/latest *event timestamps*, not arrival order — so a backfill
batch landing out of sequence still produces a correct candle. Small detail, easy to get
wrong, done right.

### Value area

`value_area(coverage=0.70)` grows a band outward from the POC, at each step taking the
heavier neighbour, until the accumulated volume reaches coverage. Note this is the
**one-row-at-a-time** variant, not the classic TPO two-rows-per-side method — results will
differ slightly from a Market Profile textbook. Not wrong, just a different convention.

### `tick_size()` — the one to appreciate

```python
prices = sorted(set(self.book.bids) | set(self.book.asks))
if len(prices) < 3: prices = sorted(self.vap)          # fall back to traded prices
gaps = Counter(round(b - a, 6) for a, b in zip(prices, prices[1:]))
return max(gaps.items(), key=lambda kv: (kv[1], kv[0]))[0]
```

Book first (dense, exchange-spaced), traded prices only as fallback, and ties broken toward
the **larger** gap because off-tick prints create small bogus gaps. IDX uses a
price-dependent tick ladder, so hardcoding would be wrong; deriving it from the book is the
right call.

### The `diag()` invariant

`vap_sh` (volume summed from the volume profile) and `fp_sh` (summed from the footprint)
are computed by independent paths from the same trades. **If they diverge, aggregation has
a bug.** Shipping a self-checking invariant in the debug readout is a genuinely good
engineering instinct.

### The regime filter

`regime()` returns `{ready, er, direction, rv, rv_pct, vol_state, core, label, vr, bars, span}`.

- **Kaufman Efficiency Ratio** over `window` bar closes: `|net move| / Σ|per-bar move|`, in [0,1].
- **Realized vol percentile** — stdev of log returns, ranked against its own rolling history.
  ≥70 → `HI VOL`, ≤30 → `LO VOL`.
- **Lo–MacKinlay variance ratio** VR(q): `Var(q-period) / (q · Var(1-period))`. >1 trending,
  <1 mean-reverting. Reported alongside; **not** used in the label.
- **Labels:** ER ≥ 0.5 → `TREND↑/↓` (by sign of net move); ER ≤ 0.3 → `CHOP`; else `MIXED`.
- **Double warm-up gate:** needs both `window+1` real bars *and* ≥ `warmup_min` minutes of
  session span before it computes anything. ER on a half-window is noise, and the code
  refuses to pretend otherwise.

**The author's own verdict, which you should take seriously:** unvalidated on IDX. Over six
captured ASII days / 201 evaluations, only `CHOP` did real work — it reliably preceded low
forward efficiency and 1–2 tick moves. `TREND↑` fired twice in six days. Treat it as a
*stay-out filter*, not a trend caller.

---

## 5. GUI (`app.py`)

> Describes the **upstream** single-window build. Locally this is now split across
> `items.py` / `panels.py` / `app.py` and every panel is a dock — see §9.

Nine classes. Five are custom `pg.GraphicsObject` / `ImageItem` / `QStyledItemDelegate`
painters — this is hand-rolled rendering, not stock pyqtgraph plots.

| Class | Base | Role |
|---|---|---|
| `MainWindow` | `QMainWindow` | ~1000 lines; owns model, panels, refresh cycle |
| `FootprintItem` | `pg.GraphicsObject` | bid\|ask cells, POC, diagonal imbalance, absorption |
| `SmoothImageItem` | `pg.ImageItem` | bilinear-filtered heatmap |
| `DeltaFooterItem` | `pg.GraphicsObject` | per-bar delta bars |
| `HeatmapCandleItem` | `pg.GraphicsObject` | OHLC overlaid on heatmap columns |
| `DepthBarDelegate` | `QStyledItemDelegate` | depth bars behind DOM numbers, wall detection |
| `CommaAxis` | `pg.AxisItem` | thousands separators instead of scientific notation |
| `SettingsDialog` | `QDialog` | tabbed editor generated from `SETTINGS_SPEC` |
| `FeedThread` | `QThread` | asyncio loop → Qt signals |

### Threading

`FeedThread.run()` creates a private asyncio event loop, iterates `live_feed()`, and
**batches events for ~100 ms** before emitting `batch(list)` to the main thread
([app.py:1697-1710](orderflow/app.py:1697)). `_on_live_batch` feeds the model incrementally
and sets `_dirty`; a `QTimer` at `live_hz` (default 7 Hz, floored at 40 ms) calls `refresh()`
only if dirty. Shutdown is clean: `closeEvent` → `call_soon_threadsafe(task.cancel)` → `wait(2000)`.

The separation matters: **`refresh()` redraws from the current model; `rebuild()` re-runs
every stored event through a fresh model.** Only `hm_throttle` and `hm_window` (`MODEL_CFG_KEYS`)
need a rebuild — everything else is a repaint. `rebuild(preserve_view=True)` restores the
view range so changing a setting doesn't throw away your zoom.

### Settings

~60 keys in `DEFAULTS`, declared once in `SETTINGS_SPEC` as `(tab, [(key, label, kind, spec)])`
and used to *generate* the dialog. Persisted to `QSettings("orderflow", "of_app")` under
`cfg/*` (Windows registry), with geometry and dock state alongside. `--reset-settings` /
`--reset-layout` exist precisely because a bad config or an off-screen window is otherwise
unrecoverable.

### Rendering details worth knowing

- Volume shading uses the **95th percentile** of cell totals as reference, not the max — one
  huge print can't wash out the whole chart.
- Imbalance floor is `max(configured_min, median cell total)` per bar — adapts to activity.
- CVD **breaks the line** across gaps >300 s (`connect="finite"` with NaN insertion) rather
  than drawing a fake flat segment through a capture hole. Honest charting.
- `--shot` sets `QT_QPA_PLATFORM=offscreen` *before* PySide6 is imported (app.py:32) — the
  only place the ordering works.

---

## 6. Backtest (`backtest.py`)

Methodology is more careful than the "toy strategy" label suggests:

- **No lookahead.** `run_segment` evaluates `regime()` at each bar boundary *before* applying
  the new bar's first trade — just-closed bar final, forming bar empty. Execution price is
  that first trade (`next_open`), i.e. a next-bar-open fill.
- **Purging.** Days split into segments at >30 min trade gaps (capture holes / lunch), and
  signals whose horizon would cross the segment end are dropped.
- **Overlap-aware stats.** Every metric is reported twice — `overlap` (all signals) and
  `indep` (non-overlapping k-bar windows). Overlapping windows inflate significance; showing
  both is the honest move.
- **Costs.** Half-spread estimated from the modal traded-price gap over median price, applied
  on both sides, plus `--fees 0.15,0.25` %/side (IDX retail defaults). Pre-cost PnL printed
  alongside so you can see what costs ate.
- **Permutation test.** Block-shuffles label *runs* within each segment (preserving run-length
  structure, so autocorrelation isn't destroyed) over 1000 reps; p = P(random ≥ actual).
- **Guard rails.** `--sweep` refuses to run on <8 days. The report ends with a literal
  `HONESTY:` line stating the day count and warning against tuning on it.
- `--allow-short` is flagged "info only; retail IDX can't short".

Anti-overfitting discipline is baked into the tool rather than left to the user's virtue.

---

## 7. Token handling (`token.py`)

Launches Chromium via `launch_persistent_context` into `data/pw_profile/`, attaches a CDP
session, and listens for `Network.webSocketCreated` / `Network.webSocketFrameSent`. It filters
to binary frames (opcode 2) on the market-data host, base64-decodes, validates with
`_looks_like_subscribe` (>200 bytes and contains a `0x04`+4-uppercase symbol), and keeps the
largest.

**Credential handling is clean:**

- The script never types, prompts for, reads or stores a username or password. `--login`
  opens a visible window and waits on `input()` — **you** log in by hand.
- The captured frame is written atomically (`.tmp` → `replace`) so a crash never leaves a
  half-written token.
- The JWT is written to a gitignored file and **never printed**. `_save()` reports only byte
  length and native symbol.
- `refresh()` catches everything and returns `(ok, msg)`, so a failed grab degrades to "use
  the existing frame" instead of blocking startup.

**The real secret on disk is `data/pw_profile/`** — an unencrypted Chromium profile holding
live Stockbit session cookies. Gitignored, but it's a logged-in session sitting in your
project folder. Set `ORDERFLOW_DATA` to somewhere you'd be comfortable keeping a password
manager if that bothers you.

---

## 8. Weaknesses found in the code

These are real, ordered by how likely they are to bite you.

1. ~~**No reconnect, anywhere.**~~ **Fixed** — see §9. `live_feed` now retries with a
   jittered exponential backoff and records every hole to `data/gaps.csv`. This was the
   highest-value patch the project could take, and it is done.
2. **Blocking I/O in the async loop.** `CsvSink` flushes on every write, synchronously,
   inside the message loop. Durable, but a slow disk stalls frame reception. Fine at IDX
   single-symbol rates; would not survive a busier feed.
3. **Unbounded memory.** `seen_ids` (feed) and `_seen_ids` / `trades` / `cvd_x` / `cvd_y`
   (model) grow for the life of the process with no eviction. Only `heatmap` is capped
   (`_heatmap_max`, default 1500 columns). A multi-day daemon run will creep.
4. **`--replay` is a no-op flag.** `args.replay` is never read; replay is simply the else-branch
   of `--live`. Harmless — it documents intent — but it isn't wired to anything.
5. **Local-timezone epochs.** `_parse_iso_epoch` uses naive `datetime.fromisoformat().timestamp()`,
   so CSVs are interpreted in the *reading* machine's timezone. Archives aren't portable
   across zones, and a DST boundary mid-archive will skew bar alignment.
6. **`replay_feed` loads everything into memory and sorts.** Fine for days, not for months.
   `--history all` on a large archive will be slow and heavy.

None of these are wrong-answer bugs in the aggregation math — which is the part that would
be hardest to notice and most damaging. The math reads carefully done; the operational
robustness is where the gaps are.

---

## 9. Local changes (diverged from upstream `b80e257`)

Two edits on top of the upstream code. Not pushed anywhere.

### Playwright token grabber removed

`orderflow/token.py` deleted; `--grab` gone from `app.py` and `capture.py`; `PW_PROFILE` /
`GRAB_CONFIG` / `SUBSCRIBE_AUTO` gone from `paths.py`; the `[token]` extra gone from
`pyproject.toml`. Reason: Google blocks OAuth sign-in in automation-controlled browsers, so
the one-time `--login` could never complete on a Google-SSO Stockbit account. The manual
browser-console grab is now the only path and is promoted to the main README section.

Sections 7 (Token handling) and the Playwright lines in §1 above describe code that no
longer exists here — kept because they document what upstream still ships.

**Knock-on fix, and this one mattered.** `load_subscribe_frame()` chose the **largest**
frame, which was only safe because `subscribe_auto.txt` from the grabber was checked first.
With the grabber gone, today's 922-byte grab and yesterday's expired 922-byte grab tie on
size, so the stale one would win forever — presenting as "socket connects, no data", the
exact symptom the README blames on expiry. It now picks the **newest** parseable frame.
Within one browser session the catcher only downloads progressively larger frames, so
newest is also the richest-channel one; the rules diverge only across days, where
freshness is the thing that matters.

### Multi-symbol capture

`live_feed()` takes an optional `sink=`; when passed, the caller owns it and `live_feed`
neither creates nor closes it. `capture.py` accepts a symbol list, opens one websocket per
symbol, and funnels all of them through **one** shared `CsvSink` under `asyncio.gather`.

This is the correct shape because the constraint was never "one symbol per archive" — the
CSVs already carry a `symbol` column and every reader filters on it. The constraint is
**one writer**. A single process on a single event loop serializes writes, so snapshots
can't interleave; two daemons would shred them (see weakness list above). Each symbol's
stream is independently wrapped so one dropped socket can't take the others down, and it
prints `stream ENDED` to stderr rather than dying quietly — a partial mitigation for
weakness #1, not a fix.

`tools/probe_multisub.py` measures, against the live server, whether one socket accepts
multiple subscribe frames (`MULTIPLEX`) or the second replaces the first (`SWITCH`). The
implementation assumes `SWITCH`, which is correct under either answer; `MULTIPLEX` would
just mean it could be done with one connection. Untested — needs a live token and market
hours.

### Dockable multi-symbol workstation

`app.py` (1,831 lines) split into three:

| module | role |
|---|---|
| `items.py` | colours, `DEFAULTS`/`SETTINGS_SPEC`, `SettingsDialog`, and the pyqtgraph drawing primitives — extracted verbatim, no logic change |
| `panels.py` | `Panel(QDockWidget)` base + 11 panel classes; the old `MainWindow._refresh_*` bodies moved onto the panel that owns them |
| `app.py` | window: model registry, link groups, feed threads, Panels menu, persistence |

**Model registry.** `self.model` became `models[(symbol, bar_kind, bar_size)]`, built lazily
and reference-counted against open panels. Events are stored per symbol, so several models
can be fed from one archive.

**Link groups.** Four channels (grey/red/blue/green). Members share symbol, bar basis,
crosshair and x-range; grey panels keep a private source. This is what makes two symbols on
screen coherent — without it, a second footprint has no way to say which profile or DOM
belongs to it.

**Multi-symbol live.** One `FeedThread` per symbol on screen, all sharing one `CsvSink`
(hence the lock above). Threads start and stop as panels open and close.

**New panels:** watchlist (click to retarget a group — fixes needing a restart to change
symbol), signal log (reads `FootprintItem._absorbs` / `_cells`, no new maths), depth curve.
**New interactions:** group-scoped crosshair with a per-cell readout, two-click measure, and
a session/visible switch on Vol@Price backed by `model.vap_for_bars()`.

Four bugs found while building it, each worth knowing:

1. **`isVisible()` is the wrong test for "is this panel open".** A dock tabbed *behind*
   another reports `isVisible() == False` but `isHidden() == False`. Gating the model
   registry and feed threads on visibility would have GC'd a backgrounded tab's model and
   killed its live feed — losing data for a symbol merely because its tab wasn't in front.
   Structural checks use `isHidden()`; only repainting uses `isVisible()`, with a
   `visibilityChanged` hook so a tab repaints the moment it comes forward.
2. **`QMenu.clear()` deletes submenus built inside the rebuild**, since nothing holds a
   reference — shiboken then raises "C++ object already deleted". The Add/Remove submenus
   are created once and parented to the window.
3. **`%`-formatting has no `,` grouping flag** (that is `format()`/f-strings only).
   `"%+,.0f"` raises `ValueError` at runtime, not import — it only surfaced when the measure
   tool actually drew.
4. **pyqtgraph `stepMode="right"` wants equal-length x/y**; only `"center"` takes the extra
   edge point.

Also note `MainWindow` now accepts an injectable `settings=` scope. It previously hardcoded
`QSettings("orderflow", "of_app")`, so tests wrote into the real config and a roster from
one run leaked into the next.

---

### No-terminal launch, and an enforced writer lock

`Orderflow Station.bat` (pythonw, so no console) opens the app; with no CLI flags it shows
`startup.StartDialog` instead of assuming replay/ASII. Any flag skips the dialog, so
`--shot` and scripting are untouched. `startup.py` also holds the guided token grab (a
`QFileSystemWatcher` on `DATA_DIR` confirms the frame landed and validates it via the
existing `parse_frame_file`/`frame_symbol`) and the command reference.

The substantive change is **who may write the archive**:

- `data/capture.lock` — the holder refreshes its **mtime** every ~2 s, and that heartbeat
  is the liveness test. Not a pid probe: on Windows `os.kill` with any signal other than
  `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` terminates the target, so a "is it alive?" check would
  kill the recorder it was asking about.
- `data/capture.stop` — a *request*. The writer notices, closes its `CsvSink` and removes
  the lock itself. Nothing is killed mid-write, so no snapshot is ever half-flushed.
- `capture.py` refuses to start (exit 3) if a fresh lock exists, and `MainWindow.start_live`
  drops itself to view-only when it finds one. **Weakness #1 of the one-writer rule — that
  it was documentation you had to remember — is now closed.** The `--view-only` flag still
  exists but is decided for you.
- The recorder is launched **detached** (`QProcess.startDetached`), not as a child, so
  closing the chart does not stop capture. The app re-attaches on next launch by reading
  the lock.

One subtlety worth keeping: lock ownership is tracked as `MainWindow._holds_lock`, not by
comparing pids. A pid comparison looked right and passed casually, but it let a window that
had never taken the lock release a lock written elsewhere in the same process — caught by
`test_session` case 4. Pids are also recycled.

Still absent: **reconnect**. A dropped socket ends that symbol's capture; the recorder logs
`stream ENDED` and keeps the others alive. The session chip and `data/capture.log` make it
visible rather than silent, which is a mitigation, not a fix.

---

### Making failures visible (`diagnostics.py`)

Shipping `pythonw.exe` as the default launcher created a class of bug rather than a bug:
`sys.stdout` and `sys.stderr` are **None** there, so every exception — startup, Qt slot, or
feed thread — vanished and the process died with no window. Every distinct problem presented
identically as *"nothing happens"*, which is undiagnosable by construction.

`diagnostics.install()` runs **before the PySide6 import** (so a failure in the Qt import is
caught too) and does three things:

- **Adopts streams.** If `sys.stdout`/`sys.stderr` are `None`, it points them at
  `data/launch.log`. This is the same trick that made the bug investigable in the first
  place — redirecting `pythonw`'s output replaces the `None` stream with a real handle.
  Doing it inside the process means it works however the app was launched, instead of
  depending on batch-file redirection.
- **`sys.excepthook` + `threading.excepthook`** → full traceback to `data/crash.log`, plus a
  `QMessageBox` when a `QApplication` already exists.
- **`faulthandler`** → the same log, for interpreter-level crashes that never reach Python.

Two guards worth keeping, both found by testing rather than reasoning:

- The crash dialog is **suppressed when `QT_QPA_PLATFORM=offscreen`**. `QMessageBox.exec()`
  is modal, so with nobody to click OK it blocks forever — a crash during `--shot` would
  have become a hang instead of an exit. The test suite hung on exactly this.
- The Shift-at-launch probe is wrapped in `try/except`. A keyboard query that throws during
  startup would produce the very no-window symptom this module exists to eliminate.

`FeedThread.run()` now catches `Exception`, records it, and emits the message on the
existing `status` signal — so a missing token reads *"no session token — click Token to grab
one"* in the status bar rather than killing the feed in silence. `--doctor` (and
`Diagnose.bat`) dumps interpreter, paths, token, recorder, Qt platform, screens, and whether
the saved geometry still lands on a connected screen.

A remembered startup that crashes also clears `startup/remember`, so a bad saved
configuration cannot lock you into an unbootable loop.

---

### Resilient recording: reconnect and the gap ledger

The archive is the product, and missed tape is unrecoverable (the server's backfill replays
only ~40 trades), so a dropped socket ending the day was the worst remaining defect.

`live_feed()` is now a retry loop around a `_session()` generator. Reconnect lives there
rather than in each caller because both consumers — the `capture.py` daemon and the GUI's
`FeedThread` — go through it, so neither needs its own logic.

Three details that are easy to get wrong:

- **`seen_ids` lives outside the retry loop.** The server replays recent trades on reconnect;
  that set is precisely what stops the tape doubling up. Recreating it per attempt would
  silently duplicate every reconnect. `test_reconnect` asserts the yielded ids are exactly
  `[1,2,3,4]` across two outages with deliberately overlapping backfills.
- **The backoff is jittered** (`uniform(0.5, 1.5)` on the delay). One socket per symbol means
  a single network blip drops them all simultaneously; un-jittered retries would then
  reconnect in lockstep, repeatedly.
- **The subscribe frame is re-read on every attempt.** A token grabbed mid-session therefore
  heals a dead connection with no restart — which matters because an expired token is the
  most likely real failure. It is also detected distinctly: `AuthExpired` is raised on the
  server's `not authorized` reply and recorded as cause `token`, not `disconnect`.

Gaps are a **new event kind** — `("gap", rec)` — flowing through the same stream as
book/trade/summary, persisted to `data/gaps.csv`, and merged back by `replay_feed()` so a
replayed day has the holes the live session had. Adding a kind was safe because
`OrderflowModel.on_event` has no `else` branch and `backtest.load_days` filters to trades.

`model.coverage()` measures the captured fraction **between the first and last trade**, not
against clock hours. That excludes overnight and weekends for free; a market calendar would
need IDX holidays and schedule changes kept correct forever to avoid reporting confident
nonsense. Gaps straddling the open are clipped to the session.

Consumers that were *inferring* holes now use the record: the CVD panel breaks its line on
recorded gaps (keeping the >300 s rule only as a fallback for older archives), and the new
`CaptureIntegrityPanel` lists every outage with a coverage summary.

**Behaviour change worth knowing:** `live_feed` no longer terminates when the socket drops.
Anything wanting the old one-shot semantics must pass `reconnect=False` — two existing tests
hung until they did.

---

## 10. Risk & compliance

Stated plainly by the project itself, and worth repeating:

- **Unofficial and reverse-engineered.** Not affiliated with, endorsed by, or supported by
  Stockbit. The protocol is private and undocumented; it can change or break without notice.
- **Your subscribe frame is a live credential** tied to your account. `.gitignore` covers
  `subscribe*.txt`, `grab_config.json`, `pw_profile/`, `data/`, `*.csv`, `*.jsonl` — keep it
  that way. Never paste one into an issue, a chat, or a log.
- **ToS compliance is on you.** The README says personal/educational use; whether automated
  access fits Stockbit's terms is a question for you and their terms, not something the code
  settles.
- **Not financial advice.** The regime filter in particular is explicitly unvalidated.
