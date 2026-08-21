# Orderflow Station

An **orderflow trading terminal for Indonesian equities (IDX)** — footprint charts,
a Bookmap-style liquidity heatmap, a DOM ladder and trade tape, built on live
order-book and tick data from the Stockbit Pro market-data websocket.

![Orderflow Station](docs/img/preview.png)

> ### ⚠️ Read this first
> - **Unofficial and reverse-engineered.** This project is **not affiliated with,
>   endorsed by, or supported by Stockbit.** It speaks an undocumented private
>   websocket that can change or break at any time.
> - **For personal and educational use.** You are responsible for your own
>   compliance with Stockbit's Terms of Service and any applicable regulations.
> - **Never commit your session frame.** `subscribe_*.txt` embeds a live JWT tied to
>   your account. It is gitignored — keep it that way. Never paste one into an issue,
>   a chat, or a log.
> - **Not financial advice.** No warranty; see [LICENSE](LICENSE).

---

## What it does

| Panel | |
|---|---|
| **Footprint** | Per-bar, per-price bid×ask volume clusters with diagonal imbalance highlighting, gold POC, V/D/R-H/R-L headers, mid OHLC candles, and **absorption markers** (heavy volume rejected at a bar extreme). Switchable to plain candlesticks. |
| **Liquidity heatmap** | Resting book depth over time, rank-equalized so real walls stand out; overlaid price line, OHLC candles, trade bubbles, and dashed lines tracking the **largest resting bid/ask** so you can see your distance to the big orders. |
| **Volume profile** | Session volume-at-price (butterfly), tick-binned, with POC and value area. |
| **CVD** | Cumulative volume delta in lots, with honest line breaks across capture gaps. |
| **DOM + tape** | Quantower-style centered ladder: depth bars, BBO highlight, per-level session volume, a **Chg** column showing size being stacked or pulled, and pinned Σ totals with book imbalance. |
| **Regime filter** | Efficiency Ratio + realized-vol + variance ratio → a TREND / CHOP / MIXED label with a live history panel. See the honest caveat below. |
| **Capture integrity** | Every hole in the tape — when the feed dropped, for how long, and why — with the share of the session actually captured. The feed reconnects by itself; this is what it missed. |
| **Watchlist** | Every symbol in your archive with last, change %, lot and trade count. Click to point a link group at it; double-click to open a new footprint for it. |
| **Signal log** | Running list of the absorption and diagonal-imbalance events the footprint already detects, with time and price. Click a row to jump the chart there. |
| **Depth curve** | Cumulative resting size either side of the touch — where liquidity thins out. |

Every panel is an independent dock: drag it anywhere, snap it, split it, tab it, float it
onto a second monitor, or close it. Open more than one of the same kind on different symbols
or bar bases. The arrangement is saved on exit and restored on launch.

Everything else is tunable from a tabbed **⚙ Settings** panel and persists across restarts.

## Install

```bash
git clone <your-repo-url>
cd orderflow-station
pip install -e .
```

Python ≥ 3.10. Deps: PySide6, pyqtgraph, numpy, websockets.

### Then just run it

Double-click **`Orderflow Station.bat`** (Windows). No terminal, no console window.

A **Start** dialog asks what you want — replay or live, which tickers, bar basis,
whether to record — and remembers it if you tick the box. Everything the flag tables
at the bottom describe has a button somewhere in the window; the flags are there for
scripting, not for you.

Hold **Shift** while launching to get the dialog back after you have ticked "remember".

### If it will not start

Double-click **`Diagnose.bat`**. It prints where the app is looking, what Qt platform it
found, your token and recorder state, whether the saved window position still lands on a
connected screen, and the tail of both logs.

Two logs are written to `data/`:

| | |
|---|---|
| `launch.log` | everything the app printed. Under `pythonw.exe` there is no console, so the app **redirects its own streams here** — otherwise a failed launch produces no window and no message, which is exactly the trap this replaced. |
| `crash.log` | full tracebacks, including ones from feed threads and interpreter-level faults |

