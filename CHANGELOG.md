# Changelog

Notable changes to Orderflow Station. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**MAJOR** is bumped when an existing setup breaks: a removed CLI flag, a changed
on-disk format, or a public function that behaves differently. **MINOR** adds
capability without breaking anything. **PATCH** is fixes only.

## [Unreleased]

### Fixed

- **Pressing Center crashed the app.** Reported live on 2026-10-09, then reproduced live
  and offline from the recorded session. `FootprintItem.paint` read `sc` — the buy/sell
  colour set — which was only ever a local of a different method, `_generate`, since the
  configurable colours landed on 2026-09-06. It only ran once the footprint was zoomed in
  far enough to draw numbers in its cells, so nothing tripped it until a view got that
  close. Center did: a live footprint opened parked on pyqtgraph's empty-view default (a
  unit square near zero), Center preserved that 1-unit span, and the result was a 2-bar,
  ~700 px-per-bar close-up. The `NameError` escaped `paint()`, and PySide answered with an
  access violation in `QtCore.pyd` — no traceback, no dialog, the window gone. Present in
  every release from 3.0.0 to 3.2.0; bisected live across 3.0.1, 3.1.0 and 3.2.0.
- **That was also the 2026-09-08 crash.** Same faulting module, same offset (`0xfd5cb`),
  same paint frame. 3.0.1 attributed it to a thread being destroyed while running; that
  bug was real, but it was a diagnosis inferred from code and never reproduced. The 3.0.1
  entry now carries a correction.
- **A live chart is on screen by itself.** A footprint that has bars but has never been
  framed now frames itself — 12 to 40 bars by 24 ticks — instead of sitting on the unit
  square until Center is pressed. Center from that state gives the same readable frame
  rather than the 2-bar close-up. A view you have zoomed or panned is never reframed.
- **The Record button never said Stop.** From 3.1.0 to 3.2.0 its update line sat
  unreachable after the `return` in `_watch_chip`, carried along when that method was
  inserted into the session refresh. It updates every second again.
- **A bug in any custom painter can no longer take the window down.** Every
  `paint()` in `chart_items.py` — footprint, delta footer, heatmap image, candles, walls,
  depth bars — now runs under `safe_paint`: an exception is caught, recorded to
  `crash.log` once per item, the painter is restored, and the frame finishes. Proven on the
  recorded session first: the same `NameError`, caught with the painter restored, left the
  app running. The time axis and the heatmap legend, which run inside pyqtgraph's own axis
  paint, fall back to plain numbers the same way.

### Added

- **`tests/suites/static_names.py`** — every scope in `orderflow/` and `tools/` must only
  read names that exist. It is pyflakes' core check, built on the standard library's
  `symtable` so CI needs no new package, and it proves on a probe that it can see a
  borrowed local like the original bug. Run against 3.2.0 it found both bugs above in
  milliseconds; the crash had survived a month of tests that never zoomed in far enough.
- **`tests/suites/paint_safety.py`** — the cell-number pass at deep zoom, called raw
  without the safety net (verified by hand to raise the original `NameError` on 3.2.0's
  painter), a raising painter contained and logged once, self-framing, and the Record
  button.

## [3.2.0] - 2026-10-07

Minor: visual polish -- new capability, nothing removed, no setting renamed. An app
icon and a title that says what the window is doing, tick flash in the DOM and
tape, one clock-time language across the charts, and a tape in the same units as
everything else. Verified by the test suites and by rendering it; the flash only
shows on live data, and none of this has yet run against a live market.

### Added

- **An app icon, drawn in code.** A tiny order book — asks over bids, sized like depth bars,
  a gold price line through the touch — rendered at every size from 16 to 256 px, with no
  binary asset in the repo. The process also registers its own Windows taskbar identity;
  launched through `pythonw.exe`, Windows otherwise files the window under Python and
  shows Python's icon whatever the window says.
- **A title that says what the window is doing**: `Orderflow Station — BUMI +5 · Live ·
  ● Recording`, readable from the taskbar without bringing it forward.
- **Tick flash.** Live, a DOM level flashes green as it grows and red as it shrinks, the
  best bid/offer pulses when the touch moves, and new prints enter the tape tinted by side,
  all fading within a second — in both DOM layouts. Flashes are keyed to *what* changed,
  not to a row, so they follow a level or a print as the ladder shifts and the tape
  scrolls; a fade step touches only the fading cells (~0.01 ms against ~1 ms for a DOM
  refresh) and its timer stops itself. Replay never flashes. `tick_flash` (DOM & Tape)
  turns it off for a still screen.
