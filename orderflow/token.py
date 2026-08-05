"""
token.py — auto-grab a fresh Stockbit subscribe frame (session token) with a
dedicated Playwright/Chromium profile, so the daily manual DevTools catch is gone.

ONE-TIME SETUP
    pip install playwright
    playwright install chromium
    python -m orderflow.token --login        # a Chromium window opens; log into Stockbit,
                                         # open ANY ticker chart, then press Enter here.
                                         # (This profile is separate from your Zen browser.)

EVERY MORNING  (headless, ~10s, before starting the capture daemon)
    python -m orderflow.token                 # writes subscribe_auto.txt with a fresh token

How it works: Chromium keeps you logged in via its own profile dir (pw_profile/).
Each run loads the saved ticker page, and a CDP listener grabs the binary websocket
subscribe frame the page sends to wss-jkt.trading.stockbit.com. of_feed auto-prefers
subscribe_auto.txt over stale manual frames. The frame holds the JWT — it is written
to the (gitignored) file and never printed.
"""

import argparse
import base64
import json
import sys
import time

from . import feed as of_feed   # reuse frame_symbol() to validate what we grabbed
from .paths import GRAB_CONFIG as CONFIG, PW_PROFILE as PROFILE_DIR, SUBSCRIBE_AUTO as OUT

WS_HOST = "wss-jkt.trading.stockbit.com"
DEFAULT_URL = "https://stockbit.com/"       # replaced by the saved ticker URL after --login


def _looks_like_subscribe(data: bytes) -> bool:
    """Big binary frame carrying a 0x04 + 4-uppercase-letter symbol (not a ping/analytics)."""
    return len(data) > 200 and of_feed.frame_symbol(data) is not None


def grab(headed_login=False, url_override=None, wait_sec=30, headed=False):
    from playwright.sync_api import sync_playwright

    url = url_override or (json.loads(CONFIG.read_text()).get("url")
                           if CONFIG.exists() else None) or DEFAULT_URL
    captured = {"data": b""}
    ws_ids = set()                          # requestIds belonging to the market-data WS

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(PROFILE_DIR),
            headless=not (headed_login or headed),
            viewport={"width": 1440, "height": 900},
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        cdp = ctx.new_cdp_session(page)
        cdp.send("Network.enable")

        def on_created(params):
            if WS_HOST in params.get("url", ""):
                ws_ids.add(params.get("requestId"))

        def on_sent(params):
            if params.get("requestId") not in ws_ids:
                return
            resp = params.get("response", {})
            if resp.get("opcode") != 2:      # binary frames only
                return
            try:
                data = base64.b64decode(resp.get("payloadData", ""))
            except Exception:
                return
            if _looks_like_subscribe(data) and len(data) > len(captured["data"]):
                captured["data"] = data      # keep the largest valid subscribe

        cdp.on("Network.webSocketCreated", on_created)
        cdp.on("Network.webSocketFrameSent", on_sent)

        page.goto(url, wait_until="domcontentloaded")
        if headed_login:
            print("\n>> Log into Stockbit, open ANY ticker's chart, then press Enter here.")
            try:
                input()
            except EOFError:
                time.sleep(90)
            CONFIG.write_text(json.dumps({"url": page.url}))
            print(f">> saved ticker URL for daily runs")

        deadline = time.time() + wait_sec
        while not captured["data"] and time.time() < deadline:
            page.wait_for_timeout(500)
        ctx.close()

    return captured["data"] or None


def _save(data: bytes) -> str:
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(",".join(str(b) for b in data))
    tmp.replace(OUT)                         # atomic: never leave a half-written frame
    sym = of_feed.frame_symbol(data)
    return (f"{OUT.name} ({len(data)} bytes, native symbol {sym.decode() if sym else '?'}); "
            f"token not printed, any 4-letter symbol works via make_subscribe_for")


def refresh(headed=False, url_override=None, wait_sec=30):
    """Grab + save a fresh frame on activation (of_app / stockbit_capture --grab).
    Returns (ok: bool, message: str) and never raises, so a grab failure degrades to
    'use the existing frame' rather than blocking the app."""
    try:
        data = grab(headed_login=False, url_override=url_override,
                    wait_sec=wait_sec, headed=headed)
    except ImportError:
        return False, ("Playwright not installed — run `pip install playwright` then "
                       "`playwright install chromium`")
    except Exception as e:                   # any Playwright/runtime failure
        return False, f"token grab failed ({type(e).__name__}: {e})"
    if not data:
        return False, "no subscribe frame captured — run `python -m orderflow.token --login` first"
    return True, "fresh token -> " + _save(data)


def main():
    ap = argparse.ArgumentParser(description="Auto-grab a fresh Stockbit subscribe frame")
    ap.add_argument("--login", action="store_true",
                    help="headed one-time login; opens Chromium, you log in + open a ticker")
    ap.add_argument("--headed", action="store_true",
                    help="run the grab with a visible window (for debugging)")
    ap.add_argument("--url", help="ticker page URL (overrides the saved one)")
    ap.add_argument("--wait", type=int, default=30, help="seconds to wait for a frame")
    args = ap.parse_args()

    if args.login:
        try:
            data = grab(headed_login=True, url_override=args.url, wait_sec=args.wait)
        except ImportError:
            print("Playwright not installed. Run:\n  pip install playwright\n  "
                  "playwright install chromium", file=sys.stderr)
            sys.exit(2)
        if not data:
            print("No subscribe frame captured during login (did you open a ticker chart?).",
                  file=sys.stderr)
            sys.exit(1)
        print("OK: fresh subscribe frame -> " + _save(data))
        return

    ok, msg = refresh(headed=args.headed, url_override=args.url, wait_sec=args.wait)
    print(("OK: " if ok else "") + msg, file=sys.stdout if ok else sys.stderr)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
