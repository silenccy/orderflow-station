"""
of_feed.py — Stockbit IDX market-data feed, shared by the headless capture
(stockbit_capture.py) and the orderflow chart (of_app.py).

Owns the websocket protocol, frame parsing, CSV persistence, and a single unified
event stream consumed by both live and replay modes.

Events yielded (live_feed / replay_feed):
    ("book",   symbol, side, levels, recv_iso)   levels = [(price, freq, value), ...]
    ("trade",  record)                           record = trade dict (see below)
    ("status", text)                             connection notices (live only)

Trade record dict:
    {symbol, price, qty, value, id, flag, sec, ns, trade_time, recv_iso}

Schema notes (reverse-engineered):
  - Each book frame is a FULL per-side snapshot (~59 ask / ~73 bid levels).
  - Trade frames are protobuf: wrapper field #8 holding one or more trade records
    (field #1); per record #3 price, #4 qty(shares), #9 id, #11 value=price*qty.
  - The subscribe frame carries a ~24h JWT — never logged here (outbound only).
"""

import asyncio
import csv
import itertools
import re
import struct
from datetime import datetime
from pathlib import Path

import websockets

# ============================================================
#  CONFIG
# ============================================================
from .paths import (BOOK_CSV, DATA_DIR, RAW_LOG, SUMMARY_CSV,  # noqa: F401 (re-exported)
                    TRADES_CSV)

WS_URL = "wss://wss-jkt.trading.stockbit.com/ws"
SUBSCRIBE_FILE = None            # None -> auto-pick largest valid subscribe_*.txt

PING_BYTES = bytes([34, 6, 10, 4, 112, 105, 110, 103])  # 0x22 0x06 0x0a 0x04 'ping'
PING_EVERY_SEC = 10
IDLE_WARN_SEC = 20

HEADERS = {
    "Origin": "https://stockbit.com",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0",
}


# ============================================================
#  Subscribe-frame loading
# ============================================================
def parse_frame_file(path: Path) -> bytes:
    nums = [int(x) for x in re.split(r"[,\s]+", Path(path).read_text().strip()) if x != ""]
    return bytes(nums)


def load_subscribe_frame(subscribe_file=None, directory=None):
    """Return (frame_bytes, source_name). Uses an explicit file if given, else the
    largest subscribe_*.txt in `directory` that parses as decimal bytes (the
    browser catcher drops extras; the biggest valid one is the real subscribe)."""
    directory = Path(directory) if directory is not None else DATA_DIR
    sf = subscribe_file if subscribe_file is not None else SUBSCRIBE_FILE
    if sf:
        p = Path(sf)
        return parse_frame_file(p), p.name

    auto = Path(directory) / "subscribe_auto.txt"   # token grabber's fresh frame wins
    if auto.exists():                               # over stale manual subscribe_*.txt
        try:
            return parse_frame_file(auto), auto.name
        except ValueError:
            pass

    best = None  # (length, name, bytes)
    for p in sorted(Path(directory).glob("subscribe_*.txt")):
        try:
            frame = parse_frame_file(p)
        except ValueError:
            continue  # not decimal bytes (e.g. an analytics/text frame)
        if best is None or len(frame) > best[0]:
            best = (len(frame), p.name, frame)
    if best is None:
        raise FileNotFoundError(
            "No parseable subscribe_*.txt found — capture a frame with the browser "
            "catcher first (and run from the folder it's in)."
        )
    return best[2], best[1]


def frame_symbol(base: bytes):
    """The 4-letter ticker encoded in a subscribe frame: protobuf length-prefix
    0x04 followed by 4 uppercase ASCII bytes (0x04 can't occur in the token)."""
    for i in range(len(base) - 4):
        if base[i] == 0x04 and all(65 <= base[i + 1 + j] <= 90 for j in range(4)):
            return base[i + 1:i + 5]
    return None


def make_subscribe_for(base: bytes, symbol: str) -> bytes:
    """Swap the frame's captured ticker for `symbol`, so ANY captured frame can
    subscribe ANY 4-letter IDX symbol (it appears in all feed channels). 0x04 is
    a control byte absent from the base64 token, so this only hits symbol fields."""
    if len(symbol) != 4 or not symbol.isalpha():
        raise ValueError(f"Symbol must be 4 letters (got {symbol!r}).")
    native = frame_symbol(base)
    if native is None:
        return base
    return base.replace(b"\x04" + native, b"\x04" + symbol.upper().encode("ascii"))