- **A Start dialog that opens with the app's name, version and token state** as a coloured
  chip, instead of a bare "Start a session".

### Changed

- **One time language.** The footprint and regime x-axes read clock time (`14:00`,
  `14:05` …) like the heatmap and CVD, instead of bar numbers 0, 5, 10. Only the labels
  changed; x is still the bar index, so measuring, linking and the crosshair are untouched.
- **The tape speaks the same units as every other panel.** Quantity follows
  `header_units` (lots) instead of raw shares, with a size bar coloured by side and
  scaled to the 95th-percentile print on screen, and sides read `▲ buy` / `▼ sell`.
  Scaling to *Big ≥* instead was tried and rejected: on BUMI nearly every print clears
  50 lots, so every bar sat full; *Big ≥* keeps the highlight and a bolder bar.

### Fixed

- **Depth bars were painted under the cell background.** The bar painter drew its bar and
  then let Qt paint the cell — background included — on top. Invisible while no size cell
  had a background; a big-print highlight or a flash would have hidden the bar completely.
  It now paints background, bar, then text, and a pixel test pins the order.
- **The README misrepresented the regime panel.** `tools/make_previews.py` auto-ranged
  every plot, flattening the regime chart to its data and hiding the TREND/CHOP lines the
  app actually draws. Panels that pin their own range are now left alone.

## [3.1.0] - 2026-10-07

Minor: new capability, nothing removed, and settings from 3.0.x migrate on first launch.
Three pieces of work — a real watchlist, a memory footprint that stops growing, and a
liquidity heatmap rebuilt to be readable. All of it was verified against the recorded
2026-08-31 session and the test suites; none of it has yet run against a live market.

### Added

- **A real watchlist: the symbols you pick are the symbols you get.** The watched set is
  now explicit state, persisted with the roster, and it — not the layout — decides what
  the app subscribes to. `WatchlistPanel` gained `+` / `−` so you can watch a ticker
  without dedicating a panel to it, and the session toolbar shows `watching N · charting M`
  whenever those differ, because silence is what let six symbols become one.
- Watching is deliberately cheap. Measured against the 2026-08-31 archive, the live
  protobuf parse costs **20 µs/frame**, so one extra watched symbol is ~0.02 % of one core
  even at the opening bell.
- **`book_buffer`** (General): how many book snapshots to keep per symbol, default 4000.

### Changed

**The liquidity heatmap, rebuilt to be readable.** Every problem below was confirmed against
the real 2026-08-31 BUMI session, not the README preview, which is a synthetic random walk
and hid the worst of them.

- **It reads clock time.** The x-axis was the column index — 0, 200, 400. `TimeAxis` puts
  ticks on round clock times (1/2/5/10/15/30 min) and labels them `HH:MM`, `HH:MM:SS` when
  zoomed in. A dashed line marks any jump between columns — a disconnect, or the lunch
  break — which a column axis silently compressed into one step; jumps of 5 min or more
  carry their length.
- **No more stripes.** IDX changes tick size at 200 / 500 / 2,000 / 5,000, and the grid used
  one global minimum tick. On BUMI that made every odd row above 200 a price that cannot
  exist — **0.0 % filled** — so the field striped and smoothing blended each real level with
  a dead neighbour, roughly halving it. Each level now paints its own tick band, inferred
  from the ladder rather than hard-coded, and the y-axis stays in price so the heatmap
  still links to the footprint.
- **Unknown is not "empty".** Cells beyond the feed's visible depth used to paint as the
  colormap's floor — 29 % of the BUMI canvas, and the flat purple slab in the old preview.
  They are now transparent, so the coloured field's edge *is* the depth edge.
- **A legend in lots, and a readout.** A colour bar labelled in lots (inverting the
  equalize ranking, so yellow is a number), and hovering reads out the cell:
  `09:12:40 · 202 · bid 182,400 lots`, or *beyond the visible depth* / *nothing resting*.
