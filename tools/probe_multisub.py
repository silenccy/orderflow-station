"""
probe_multisub.py — does ONE socket accept subscriptions for several symbols?

capture.py currently opens one websocket per symbol. That works, but if the server
treats a second subscribe frame on the same socket as an ADDITIONAL subscription
rather than a replacement, N symbols would cost one connection and one keepalive
instead of N. Nobody knows which it does — the protocol is reverse-engineered — so
this measures it instead of guessing.

    python tools/probe_multisub.py ASII BBCA           # during IDX market hours
    python tools/probe_multisub.py ASII BBCA --secs 40

Method: subscribe to A, watch; subscribe to B on the SAME socket, watch again.
Then compare A's frame rate before and after B arrives.

    MULTIPLEX  both symbols stream after the 2nd subscribe -> one socket is enough
    SWITCH     A stops when B starts -> the subscribe replaces; keep one socket each
    NO-DATA    nothing arrived at all -> market closed, or the token is stale

Read-only: it writes no CSVs and never prints the token.
"""

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets                                    # noqa: E402

from orderflow import feed as of_feed                # noqa: E402


async def _collect(ws, seconds, counter, label):
    """Tally frames per symbol for `seconds`, returning total frames seen."""
    seen = 0
    loop = asyncio.get_event_loop()
    deadline = loop.time() + seconds
    while loop.time() < deadline:
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - loop.time()))
        except asyncio.TimeoutError:
            break
        if not isinstance(msg, (bytes, bytearray)):
            continue
        msg = bytes(msg)
        book = of_feed.parse_book(msg)
        if book:
            counter[(label, book[0], "book")] += 1
            seen += 1
            continue
        trades = of_feed.parse_trades(msg)
        if trades:
            for rec in trades:
                counter[(label, rec.get("symbol") or "?", "trade")] += 1
                seen += 1
            continue
        summ = of_feed.parse_summary(msg)
        if summ:
            counter[(label, summ["symbol"], "summary")] += 1
            seen += 1
    return seen


async def probe(sym_a, sym_b, secs):
    base, src = of_feed.load_subscribe_frame()
    print(f"loaded subscribe frame {len(base)}B from {src}")
    half = max(5, secs // 2)
    counter = Counter()

    async with websockets.connect(
        of_feed.WS_URL, subprotocols=["web"], additional_headers=of_feed.HEADERS,
        max_size=None, ping_interval=None,
    ) as ws:
        print(f"connected subprotocol={ws.subprotocol}")

        async def keepalive():
            while True:
                await asyncio.sleep(of_feed.PING_EVERY_SEC)
                await ws.send(of_feed.PING_BYTES)

        ka = asyncio.create_task(keepalive())
        try:
            await ws.send(of_feed.make_subscribe_for(base, sym_a))
            print(f"subscribed -> {sym_a}; watching {half}s ...")
            await _collect(ws, half, counter, "phase1")

            await ws.send(of_feed.make_subscribe_for(base, sym_b))
            print(f"subscribed -> {sym_b} on the SAME socket; watching {half}s ...")
            await _collect(ws, half, counter, "phase2")
        finally:
            ka.cancel()

    p1 = {s: n for (ph, s, _k), n in counter.items() if ph == "phase1"}
    p2 = {s: n for (ph, s, _k), n in counter.items() if ph == "phase2"}
    for sym in sorted(set(p1) | set(p2)):
        print(f"  {sym:6} phase1={sum(v for s, v in p1.items() if s == sym):5d}  "
              f"phase2={sum(v for s, v in p2.items() if s == sym):5d}")

    a_after = p2.get(sym_a, 0)
    b_after = p2.get(sym_b, 0)
    if not p1 and not p2:
        print("\nNO-DATA — nothing arrived. Market closed, or the token is stale "
              "(re-grab a subscribe frame).")
        return 2
    if a_after > 0 and b_after > 0:
        print(f"\nMULTIPLEX — both {sym_a} and {sym_b} streamed after the 2nd subscribe. "
              "One socket can carry several symbols; capture.py could use a single "
              "connection instead of one per symbol.")
        return 0
    if b_after > 0 and a_after == 0:
        print(f"\nSWITCH — {sym_a} stopped when {sym_b} was subscribed. The subscribe "
              "REPLACES rather than adds; keep one socket per symbol (what capture.py does).")
        return 1
    print(f"\nINCONCLUSIVE — {sym_a} after={a_after}, {sym_b} after={b_after}. "
          "A thin symbol may simply have had no prints; retry with two liquid tickers "
          "or a longer --secs.")
    return 3


def main():
    ap = argparse.ArgumentParser(description="Test whether one socket accepts multiple subscribes")
    ap.add_argument("symbols", nargs=2, metavar="SYMBOL",
                    help="two 4-letter IDX tickers, ideally both liquid")
    ap.add_argument("--secs", type=int, default=30, help="total watch time (split in half)")
    args = ap.parse_args()
    a, b = (s.upper() for s in args.symbols)
    for s in (a, b):
        if len(s) != 4 or not s.isalpha():
            ap.error(f"{s!r} is not a 4-letter ticker")
    try:
        sys.exit(asyncio.run(probe(a, b, args.secs)))
    except FileNotFoundError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
