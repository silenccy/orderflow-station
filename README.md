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

Everything is tunable from a tabbed **⚙ Settings** panel and persists across restarts.

## Install

```bash
git clone <your-repo-url>
cd orderflow-station
pip install -e .
```

Python ≥ 3.10. Core deps: PySide6, pyqtgraph, numpy, websockets.
For the automatic token grabber (optional):

```bash
pip install -e ".[token]"
playwright install chromium
```

## Get a session token

The websocket authenticates from a **subscribe frame** containing a ~24 h JWT. Two ways
to obtain one:

**Automatic (recommended)**
```bash
python -m orderflow.token --login     # one time: log in + open any ticker, press Enter
python -m orderflow.token             # thereafter: headless, ~10 s, writes data/subscribe_auto.txt
```

**Manual** — open Stockbit Pro, `F12` → Console (type `allow pasting` if blocked), paste a
`WebSocket.send` interceptor, open a ticker, and save the largest binary frame as
`data/subscribe_NNN.txt` (comma-separated decimal bytes).

One frame works for **any** 4-letter IDX ticker — the symbol field is swapped in
software ([protocol notes](docs/protocol.md)). Re-grab only when the token expires.

## Run

```bash
# chart captured data (no connection needed)
python -m orderflow.app --replay --symbol ASII

# capture daemon — run this all session; it owns the CSV files
python -m orderflow.capture ASII --grab

# live chart alongside the daemon (does NOT write CSVs)
python -m orderflow.app --live --symbol ASII --view-only --debug

# evaluate the regime filter on everything captured so far
python -m orderflow.backtest --symbol ASII
```

Installed console scripts `orderflow-app`, `orderflow-capture` and `orderflow-backtest`
do the same thing.

> **Only one process may write the CSVs.** Run the daemon for capture and always add
> `--view-only` to a chart running beside it — two writers corrupt the files. The daemon
> keeps recording while you open and close the chart freely.

Useful flags: `--grab` (refresh the token on launch), `--debug` (diagnostics status bar),
`--history today|all|none`, `--reset-layout`, `--reset-settings`.

## Architecture

```
orderflow/
  paths.py     one place for every file location ($ORDERFLOW_DATA overrides the archive)
  feed.py      websocket protocol, frame parsing, CSV persistence, live + replay feeds
  model.py     pure aggregation, NO Qt — footprint, CVD, volume profile, book, heatmap,
               trade classification, regime filter
  app.py       PySide6/pyqtgraph GUI (all rendering; reads from model)
  backtest.py  walk-forward regime evaluation, no GUI
  capture.py   headless capture daemon
  token.py     Playwright session-token grabber
tools/         standalone helpers (protobuf frame decoder)
docs/          protocol reference + images
data/          captured CSVs, session frames, browser profile — gitignored
```

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
- The GUI renders headless for verification: `python -m orderflow.app --replay --shot out.png`.
- `tools/decode_frame.py` dumps an unknown protobuf frame's field structure — the tool
  used to decode the trade format.
- Frame formats, units and gotchas: **[docs/protocol.md](docs/protocol.md)**.

Two units traps worth knowing before touching aggregation code: order-book `value` is
**shares** (`lots = value / 100`), and instrument tick size is derived from the **book
ladder**, not from traded prices — a sparse day or an off-tick negotiated print
otherwise corrupts the price grid.

## License

MIT — see [LICENSE](LICENSE).