- **Walls you can lean on.** The two dashed lines that traced the single largest bid and ask
  per column flipped level to level and zigzagged across the chart. A wall is now a level
  holding ≥ `wall_mult` × the column's median for 30 s — the DOM's own rule, so the word
  means the same in both panels — outlined across its life, with the standing ones labelled
  by size. The overlay is capped at 12: walls per window depend on the market far more than
  on any threshold (range-bound BUMI: 21, median life 31 min; the trending preview: 201,
  median life 46 s), so it shows every standing wall plus the strongest ended ones.
- **Hollow candles.** They were solid at 86 % opacity across 84 % of the bar, on top of the
  very price band the heatmap exists to show.
- **Pre-open no longer blank.** The heatmap's y-axis follows the group's footprint, which is
  empty before the first trade — so during IDX's pre-open auction, full book and no
  trades, the whole field sat off-screen. With no bars to anchor to, it frames the book.
- **Far cheaper to keep open.** Measured on the real session, the old panel cost
  **~77 ms on every refresh** — about half a core at the default 7 Hz — rebuilding its whole
  array even when nothing had moved, and rendering a fresh symbol for nearly every trade
  bubble because each had a continuous size. `HeatGrid` now updates incrementally (0.12 ms
  per column), the field is repainted only when a column or setting changes, trade
  timestamps are parsed once, and bubble sizes are whole pixels. An idle refresh is now
  **0.56 ms**; one with a new column ~15 ms.

**Memory that stops growing.**

- **The replay buffer is bounded, so a long day no longer grows without limit.** The cap
  is deliberately asymmetric, because the two event kinds are nothing alike. Measured on
  the 2026-08-31 archive, book snapshots are **92 % of the buffer at ~5.5 KB each**, while
  a whole session of trades is 8.7 MB — and nothing on screen reads an old book frame: the
  DOM shows only the newest and the heatmap keeps its own bounded window. The footprint,
  CVD, volume profile and tape, by contrast, need **every** trade of the session. So books
  are capped and trades are never dropped. Replaying the real 2.3 h BUMI session, the
  buffer falls from **82.2 MB to 24.8 MB** with every trade retained, and it stops growing
  rather than merely growing slower. Trimming is counted and shown in `--debug` as
  `books_trimmed=N`, because a trimmed buffer means a rebuilt heatmap starts later than
  the session did and that should never be a surprise.
- **Event retention is tiered, and this is what makes a long watchlist affordable.**
  `self.events` is the replay buffer models rebuild from, and before the cap above it grew
  without limit: measured with `tracemalloc`, a liquid symbol cost **~36 MB per
  symbol-hour**, ~233 MB *each* over a 6.5 h session. Only symbols a visible panel is
  drawing now earn a buffer at all; the rest get `_tally`, three counters, which is what
  the watchlist table reads anyway. Charting a watched symbol starts its buffer from that
  moment.

### Fixed

- **Picking six symbols streamed one.** Two defects stacked. `MainWindow.__init__` seeded
  the three link groups with `syms[min(i, len(syms) - 1)]` and dropped `syms[3:]` on the
  floor, so three of six vanished before anything else ran. Then `_restore_panels`
  reinstated a saved roster whose panels all pointed at ASII, losing the survivors too.
  The Start dialog's choice is now kept, and a restored watchlist is unioned with it
  rather than replaced.
- Settings from 3.0.x have no watchlist. One migration derives it from whatever the saved
  roster already pointed at, so an upgrade keeps watching exactly what it watched before.
- **A native crash left an undated blob in `crash.log`.** `faulthandler` writes its dump
  straight from C with no timestamp, so when the window died on 2026-09-08 the log could
  not say *when* — the crash had to be dated from the Windows event log instead.
  `install()` now stamps a dated, pid-tagged header the moment it arms, so even an access
  violation lands under a line that says which run it belongs to.
- **The Watchlist's `+` / `−` buttons rendered blank.** `DARK_QSS` pads every `QPushButton`
  14 px a side; at 26 px wide that left −4 px for a 12 px glyph. Found by looking at the
  regenerated preview, and now checked against the live stylesheet in the suite.

## [3.0.1] - 2026-10-03

### Fixed