# ============================================================
#  Book parsing  ("<SYM>|OFFER|p;f;v|..." / "...|BID|...")
# ============================================================
BOOK_RE = re.compile(rb"([A-Z]{4})\|(OFFER|BID)\|([0-9;|]+)")
TRIPLET_RE = re.compile(rb"(\d+);(\d+);(\d+)")


def parse_book(frame: bytes):
    """Return (symbol, side, [(price, freq, value), ...]) or None."""
    m = BOOK_RE.search(frame)
    if not m:
        return None
    symbol = m.group(1).decode()
    side = m.group(2).decode()
    levels = [(int(p), int(f), int(v)) for p, f, v in TRIPLET_RE.findall(m.group(3))]
    return symbol, side, levels


# ============================================================
#  Trade parsing (protobuf)
# ============================================================
def _read_varint(b, i):
    shift = result = 0
    while True:
        byte = b[i]; i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7


def _pb_fields(b):
    """Yield (field_num, wire_type, value). value: int (varint) or bytes (else)."""
    i, n = 0, len(b)
    while i < n:
        try:
            tag, i = _read_varint(b, i)
        except IndexError:
            return
        field, wt = tag >> 3, tag & 7
        if wt == 0:
            val, i = _read_varint(b, i); yield field, wt, val
        elif wt == 1:
            yield field, wt, b[i:i + 8]; i += 8
        elif wt == 2:
            ln, i = _read_varint(b, i)
            yield field, wt, b[i:i + ln]; i += ln
        elif wt == 5:
            yield field, wt, b[i:i + 4]; i += 4
        else:
            return


def _f64(b):
    return struct.unpack("<d", b)[0] if len(b) == 8 else None


def _num(x):
    """Trim integer-valued floats (4800.0 -> 4800) for clean CSV output."""
    if x is None:
        return ""
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def _parse_trade_record(b):
    rec = {"sec": None, "ns": None, "symbol": None,
           "price": None, "qty": None, "value": None, "id": None, "flag": None}
    for field, wt, val in _pb_fields(b):
        if field == 3 and wt == 1:
            rec["price"] = _f64(val)
        elif field == 4 and wt == 1:
            rec["qty"] = _f64(val)
        elif field == 11 and wt == 1:
            rec["value"] = _f64(val)
        elif field == 9 and wt == 0:
            rec["id"] = val
        elif field == 5 and wt == 0:
            rec["flag"] = val
        elif field == 2 and wt == 2:
            rec["symbol"] = val.decode("ascii", "replace")
        elif field == 1 and wt == 2:
            for ff, fwt, fv in _pb_fields(val):
                if ff == 1 and fwt == 0:
                    rec["sec"] = fv
                elif ff == 2 and fwt == 0:
                    rec["ns"] = fv
    if rec["price"] is None or rec["id"] is None:
        return None
    return rec


def parse_trades(frame: bytes):
    """Return a list of trade records, or None if this isn't a trade frame.
    Trade frames are a field-8 wrapper containing one or more field-1 records."""
    trades = []
    is_trade_frame = False
    for field, wt, val in _pb_fields(frame):
        if field == 8 and wt == 2:
            is_trade_frame = True
            for f2, wt2, v2 in _pb_fields(val):
                if f2 == 1 and wt2 == 2:
                    rec = _parse_trade_record(v2)
                    if rec:
                        trades.append(rec)
    return trades if is_trade_frame else None


def trade_time(rec) -> str:
    """ISO event time with nanoseconds, from the record's #1 timestamp."""
    if rec.get("sec") is None:
        return ""
    base = datetime.fromtimestamp(rec["sec"]).strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{(rec.get('ns') or 0):09d}"


