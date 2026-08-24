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
| `diagnostics` | failures stay visible with no console attached |

## Writing another

Add `suites/yourname.py`. Exit non-zero on failure (a bare `assert` is fine) and print a
`PASS: ...` line per check — `run_all.py` counts those. It is picked up automatically;
nothing needs registering.