- **The window died with no traceback when a symbol left the screen.** Hiding or
  retargeting the last panel for a symbol made `_sync_feeds` drop that feed with
  `self.feeds.pop(sym).stop()`. `stop()` only *requests* cancellation — it posts
  `task.cancel()` to the feed's event loop and returns while `run()` is still
  going — so the popped thread's last Python reference died with the statement
  and the C++ `QThread` destructor ran on a live thread. Windows answered with an
  access violation inside `QtCore.pyd`: no Python traceback, no crash dialog,
  just a vanished window. It now binds, stops and `wait(2000)`s, the same as the
  two other teardown paths already did. Regression test: `tests/suites/feed_lifetime.py`.

  > **Correction (3.2.1):** the thread bug above was real and stays fixed, but it was
  > **not** what crashed the window on 2026-09-08. That diagnosis was inferred from
  > reading the code, never reproduced. The same signature returned on 2026-10-09 with
  > no thread being retired; reproduced live and then offline, the cause was a
  > `NameError` in the footprint's cell-number pass. See 3.2.1.

## [3.0.0] - 2026-09-06

Major, because two things break an existing setup: `orderflow.items` no longer exists —
it was split into `theme`, `settings` and `chart_items`, so any import of it fails — and
aggressor side now comes from the exchange's own tag rather than inference, which changes
delta, CVD and imbalance numbers when you replay an archive captured before this.


### Changed

- **`items.py` is gone, split into three modules that say what they hold.** It was 847
  lines doing three unrelated jobs under a name that only means something if you already
  know pyqtgraph — "items" is its word for `QGraphicsObject`. There was even a footprint
  helper wedged between the colour constants and the settings table.

  | new module | answers |
  |---|---|
  | `theme.py` | what colour is a buy, and how are Qt widgets styled |
  | `settings.py` | what can the user change, and the dialog that edits it |
  | `chart_items.py` | how the charts are actually painted |

  Code moved verbatim; no behaviour change. `orderflow.__all__`, the README tree, the
  architecture doc and CONTRIBUTING all describe the new shape.

### Fixed

- **`vap_mode` was a control that did nothing.** It sat in the dialog labelled
  "Vol@price range (new panels)" while no code read it, so choosing `visible` changed
  nothing. New profile panels now honour it; a restored panel still keeps whatever mode
  it was left in.

### Added

- **Help text on 47 of 55 settings** (was 25). The 8 without it are `show_*` toggles
  whose labels already say what they do.
- `tests/suites/settings_complete.py` makes the settings surface self-policing: no
  duplicate rows, nothing in `DEFAULTS` that the dialog cannot reach, nothing in the
  dialog missing a default (which would raise `KeyError` the moment it opened), no
  setting that nothing reads, no `cfg[...]` read without a default, every numeric
  default inside its own declared range, and every choice default actually one of the
  choices. That last pair would otherwise fail only when a user happened to open the
  page.

### Added

- **Order-size analytics from the book's order count.** Every level carries `freq`, the
  number of resting orders, which the terminal previously used only to fill two columns
  in the classic DOM. The pro DOM now has an **Avg** column showing `value / freq` — the
  average size of one resting order — highlighted when a level is held by unusually few,
  large orders. On the 2026-08-31 capture that ranged 7 → 82 lots across ASII levels and
  up to 49,354 on BUMI: a level averaging 49,354 lots and one averaging 356 are utterly
  different things the terminal used to render identically.
- **Replenishment (hidden-liquidity) detection.** Levels topped back up after trades eat
  into them are marked `↻`, with the count, lots put back and rate in the tooltip, and a
  count in the Σ footer. The test is not "the level got bigger" — book snapshots arrive
  about once a second, so a level hit and refilled within that second shows no net change
  at all. It compares against `old_value - consumed_by_trades`, which catches exactly that
  case. The order count then separates one order reloading from a crowd arriving.

  Reported as a **rate**, not just a session total: on a stock trading in four ticks a
  cumulative count is nearly tautological, since every traded price gets refilled
  eventually.

  **Heuristic, not proof** — and labelled as such in the UI. The feed is level-aggregated
  with no order ids, so one order reloading is indistinguishable from one leaving while a
  similar one arrives.

### Added

- **Buy/sell colours are configurable** (Settings ▸ Footprint). Two pickers; the
  imbalance fills and edge markers are *derived* from them, so the palette cannot end
  up incoherent. Applies to the footprint clusters, candles and headers, and to the
  volume profile. The DOM, tape, delta footer and watchlist keep their fixed green/red,
  so a palette far from green/red will show a seam between them.
