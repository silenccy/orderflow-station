# Tests

```bash
pip install -e ".[dev]"
pytest                      # everything
pytest -k reconnect         # one suite
python tests/run_all.py     # same thing without pytest
```

## Layout

| | |
|---|---|
| `suites/` | the actual tests, each a standalone script |
| `test_suites.py` | pytest entry: one test per suite |
| `_runner.py` | launches a suite in its own process |
| `run_all.py` | the same, without pytest |

## Why each suite is a subprocess

The suites monkeypatch module globals — `feed._session`, `feed.websockets.connect`,
`app.FeedThread` — and most of them build a `QApplication`, which is a process-wide
singleton. Imported into one interpreter they would leak state into each other, and a
failure would then depend on collection order rather than on the code under test.

One process per suite is what makes a green run mean something. It also lets each suite
get a throwaway `ORDERFLOW_DATA`, so **a test can never read or write your real capture
archive**.

That is also why `python_files = ["test_suites.py"]` is set in `pyproject.toml`: without
it pytest would import everything under `suites/` directly and undo the isolation.

## What each suite covers

| Suite | Covers |
|---|---|
| `loader` | subscribe-frame selection: newest wins, junk skipped |
| `multisym` | shared-sink ownership; concurrent symbols don't shred snapshots |
| `daemon` | recorder lifecycle: cooperative stop, clean close, one-writer refusal |
| `reconnect` | self-healing feed, gap ledger, no duplicate tape after a reconnect |
| `gaps` | coverage maths, integrity panel, CVD line breaks |
| `workstation` | model registry and GC, link groups, layout round-trip |
| `panels_smoke` | every panel kind builds and refreshes |
| `session` | the app never becomes a second writer, never drops a lock it didn't take |
| `launch` | when the Start dialog appears, and that flags bypass it |
| `diagnostics` | failures stay visible with no console attached; the `[today]` report |
| `recording` | rows reach disk through the real feed, and the right process writes them |
| `feed_lifetime` | feed threads are joined before release; `stop()` is honoured at any moment |
| `paint_safety` | a paint error is contained and logged, never a native crash |
| `static_names` | no module uses a name that does not exist |
| `retention` | the replay buffer is bounded; native crashes land under a dated header |
| `watchlist` | watched vs charted symbols, tiered retention, add/remove |
| `heatmap` | clock-time axis, real tick bands, honest depth, walls |
| `big_prints` | *Big ≥* is per stock: automatic threshold, per-stock overrides |
| `aggressor` | the exchange's side tag wins; inference only when it is absent |
| `order_size` | average size per level, reload detection |
| `polish` | visual polish that must keep working: flashes, tape bars, axis labels |
| `layout_once` | startup arranges the docks at most once |
| `settings` | colour settings reach the pixels |
| `settings_complete` | every setting is reachable, defaulted and wired to something |
| `version` | one version source, valid semver, documented in the changelog |

## Writing another

Add `suites/yourname.py`. Exit non-zero on failure (a bare `assert` is fine) and print a
`PASS: ...` line per check — `run_all.py` counts those. It is picked up automatically;
nothing needs registering (files starting with `_` are helpers, not suites).

Make `import _safety  # noqa: F401` its first import. Run directly, a suite's data folder
would be your real `data/` — token and archive included — and `_safety` refuses to start
unless the runner has given it a throwaway one.
