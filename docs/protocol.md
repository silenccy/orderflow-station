# Stockbit Pro market-data websocket — reverse-engineered protocol

Everything below was derived by observing traffic from a logged-in browser session
and decoding captured frames (see `tools/decode_frame.py`). It is **unofficial and
undocumented** — Stockbit can change any of it without notice. Implementation lives
in [`orderflow/feed.py`](../orderflow/feed.py).

## Connection

| | |
|---|---|
| URL | `wss://wss-jkt.trading.stockbit.com/ws` |
| Subprotocol | **`web`** — the handshake is rejected without it |
| Headers | `Origin: https://stockbit.com` + a browser `User-Agent` |
| Auth | **no cookie on the handshake** — auth rides *inside* the subscribe frame |
| Wire format | binary protobuf |

### Keepalive
The client must send an app-level ping or the server silently drops the connection:

- bytes `[34, 6, 10, 4, 112, 105, 110, 103]` (protobuf field 4 = `"ping"`), every ~10 s
- disable the `websockets` library's own WS ping (`ping_interval=None`)

## Subscribe frame (sent, protobuf, ~916–922 bytes)

One frame subscribes one symbol across all channels.

| field | type | contents |
|---|---|---|
| 1 | string | user id |
| 2 | message | the symbol, repeated across channel sub-fields (2, 5, 6, 7), each `0x04` + 4 ASCII letters. The 922-byte variant carries 4 channels (book **and** trades); 916 carries 3. |
| 3 | string (44 B) | base64 device/session key |
| 5 | string (~838 B) | **JWT auth token — SECRET, ~24 h expiry** |

**Symbol swapping is safe.** The ticker is length-prefixed `0x04` + ASCII, and `0x04`
cannot occur inside the base64 token, so replacing `b"\x04CUAN"` with `b"\x04XXXX"`
only ever hits real symbol fields. `feed.make_subscribe_for()` uses this to subscribe
any 4-letter IDX ticker from a single captured frame — no re-capture per symbol.

**Token expiry:** ~24 h, and it also dies on session rotation (re-login elsewhere).
Symptom of a stale token: the socket connects, then an explicit
`you are not authorized` frame (~29 bytes) and no data.

## Order book frame (received)

A protobuf wrapper around a **pipe-delimited ASCII** payload:

```
<framing> SYM | OFFER | p;f;v | p;f;v | ... | <id> | <total> | <ts_event> | <ts_server>
```

- Marker `SYM|OFFER|` (asks) or `SYM|BID|` (bids) — both confirmed live.
- Each level is `price;freq;value`:
  - `price` — price level (IDR)
  - `freq` — **number of resting orders** at that level (rarely exposed by a retail feed)
  - `value` — **total shares** resting. **lots = value / 100.**
- ~60 levels per side (not the 5-level public cap).
- Trailer holds an update id, a total, and two nanosecond ISO timestamps (event +
  server). **Sequence on the server timestamp**, not the local clock.
- Each frame is a **full snapshot of one side**, not a delta. Sides arrive
  independently, so a momentarily "crossed" book (bid ≥ ask) is a normal artifact of
  one side being stale — only a sustained spike means real desync.

> **Units gotcha:** `value` is shares. An early revision of these notes said
> `lots = value / price / 100`, which is wrong and yields nonsense (~0.6 lots).
> Verified against Stockbit's own ladder totals: **`lots = value / 100`**.

Example:
`ASII|OFFER|4790;18;183000|4800;21;381300|...|<id>|<total>|<ts1>|<ts2>`

## Trade frame (received) — decoded

Pure protobuf (no ASCII payload). Wrapper **field 8** holds one or more trade
records in **field 1**; each record:

| field | contents |
|---|---|
| 3 | price |
| 4 | quantity (**shares**) |
| 9 | trade id (used for dedup across reconnect backfill) |
| 11 | value = price × qty |
| 5 | a flag; meaning unresolved (does **not** reliably indicate aggressor side) |

Sizes seen: ~91 B for a live tick, ~3.6 KB for the reconnect backfill batch.

**Aggressor side is not in the frame** — it is inferred client-side by the quote rule
against the synced book, with a tick-rule fallback (Lee–Ready style); see
`model.classify()`. Trades that arrive before the first book snapshot (the backfill)
therefore classify by tick/carry only.

**Broker codes are not present** on this channel, despite Stockbit's UI showing them.

## Session summary frame (received)

Wrapper field 9: `2` last, `3` volume (shares), `4` high, `5` low, `6` open,
`7` trade count, `10` average (the exchange's own VWAP), `13` previous close,
`14` turnover.

## Practical notes

- Frames arrive interleaved; the parser dispatches on shape, not order.
- The reconnect backfill replays recent trades — **dedup on trade id (field 9)** or
  the tape doubles up (`model` does this).
- One captured frame + `make_subscribe_for` covers every symbol, so a re-capture is
  only needed when the **token** expires, not when you change ticker.