- **The settings dialog was rebuilt**: sidebar navigation instead of tabs, a search box
  (worth having at 50 settings), inline help on the 20 least obvious ones, live colour
  swatches, and Reset page. `SETTINGS_SPEC` rows now take an optional 5th element for
  that help text.

### Changed

- **Aggressor side now comes from the exchange, not from inference.** Trade field 5
  carries it directly: `1` buyer-initiated, `2` seller-initiated, absent for the
  closing auction and negotiated blocks. `docs/protocol.md` previously described the
  field as unresolved and unusable; measured over 8,138 BUMI prints it agrees with
  the quote rule 94.7 % / 91.6 % of the time, and where they differ the exchange's
  tag is the better source — the quote rule compares against a book whose two sides
  arrive in separate frames and can be momentarily stale.

  `model.aggressor()` prefers the tag and falls back to the existing
  `model.classify()`; `diag()` gains `cls_flag` alongside `cls_quote`/`cls_tick`/
  `cls_carry`, so `--debug` still shows how much of your delta is fact rather than
  inference. Replaying the 2026-08-31 session: ASII 100 % from the tag, BUMI 91.4 %,
  and the carry rule — pure guesswork — fired **not once**.

  **This changes numbers on archives you have already captured.** Delta, CVD and
  footprint imbalance for a previously recorded day will shift slightly when
  replayed, because those trades are no longer inferred. The new values are the more
  accurate ones, but an old screenshot will not match a fresh replay.

### Fixed

- The volume profile hardcoded its bar colours as raw RGB instead of using `BULL`/`BEAR`,
  so it would have ignored any change to them. It now follows the configured palette.
- `DARK_QSS` styled `QSpinBox`, which does not match `QDoubleSpinBox` — half the numeric
  fields in the settings dialog were left unstyled. Now `QAbstractSpinBox`, and
  `QPushButton` is styled too rather than staying light against the dark dialog.
- Preview images shifted very slightly: the imbalance shades are now derived from the
  buy/sell colours rather than being separate hand-tuned constants.

- **The app aborted on startup and never opened a window.** Startup ran the dock
  arrangement *three* times -- once from `_default_panels`, once when `restoreState`
  failed, and once from the all-hidden safety net. Rearranging docks that Qt has
  already placed corrupts its dock layout, and on a real (non-offscreen) platform
  roughly nine launches in ten died with `Fatal Python error: Aborted` or a
  segfault inside whichever call did the moving. Startup now arranges once.

  Every test suite passed throughout, because they all run under the offscreen Qt
  platform, which never reproduces it. `tests/suites/layout_once.py` asserts the
  invariant instead, and also guards the restore path: an intermediate fix created
  the panels undocked, which silently made `restoreState` a no-op and left every
  panel floating as its own top-level window.

### Added

- README badges (CI, latest tag, Python, licence) and a Mermaid data-flow diagram of
  `feed → model → panels`, rendered natively by GitHub.
- Issue and pull-request templates, `CONTRIBUTING.md` and `SECURITY.md`. The security
  policy leads with the real hazard: `subscribe_*.txt` is a live account credential, not
  a config file.

### Fixed

- VWAP and VAH price labels shared an x position and overprinted into an unreadable smear
  whenever VWAP sat inside the value area — which is most of the time.
- The order book's last column was clipped on a fresh install. `Interactive` header mode
  keeps every section at its ~100px default and `setStretchLastSection` only *grows* the
  last one into spare room, so a narrow dock cut off `Vol`. Columns now share the viewport
  until you drag one, after which your widths win.


## [2.0.0] - 2026-08-21

Major, because three things break an existing setup: the `--grab` flag and the
`[token]` extra are gone with the Playwright grabber, `orderflow.token` no longer
exists, and `live_feed()` no longer returns when the socket drops -- it now retries
forever unless you pass `reconnect=False`.

### Added

- **Dockable workstation.** Every widget is a `QDockWidget` that snaps, tabs, floats and
  persists. Link groups A/B/C decide which panels move together, so several symbols and
  bar bases can sit side by side. New panels: depth curve, watchlist, signal log, capture
  integrity.
