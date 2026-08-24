# Changelog

Notable changes to Orderflow Station. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

**MAJOR** is bumped when an existing setup breaks: a removed CLI flag, a changed
on-disk format, or a public function that behaves differently. **MINOR** adds
capability without breaking anything. **PATCH** is fixes only.

## [Unreleased]

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

[Unreleased]: https://github.com/silenccy/orderflow-station/compare/v2.0.0...HEAD
[2.0.0]: https://github.com/silenccy/orderflow-station/compare/v1.0.0...v2.0.0
[1.0.0]: https://github.com/silenccy/orderflow-station/releases/tag/v1.0.0