def parse_summary(frame: bytes):
    """Session OHLC/turnover summary, or None. Field-9 wrapper; inner #1 = symbol.
    f64 fields: #2 last, #3 volume(shares), #4 high, #5 low, #6 open, #7 trades,
    #10 avg(VWAP), #13 prev close, #14 turnover(IDR)."""
    for field, wt, val in _pb_fields(frame):
        if field == 9 and wt == 2:
            sym, f = None, {}
            for ff, fwt, fv in _pb_fields(val):
                if ff == 1 and fwt == 2:
                    sym = fv.decode("ascii", "replace")
                elif fwt == 1:
                    f[ff] = _f64(fv)
            if sym is None or len(f) < 4:
                return None
            return {"symbol": sym, "last": f.get(2), "vol_sh": f.get(3),
                    "high": f.get(4), "low": f.get(5), "open": f.get(6),
                    "trades": f.get(7), "avg": f.get(10), "prev": f.get(13),
                    "val": f.get(14)}
    return None


# ============================================================
#  CSV persistence
# ============================================================
def _needs_header(name):
    p = Path(name)
    return not (p.exists() and p.stat().st_size > 0)


class CsvSink:
    """Appends book levels, trades, and raw frames to CSV/JSONL, flushing each
    write so a hard stop never loses data. Used by live mode (persist=True)."""
    BOOK_COLS = ["recv_time", "symbol", "side", "price", "freq", "value"]
    TRADE_COLS = ["recv_time", "symbol", "trade_time", "price", "qty",
                  "lots", "value", "trade_id", "flag"]
    SUMMARY_COLS = ["recv_time", "symbol", "last", "open", "prev", "high", "low",
                    "avg", "vol_sh", "val", "trades"]

    def __init__(self, book_csv=BOOK_CSV, trades_csv=TRADES_CSV, raw_log=RAW_LOG,
                 summary_csv=SUMMARY_CSV):
        self._book_f = open(book_csv, "a", newline="", encoding="utf-8")
        self._book_w = csv.writer(self._book_f)
        if _needs_header(book_csv):
            self._book_w.writerow(self.BOOK_COLS); self._book_f.flush()
        self._tr_f = open(trades_csv, "a", newline="", encoding="utf-8")
        self._tr_w = csv.writer(self._tr_f)
        if _needs_header(trades_csv):
            self._tr_w.writerow(self.TRADE_COLS); self._tr_f.flush()
        self._sum_f = open(summary_csv, "a", newline="", encoding="utf-8")
        self._sum_w = csv.writer(self._sum_f)
        if _needs_header(summary_csv):
            self._sum_w.writerow(self.SUMMARY_COLS); self._sum_f.flush()
        self._raw_f = open(raw_log, "a", encoding="utf-8") if raw_log else None

    def write_book(self, ts, sym, side, levels):
        for price, freq, value in levels:
            self._book_w.writerow([ts, sym, side, price, freq, value])
        self._book_f.flush()

    def write_trade(self, ts, rec):
        lots = int(rec["qty"] // 100) if rec.get("qty") is not None else ""
        self._tr_w.writerow([ts, rec["symbol"], rec.get("trade_time", ""),
                             _num(rec["price"]), _num(rec["qty"]), lots,
                             _num(rec["value"]), rec["id"], rec["flag"]])
        self._tr_f.flush()

    def write_summary(self, ts, s):
        self._sum_w.writerow([ts, s["symbol"], _num(s["last"]), _num(s["open"]),
                              _num(s["prev"]), _num(s["high"]), _num(s["low"]),
                              _num(s["avg"]), _num(s["vol_sh"]), _num(s["val"]),
                              _num(s["trades"])])
        self._sum_f.flush()

    def write_raw(self, ts, hexstr):
        if self._raw_f:
            self._raw_f.write(f'{{"t":"{ts}","hex":"{hexstr}"}}\n')
            self._raw_f.flush()

    def close(self):
        for f in (self._book_f, self._tr_f, self._sum_f, self._raw_f):
            try:
                if f:
                    f.close()
            except Exception:
                pass


# ============================================================
#  Live feed
# ============================================================
async def _keepalive(ws):
    try:
        while True:
            await asyncio.sleep(PING_EVERY_SEC)
            await ws.send(PING_BYTES)
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


async def live_feed(symbol, subscribe_file=None, persist=True, raw=True,
                    idle_warn=IDLE_WARN_SEC):
    """Async generator: connect, subscribe, keepalive, parse, optionally persist,
    and yield ('book'|'trade'|'status', ...) events. Cancellation-safe."""
    base, src = load_subscribe_frame(subscribe_file)
    yield ("status", f"loaded subscribe frame {len(base)}B from {src}")

    sink = CsvSink(raw_log=(RAW_LOG if raw else None)) if persist else None
    seen_ids = set()
    try:
        async with websockets.connect(
            WS_URL, subprotocols=["web"], additional_headers=HEADERS,
            max_size=None, ping_interval=None,
        ) as ws:
            yield ("status", f"connected subprotocol={ws.subprotocol}")
            await ws.send(make_subscribe_for(base, symbol))
            yield ("status", f"subscribed -> {symbol}")
            ka = asyncio.create_task(_keepalive(ws))
            try:
                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=idle_warn)
                    except asyncio.TimeoutError:
                        yield ("status", f"idle {idle_warn}s — no frame "
                                         "(market closed? token expired?)")
                        continue
                    if not isinstance(msg, (bytes, bytearray)):
                        continue
                    msg = bytes(msg)
                    ts = datetime.now().isoformat()
                    if sink:
                        sink.write_raw(ts, msg.hex())

                    book = parse_book(msg)
                    if book:
                        sym, side, levels = book
                        if sink:
                            sink.write_book(ts, sym, side, levels)
                        yield ("book", sym, side, levels, ts)
                        continue

                    trades = parse_trades(msg)
                    if trades:
                        for rec in trades:
                            if rec["id"] in seen_ids:
                                continue
                            seen_ids.add(rec["id"])
                            rec["recv_iso"] = ts
                            rec["trade_time"] = trade_time(rec)
                            if sink:
                                sink.write_trade(ts, rec)
                            yield ("trade", rec)
                        continue

                    summ = parse_summary(msg)
                    if summ:
                        if sink:
                            sink.write_summary(ts, summ)
                        yield ("summary", summ)
                        continue
                    # remaining frames (pong, etc.) ignored
            finally:
                ka.cancel()
    except websockets.ConnectionClosed as e:
        yield ("status", f"connection closed: {e}")
    finally:
        if sink:
            sink.close()