- **GUI-first launch.** A Start dialog (mode, symbols, bar basis, recording) replaces the
  command line, with a guided session-token grab that watches `data/` and validates the
  frame, and an in-app command reference. `Orderflow Station.bat` launches under
  `pythonw` with no console; `Diagnose.bat` runs `--doctor`.
- **Reconnect and a gap ledger.** The feed heals itself with jittered exponential backoff
  and records every hole to `data/gaps.csv` as a `("gap", rec)` event. `replay_feed`
  merges them back, so a replayed day has the same holes the live session did.
  `model.coverage()` reports how much of the session actually survived.
- **Multi-symbol recording.** One process, one websocket per symbol, one shared `CsvSink`.
- **Test suite.** 10 suites, 61 checks, each in its own process against a throwaway
  archive. `pytest` or `python tests/run_all.py`. CI on Windows for Python 3.10 and 3.12.
- `--doctor`, `--ask`, `--version`, `orderflow-capture --status` / `--stop`,
  `tools/probe_multisub.py`, `tools/make_previews.py`.

### Changed

- **The one-writer rule is enforced, not documented.** `data/capture.lock` is held by
  whoever is writing and heartbeats its mtime; a second recorder refuses to start and a
  chart that finds the lock taken goes view-only by itself. Liveness is the heartbeat
  rather than a pid probe, because on Windows `os.kill` with any ordinary signal
  terminates the target — a "is it alive?" check would have killed the recorder it was
  asking about. Shutdown is a cooperative `data/capture.stop` flag, so the sink always
  closes cleanly and nothing is killed mid-write.
- **Failures are visible.** `diagnostics.py` installs `excepthook`, `threading.excepthook`
  and `faulthandler` before the Qt import, and adopts real streams when `stdout`/`stderr`
  are `None` under `pythonw`. Without this any failure produced no window and no message,
  which collapsed every distinct bug into "nothing happens". Feed errors now reach the
  status bar — a missing token reads as such instead of killing the feed silently.
- **Subscribe-frame selection prefers the newest parseable frame**, not the largest.
  Yesterday's expired 922-byte frame and today's fresh 922-byte frame tie on size, so the
  old rule would have kept the dead one indefinitely.
- Dark palette applied to the tables and title bars, which were rendering light against
  the dark charts.
- The version is single-sourced from `orderflow.__version__`; `pyproject.toml` derives it
  instead of keeping a second copy that drifts.
- CVD line-breaks use recorded gaps, keeping the >5-minute heuristic only as a fallback
  for archives captured before gaps were logged.

### Removed

- **The Playwright session-token grabber.** Google blocks OAuth sign-in in
  automation-controlled browsers, so the one-time headed login could never complete for a
  Google SSO account. The browser-console grab is now the only path, and it is documented
  as the main route rather than a fallback. `--grab` and the `[token]` extra are gone.

### Fixed

- Headless `--shot` renders produced tofu boxes instead of text: the offscreen Qt plugin
  ships no font backend on Windows and loads zero families without `QT_QPA_FONTDIR`.
- A chart could release a writer lock it never took, because ownership was inferred from a
  pid comparison rather than tracked.
- The crash dialog is suppressed under `offscreen`; a modal `exec()` with nobody to click
  OK turned a crash during `--shot` into a hang.

### Known limitations

- The TREND/CHOP regime filter is **unvalidated on IDX data**. Over six captured days and
  201 evaluations only `CHOP` did measurable work. Treat it as a stay-out filter.
- Session tokens last ~24 h and must be grabbed by hand from a browser console.

## [1.0.0] - 2026-08-06

Initial public release: footprint charts, liquidity heatmap, volume profile, CVD,
DOM ladder and trade tape over the Stockbit Pro market-data websocket, plus the
walk-forward regime backtest.

[Unreleased]: https://github.com/silenccy/orderflow-station/compare/v3.2.0...HEAD
[3.2.0]: https://github.com/silenccy/orderflow-station/compare/v3.1.0...v3.2.0
[3.1.0]: https://github.com/silenccy/orderflow-station/compare/v3.0.1...v3.1.0
[3.0.1]: https://github.com/silenccy/orderflow-station/compare/v3.0.0...v3.0.1
[3.0.0]: https://github.com/silenccy/orderflow-station/compare/v2.0.0...v3.0.0
[2.0.0]: https://github.com/silenccy/orderflow-station/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/silenccy/orderflow-station/releases/tag/v1.0.0