A crash after the window exists also raises an error dialog. If the app starts but a symbol
never ticks, the status bar names the reason — a missing token now reads *"no session token
— click Token to grab one"* instead of failing silently.

## Get a session token

The websocket does **not** authenticate on the handshake — auth rides inside a
**subscribe frame** that embeds a ~24 h JWT. You need one such frame in `data/`
before anything can connect. One frame works for **any** 4-letter IDX ticker (the
symbol field is swapped in software), so you only re-grab when the *token* expires,
never when you change symbol.

**In the app:** click **Token** in the toolbar. It walks you through the steps, hands
you the snippet with a Copy button, and watches `data/` so it can confirm the frame
landed and tell you its size and symbol. The toolbar then shows the token's age.

The manual route below is the same thing, done yourself.

### Grab it from your browser console (~2 minutes)

You grab the frame by hand, from your own logged-in browser. There is no automated
grabber: headless-browser login is blocked by Google SSO, and a scripted browser holding
a live brokerage session is a liability you don't need for a 2-minute job.

1. Open **[stockbit.com](https://stockbit.com)** and log in.
2. Press **F12** → **Console** tab. If pasting is blocked, type `allow pasting` and Enter.
3. Paste this catcher and press Enter — it hooks outgoing websocket frames and downloads
   the subscribe frame in exactly the format this project reads:

   ```js
   (() => {
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
     console.log('catcher armed — now open a ticker chart');
   })();
   ```

4. **Open a ticker you haven't loaded yet this session** (e.g. ASII). The console logs
   `captured 922B subscribe frame for ASII` and a `subscribe_922.txt` lands in your
   Downloads folder. Allow the download if the browser asks.
5. Move that file into the project's **`data/`** directory:

   ```bash
   mv ~/Downloads/subscribe_922.txt "data/"
   ```

   ```powershell
   move "$env:USERPROFILE\Downloads\subscribe_922.txt" ".\data\"
   ```

**Notes**
- The catcher only downloads a frame **larger** than the previous one, so within one
  session you may get a couple of files — the last (largest) is the real one. That variant
  carries all four feed channels (book *and* trades); smaller ones carry fewer.
- The loader picks the **newest** parseable `subscribe_*.txt` in `data/`, so tomorrow's
  grab wins over today's without you deleting anything. Old frames are harmless clutter.
- The file is comma-separated decimal bytes, e.g. `10,7,49,54,...`. Nothing else parses.
- Reload the page to disarm the catcher.

### Token expiry

Tokens last ~24 h and also die if you log in elsewhere (session rotation). The toolbar
shows the age and turns amber past 20 h, red past 24 h — click **Token** and grab a fresh
one. **Symptom if you miss it:** the socket connects but no data arrives (with `--debug`,
an explicit `you are not authorized` status).

> `data/` is gitignored precisely because these frames identify your account. Never commit
> or paste one.

## Usage

### Try it without a token

Pick **Replay** in the Start dialog. If you have captured CSVs in `data/`, the whole
workstation runs offline — no connection, no token. Fastest way to explore the interface.

### A trading session, start to finish

1. **Before the open** — launch, pick **Live**, tick your symbols and
   **Also record to disk**, press Start.
2. **During** — chart freely. Add, move and tab panels; change symbols and bar bases.
   Close and reopen the window whenever you like: **recording keeps running**, because
   the recorder is a detached process, not a child of the chart.
3. **After the close** — press **Stop**, then fold the day into the regime evaluation:
   `orderflow-backtest --symbol ASII`.

The toolbar's **Record** button and the status chip beside it always tell you who is
writing: *this window*, a separate recorder, or nobody.

> **You can no longer corrupt the archive by accident.** Exactly one process may write
> the CSVs, and that is now enforced by `data/capture.lock` rather than asked of you:
> a second recorder refuses to start, and a chart that finds someone already recording
> quietly goes view-only and says so. The old `--view-only` flag still exists, but you
> no longer have to remember it.
>
> Stopping is cooperative — the app creates `data/capture.stop`, and the recorder closes
> its CsvSink properly and removes the lock. Nothing is ever killed mid-write.

### Several symbols at once

Tick as many as you like in the Start dialog, or add them later from the toolbar or the
**Watchlist** panel. One recorder handles all of them.

You do **not** need a second session frame. One captured frame subscribes any 4-letter
IDX ticker — `feed.make_subscribe_for()` swaps the symbol bytes — so a frame grabbed on
ASII drives every symbol you list. Re-grab only when the *token* expires.

One process opens one websocket per symbol and funnels them all through a single shared
`CsvSink`. Everything downstream already understands a mixed archive: `book.csv` and
`trades.csv` carry a `symbol` column, and `replay_feed`, the chart and the backtest all
filter on it.

The first three symbols seed link groups A, B and C. Put each footprint (and the panels that follow it) on a different coloured link group;
see [Link groups](#link-groups). You can also switch a group's symbol at any time from the
toolbar, a panel's title bar, or the **Watchlist** panel — no restart.

Adding symbols costs bandwidth and one connection each, so start with the few you
actually watch.

> **Only one process may write the CSVs** — one *process*, not one symbol. A single
> recorder with five symbols is fine; two recorders with one symbol each is not. Two
> writers interleave rows mid-snapshot, and because `replay_feed` regroups book levels
> with `itertools.groupby` (which only groups *adjacent* rows), the archive is left full
> of shredded snapshots that still parse. You wouldn't notice until you replayed it.
>
> `data/capture.lock` enforces this now, so it is not something you have to get right.
>
> If you want separate archives per symbol anyway, point `ORDERFLOW_DATA` somewhere
> different for each recorder — separate directories, separate locks, no conflict.
>
> **Dropped sockets heal themselves.** The feed reconnects with an exponential backoff
> (0.5 s up to 30 s, jittered so several symbols don't retry in lockstep), and the server's
> replayed backfill is deduplicated by trade id, so the tape never doubles up.
>
> **Every hole is recorded, not inferred.** Each outage is written to `data/gaps.csv` with
> its start, duration and cause, and shown in the **Capture integrity** panel along with the
> percentage of the session actually captured. An expired token is reported as its own cause
> rather than looking like a quiet market. What was missed is still gone — the backfill only
> replays ~40 trades — but you can now see exactly what you lost instead of guessing.

Installed console scripts `orderflow-app`, `orderflow-capture`, `orderflow-backtest` are
equivalent to the `python -m orderflow.*` forms.

### Command reference (optional)

You do not need any of this — it is all reachable from the window, and the same table is
in **Help ▸ ?** in the toolbar. The flags exist for scripting and headless rendering.

**`orderflow.app`** — the terminal

| flag | |
|---|---|
| `--replay` / `--live` | chart captured CSVs (default) or connect to the feed |
| `--symbol ASII BBCA` | one or more 4-letter tickers; the first three seed link groups A, B and C |
| `--view-only` | live mode without writing CSVs — applied automatically when something else holds the writer lock |
| `--debug` | diagnostics status bar (flow, book health, feed age, integrity check) |
| `--bars time\|tick\|volume`, `--size N` | bar basis and size |
| `--history today\|all\|none` | how much captured history to preload in live mode |
| `--shot out.png [--secs N]` | render once to PNG and exit (headless) |
| `--doctor` | print environment, paths, token, recorder and screen diagnostics, then exit |
| `--ask` | show the Start dialog even if you ticked "remember" (Shift at launch does the same) |
| `--reset-layout` | forget the saved geometry, dock layout and panel roster |
| `--reset-settings` | forget all saved Settings-panel values (recover from a bad config) |

**`orderflow.capture`** — the recorder

| flag | |
|---|---|
| `orderflow-capture ASII BBCA` | record several tickers concurrently into one archive |
| `--status` | is anything recording? |
| `--stop` | ask the current writer to shut down cleanly |

Refuses to start (exit 3) if another writer holds `data/capture.lock`.

**`orderflow.backtest`** — regime evaluation

| flag | |
|---|---|
| `--symbol ASII` | which symbol's captured days to evaluate |
| `--window 20 --warmup 20` | ER lookback (bars) and warm-up gate (minutes) |
| `--er-trend 0.5 --er-chop 0.3` | label thresholds |
| `--horizons 5,10,20` | forward horizons in bars |
| `--fees 0.15,0.25` | %/side buy,sell (IDX retail defaults) |
| `--csv out.csv` | dump per-signal rows |

### Arranging the workstation

**▦ Panels** lists every open panel with a checkbox — untick to hide, tick to bring back.
Below that, **Add panel** opens another instance of any widget (it appears floating, so you
can drop it where you want) and **Remove panel** deletes one for good. **Reset to default
layout** puts everything back.

Drag a panel by its title bar to move it. Qt handles the snapping: drop it against an edge
to dock, between two panels to split, or *onto* another panel to tab them together. Drag the
seam between panels to resize. The ⧉ button floats a panel — useful for a second monitor.

### Link groups

The coloured dot on each panel's title bar is its **link group**. Panels sharing a colour
share a symbol, a bar basis, a crosshair and an x-axis — so a footprint, its delta footer,
its profile and its DOM all move together. Click the dot to cycle
**grey → red → blue → green**; grey means unlinked, and an unlinked panel keeps its own
private symbol and bar basis.

That is what makes several symbols work at once: put one footprint on red/ASII and another
on blue/BBCA, and each pulls its own panels along. The toolbar's **Group** selector chooses
which group the symbol and bar controls drive.

### In the chart

| | |
|---|---|
| **▦ Panels** | show/hide, add, remove panels; reset the layout |
| **⚙ Settings** | tabbed panel — footprint, heatmap, DOM & tape, layout, general. Everything persists. |
| **⌖ Center / Home** | jump to the latest bars, keeping your zoom |
| **Follow** | auto-scroll to new bars; panning back into history switches it off |
| **↔ Measure** | click two points on a footprint for ticks, %, elapsed time and the volume traded between them |
| **Δ cells** | switch cluster cells to delta heat |
| **Big≥ (lots)** | highlight threshold for large prints in the tape |

Moving the cursor over a chart puts a **crosshair** on every panel in the same link group,
and the footprint's title bar reads out the price plus that cell's bid, ask, delta and total.

**Vol@Price** has a *session / visible* switch in its title bar. On **visible** it rebuilds
the profile from only the bars currently in view on its linked footprint, so POC and the
value area follow your zoom instead of describing the whole day.

Chart style (clusters vs candlesticks), imbalance and absorption thresholds, heatmap
palette and contrast, DOM depth and columns all live in **⚙ Settings**.

## Architecture

```
orderflow/
  paths.py     one place for every file location ($ORDERFLOW_DATA overrides the archive)
  feed.py      websocket protocol, frame parsing, CSV persistence, live + replay feeds
  model.py     pure aggregation, NO Qt — footprint, CVD, volume profile, book, heatmap,
               trade classification, regime filter
  items.py     colours, tunable settings + the pyqtgraph drawing primitives
  panels.py    every widget as a dockable Panel (footprint, heatmap, DOM, watchlist, ...)
  app.py       PySide6/pyqtgraph window: model registry, link groups, live feed threads
  startup.py   the dialogs that replace the CLI (Start, Get token, command reference)
  diagnostics.py  crash/launch logging and --doctor; installed BEFORE the Qt import
  backtest.py  walk-forward regime evaluation, no GUI
  capture.py   the recorder, and the writer lock every writer shares
Orderflow Station.bat   double-click launcher (pythonw = no console window)
Diagnose.bat            double-click when it will not start (prints --doctor)
tools/         standalone helpers (protobuf frame decoder, multi-subscribe probe)
docs/          protocol reference + images
data/          captured CSVs, gaps.csv, session frames, capture.lock/.stop/.log
               — gitignored
```

**Who may write the archive** is decided by `data/capture.lock`. Whoever holds it refreshes
its *mtime* every couple of seconds, and that heartbeat is the liveness test — deliberately
not a pid probe, because on Windows `os.kill` with any signal other than
`CTRL_C_EVENT`/`CTRL_BREAK_EVENT` terminates the target, so asking "is the recorder alive?"
would kill it. Shutdown is equally indirect: `data/capture.stop` is a request, and the
writer closes its `CsvSink` and removes the lock itself. Nothing is killed mid-write.

The recorder is started **detached**, not as a child process, so closing the chart does not
stop your capture — which is the entire point of having a recorder.

The window keeps a registry of models keyed by `(symbol, bar_kind, bar_size)`, built lazily
and dropped when no open panel is bound to them, plus one live feed thread per symbol
actually on screen. That is why a second footprint on another symbol costs you a connection
and a model, and nothing else.

The **model layer has no Qt dependency**: `feed → model` is fully usable headless, which
is how the backtest and daemon work. Both live and replay emit the same
`("book" | "trade" | "summary", …)` event stream, so the GUI can't tell them apart.

Captured data lands in `data/` as `book.csv`, `trades.csv`, `summary.csv` and a raw
`capture_raw.jsonl`. Point `ORDERFLOW_DATA` elsewhere to keep the archive off the repo drive.

## The regime filter — an honest note

The TREND/CHOP label is Kaufman Efficiency Ratio + a realized-volatility percentile,
with a Lo–MacKinlay variance ratio alongside. Method choice was driven by a literature
survey favouring simple, intraday-validated indicators over Hurst/HMM/BOCPD.

**It has not been validated on IDX data.** On six captured ASII days (201 evaluations)
the only label doing real work was **CHOP** — it reliably preceded low forward efficiency
and 1–2 tick moves. `TREND↑` fired twice in six days: far too few to judge. Treat the
chip as a *stay-out filter*, not a trend caller, and don't loosen the thresholds to make
it fire more — that's fitting noise. `backtest.py --sweep` deliberately refuses to tune
thresholds on fewer than 8 captured days.

## Development

- `python -m py_compile orderflow/*.py` — quick syntax gate.
- The GUI is three modules: `items.py` (colours, settings spec, pyqtgraph drawing
  primitives), `panels.py` (every widget as a `QDockWidget` subclass) and `app.py`
  (window, model registry, link groups, feed threads). Adding a widget means one class in
  `panels.py` with a `@register` decorator and one line in `app.py`'s `PANEL_MENU`.
- The GUI renders headless for verification: `python -m orderflow.app --replay --shot out.png`.
- `tools/decode_frame.py` dumps an unknown protobuf frame's field structure — the tool
  used to decode the trade format.
- `tools/probe_multisub.py ASII BBCA` answers, against the live server, whether one
  socket accepts subscriptions for several symbols (`MULTIPLEX`) or the second subscribe
  replaces the first (`SWITCH`). `capture.py` assumes `SWITCH` and opens one socket per
  symbol, which is correct either way; a `MULTIPLEX` result just means it could be cheaper.
  Needs a valid token and market hours.
- `startup.py` holds the Start/token/help dialogs. `StartDialog.values()` returns exactly
  the keys `main()` reads, so the dialog and the flags build an identical window — if you
  add a flag, add it to both.
- The writer lock lives in `capture.py` (`writer_status` / `take_lock` / `touch_lock` /
  `request_stop`). Any new component that writes the archive must take the lock and
  heartbeat it, or other writers will correctly conclude it is dead.
- Frame formats, units and gotchas: **[docs/protocol.md](docs/protocol.md)**.

Two units traps worth knowing before touching aggregation code: order-book `value` is
**shares** (`lots = value / 100`), and instrument tick size is derived from the **book
ladder**, not from traded prices — a sparse day or an off-tick negotiated print
otherwise corrupts the price grid.

## License

MIT — see [LICENSE](LICENSE).
