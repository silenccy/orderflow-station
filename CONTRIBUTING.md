# Contributing

## Getting set up

```bash
python -m venv .venv
```
```bash
.venv\Scripts\python.exe -m pip install -e ".[dev]"
```
```bash
pytest
```

Eleven suites, about twelve seconds. If that is green you have a working checkout.

## Running the tests

```bash
pytest                      # everything
pytest -k reconnect         # one suite
python tests/run_all.py     # same, without pytest
```

Each suite runs in **its own process** against a throwaway `ORDERFLOW_DATA`. That is not
stylistic: the suites monkeypatch module globals (`feed._session`,
`feed.websockets.connect`, `app.FeedThread`) and most build a `QApplication`, which is a
process-wide singleton. In one interpreter they would leak into each other and failures
would depend on collection order. It also means **a test can never touch your real capture
archive**. See [tests/README.md](tests/README.md).

To add one, drop `tests/suites/yourname.py` in place. Exit non-zero on failure and print a
`PASS: ...` line per check. Nothing needs registering.

## Things that will bite you

**Units.** Order-book `value` is **shares**, so `lots = value / 100`. Getting this wrong
yields numbers that look plausible and are not. `docs/protocol.md` records an earlier
revision of the notes making exactly this mistake.

**Tick size comes from the book ladder, not from traded prices.** The book is dense and
exactly tick-spaced by the exchange; a sparse day or one off-tick negotiated print will
corrupt the price grid if you infer the tick from trades.

**Anything that writes the archive must take the lock.** `capture.take_lock()` and then
`capture.touch_lock()` at least every `STALE_AFTER_SEC`, or other writers will correctly
conclude you are dead and start writing alongside you. Two writers interleave rows
mid-snapshot and `replay_feed`'s `groupby` — which only groups *adjacent* rows — silently
shreds the book into fragments that still parse.

**Liveness is a heartbeat, never a pid probe.** On Windows `os.kill` with any signal other
than `CTRL_C_EVENT`/`CTRL_BREAK_EVENT` terminates the target, so "is it alive?" would kill
the recorder it was asking about.

**The model layer imports no Qt.** `feed → model` must stay usable headless; that is what
lets the backtest and the recorder run without a GUI. Keep rendering in `panels.py` and
`items.py`.

## Adding a panel

One class in `panels.py` with the `@register` decorator, one line in `app.py`'s
`PANEL_MENU`. `tests/suites/panels_smoke.py` will pick it up automatically and assert it
builds and refreshes.

## Pull requests

Keep the version in `orderflow/__init__.py` alone — bumping it is a release, not a PR. Add
your entry to `CHANGELOG.md` under `## [Unreleased]`. The release procedure lives in the
README.

## Screenshots

`python tools/make_previews.py` regenerates `docs/img/` headlessly from a **synthetic**
random-walk session. Never commit a screenshot of real market data: the window shows your
symbols, your positions in the tape, and your session state.
