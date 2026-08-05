"""
stockbit_capture.py — headless IDX capture (thin wrapper over of_feed).

The orderflow chart (of_app.py) now does capture + chart in one program; this
stays as a no-GUI fallback that just connects and persists book.csv/trades.csv/
capture_raw.jsonl. All protocol/parsing/persistence lives in of_feed.py.

Run (during IDX market hours, from this folder):
    python stockbit_capture.py [SYMBOL]

NOTE: the subscribe frame holds a ~24h session token; it is never printed/logged.
"""

import asyncio
import sys
from datetime import datetime

from . import feed as of_feed

SYMBOL = "ASII"


async def _pump(symbol):
    print(f"Capturing {symbol} -> {of_feed.BOOK_CSV}, {of_feed.TRADES_CSV}, "
          f"{of_feed.RAW_LOG}   (Ctrl+C to stop)\n")
    async for ev in of_feed.live_feed(symbol, persist=True):
        kind = ev[0]
        if kind == "status":
            print(f"[{datetime.now().isoformat()[-15:]}] {ev[1]}")
        elif kind == "book":
            _, sym, side, levels, ts = ev
            top = levels[0][0] if levels else "-"
            print(f"[{ts[-12:]}] {sym} {side:5} {len(levels):2} lvls  top={top}")
        elif kind == "trade":
            r = ev[1]
            print(f"[{r['recv_iso'][-12:]}] TRADE {r['symbol']} "
                  f"{of_feed._num(r['price'])} x {of_feed._num(r['qty'])}sh (id {r['id']})")


def main():
    """CLI entry point: orderflow-capture [SYMBOL] [--grab]"""
    if "--help" in sys.argv or "-h" in sys.argv:
        print("usage: orderflow-capture [SYMBOL] [--grab]\n\n"
              f"  SYMBOL   4-letter IDX ticker (default {SYMBOL})\n"
              "  --grab   refresh the session token before connecting")
        return
    argv = [a for a in sys.argv[1:] if not a.startswith("-")]
    sym = argv[0].upper() if argv else SYMBOL
    if "--grab" in sys.argv:                 # refresh the token on activation
        from . import token as grab_token
        ok, msg = grab_token.refresh()
        print(("token: " if ok else "token grab skipped — ") + msg)
    try:
        asyncio.run(_pump(sym))
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