# ============================================================
#  Replay feed (from CSVs)
# ============================================================
def replay_feed(book_csv=BOOK_CSV, trades_csv=TRADES_CSV, summary_csv=SUMMARY_CSV):
    """Generator yielding ('book'|'trade'|'summary', ...) events from captured
    CSVs, ordered by receive time. Books are regrouped from per-level rows."""
    events = []  # (recv_time, kind, payload...)

    bp = Path(book_csv)
    if bp.exists():
        with open(book_csv, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        keyf = lambda r: (r["recv_time"], r["symbol"], r["side"])
        for (rt, sym, side), grp in itertools.groupby(rows, key=keyf):
            levels = [(int(g["price"]), int(g["freq"]), int(g["value"])) for g in grp]
            events.append((rt, "book", sym, side, levels))

    tp = Path(trades_csv)
    if tp.exists():
        with open(trades_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rec = {
                    "symbol": r["symbol"],
                    "trade_time": r.get("trade_time", ""),
                    "price": float(r["price"]),
                    "qty": float(r["qty"]),
                    "value": float(r["value"]) if r.get("value") else None,
                    "id": int(r["trade_id"]),
                    "flag": int(r["flag"]) if r.get("flag") not in (None, "") else None,
                    "recv_iso": r["recv_time"],
                }
                events.append((r["recv_time"], "trade", rec))

    spath = Path(summary_csv)
    if spath.exists():
        with open(summary_csv, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = {"symbol": r["symbol"]}
                for k in ("last", "open", "prev", "high", "low", "avg",
                          "vol_sh", "val", "trades"):
                    v = r.get(k)
                    d[k] = float(v) if v not in (None, "") else None
                events.append((r["recv_time"], "summary", d))

    events.sort(key=lambda e: e[0])
    for e in events:
        if e[1] == "book":
            _, _, sym, side, levels = e
            yield ("book", sym, side, levels, e[0])
        elif e[1] == "trade":
            yield ("trade", e[2])
        else:
            yield ("summary", e[2])
