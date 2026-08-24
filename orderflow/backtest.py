"""
backtest.py — walk-forward backtest of the regime filter (model.regime()).

Pure Python, no Qt. Replays captured trades through the SAME model the live chip
uses, evaluating the regime only at bar close (no lookahead), purged at capture
gaps, and reports (1) per-label forward statistics and (2) a toy long-only
strategy vs buy-&-hold with a within-day label-permutation p-value.

    python -m orderflow.backtest --symbol ASII
    python -m orderflow.backtest --symbol ASII --horizons 5,10,20 --csv out.csv

Honesty: with only a handful of captured days the output is direction-of-evidence,
not proof. Re-run as the capture archive grows.
"""

import argparse
import math
import random
import sys
from collections import Counter, defaultdict

from . import __version__
from . import feed as of_feed
from . import model as of_model

# Regime labels contain ↑/↓; Windows consoles default to cp1252 and would crash on
# print(). Force UTF-8 on our own streams rather than mangling the labels.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):      # non-reconfigurable stream (redirect/pipe)
        pass

GAP_MIN_DEFAULT = 30    # >30 min without trades = capture hole / lunch break -> new
                        # segment. (Quiet 5-10 min lulls are real market time on IDX —
                        # splitting there would let the warm-up gate eat the day.)


# ---------- data loading (same parser as live; trades only — regime uses closes) ----------
def load_days(symbol):
    """{date: [trade recs sorted by execution epoch]} for one symbol."""
    days = defaultdict(list)
    for ev in of_feed.replay_feed():
        if ev[0] != "trade":
            continue
        rec = ev[1]
        if rec.get("symbol") != symbol:
            continue
        d = (rec.get("recv_iso") or rec.get("trade_time") or "")[:10]
        ep = of_model.trade_epoch(rec)
        if d and ep is not None:
            days[d].append((ep, rec))
    out = {}
    for d, rows in days.items():
        rows.sort(key=lambda t: t[0])
        seen = set()
        clean = []
        for ep, rec in rows:              # dedup restart-backfill overlaps by trade id
            tid = rec.get("id")
            if tid is not None:
                if tid in seen:
                    continue
                seen.add(tid)
            clean.append((ep, rec))
        out[d] = clean
    return dict(sorted(out.items()))


def split_segments(rows, gap_sec=GAP_MIN_DEFAULT * 60):
    """Split a day's (ep, rec) rows into contiguous segments at >gap_sec holes."""
    segs, cur = [], []
    prev = None
    for ep, rec in rows:
        if prev is not None and ep - prev > gap_sec and cur:
            segs.append(cur)
            cur = []
        cur.append((ep, rec))
        prev = ep
    if cur:
        segs.append(cur)
    return segs


# ---------- walk-forward signal generation ----------
def run_segment(rows, bar_size, window, warmup, er_trend, er_chop):
    """Feed one contiguous segment; evaluate regime() at each bar boundary BEFORE
    the new bar's first trade is applied (just-closed bar final, forming bar empty).
    Returns (signals, closes): signals[i] refers to closes[sig['i']] with execution
    at sig['next_open'] (the new bar's first trade = next-bar-open fill)."""
    m = of_model.OrderflowModel(of_model.make_bars("time", bar_size))
    signals, closes = [], []
    cur_bar = None
    for ep, rec in rows:
        bar = int(ep // bar_size) * bar_size
        if cur_bar is not None and bar != cur_bar:
            closes.append(m.bar_meta[cur_bar]["c"])          # bar just completed
            r = m.regime(window, warmup, er_trend, er_chop)
            if r["ready"]:
                signals.append({"i": len(closes) - 1, "core": r["core"],
                                "dir": r["direction"], "er": r["er"], "vr": r["vr"],
                                "rv_pct": r["rv_pct"], "next_open": rec["price"],
                                "ep": ep})
        cur_bar = bar
        m.on_event(("trade", rec))
    if cur_bar is not None:
        closes.append(m.bar_meta[cur_bar]["c"])              # final (possibly partial) bar
    return signals, closes


def est_halfspread_pct(rows):
    """Half a tick over the median price, as %: cost proxy without book data."""
    prices = [rec["price"] for _ep, rec in rows]
    uniq = sorted(set(prices))
    diffs = [round(b - a, 6) for a, b in zip(uniq, uniq[1:])]
    tick = Counter(diffs).most_common(1)[0][0] if diffs else 0.0
    mid = sorted(prices)[len(prices) // 2] if prices else 1.0
    return 100.0 * (tick / 2) / mid if mid else 0.0


# ---------- (1) label-quality metrics ----------
def fwd_er(closes, i, k):
    seg = closes[i:i + k + 1]
    path = sum(abs(seg[j] - seg[j - 1]) for j in range(1, len(seg)))
    return abs(seg[-1] - seg[0]) / path if path > 0 else 0.0


def label_metrics(day_segs, horizons):
    """{(label, k, overlap?): aggregates} across all segments of all days."""
    acc = defaultdict(lambda: {"n": 0, "fr": 0.0, "hit": 0, "absfr": 0.0, "fer": 0.0})
    for segs in day_segs.values():
        for signals, closes in segs:
            for k in horizons:
                last_taken = -10**9
                for s in signals:
                    i = s["i"]
                    if i + k >= len(closes):
                        continue                     # purged: window would cross the end
                    fr = math.log(closes[i + k] / closes[i]) if closes[i] > 0 else 0.0
                    for overlap in (True, False):
                        if not overlap:
                            if i - last_taken < k:
                                continue
                            last_taken = i
                        a = acc[(s["core"], k, overlap)]
                        a["n"] += 1
                        a["fr"] += s["dir"] * fr     # direction-aligned (TREND labels)
                        a["hit"] += 1 if (fr > 0) == (s["dir"] > 0) and fr != 0 else 0
                        a["absfr"] += abs(fr)
                        a["fer"] += fwd_er(closes, i, k)
    return acc


# ---------- (2) toy conditional strategy ----------
def strategy_pnl(segs, half_spread, fee_buy, fee_sell, allow_short, cores=None):
    """Long in TREND↑ (short in TREND↓ if allowed), flat otherwise; fills at
    next-bar open; liquidate at segment end. Returns total % PnL and n trades.
    `cores` optionally overrides each signal's label (permutation test)."""
    total, ntr = 0.0, 0
    for signals, closes in segs:
        pos, entry = 0, 0.0
        seq = cores if cores is not None else [s["core"] for s in signals]
        for s, core in zip(signals, seq):
            tgt = 1 if core == "TREND↑" else (-1 if (allow_short and core == "TREND↓") else 0)
            if tgt != pos:
                px = s["next_open"]
                if pos != 0:                        # close current position
                    total += pos * (px - entry) / entry * 100 - half_spread - fee_sell
                if tgt != 0:                        # open new position
                    entry = px
                    total -= half_spread + fee_buy
                    ntr += 1
                pos = tgt
        if pos != 0 and closes:
            px = closes[-1]
            total += pos * (px - entry) / entry * 100 - half_spread - fee_sell
            pos = 0
    return total, ntr


def buy_hold_pnl(segs, half_spread, fee_buy, fee_sell):
    total = 0.0
    for signals, closes in segs:
        if not signals or not closes:
            continue
        entry = signals[0]["next_open"]
        total += (closes[-1] - entry) / entry * 100 - 2 * half_spread - fee_buy - fee_sell
    return total


def permutation_test(segs, actual, half_spread, fee_buy, fee_sell, allow_short,
                     reps=1000, seed=7):
    """Block-shuffle label runs within each segment; p = P(permuted >= actual)."""
    rng = random.Random(seed)
    per_seg_runs = []
    for signals, _ in segs:
        runs, prev = [], None
        for s in signals:
            if s["core"] != prev:
                runs.append([s["core"], 0])
                prev = s["core"]
            runs[-1][1] += 1
        per_seg_runs.append(runs)
    ge = 0
    for _ in range(reps):
        pnl = 0.0
        for (signals, closes), runs in zip(segs, per_seg_runs):
            shuf = runs[:]
            rng.shuffle(shuf)
            cores = [lab for lab, n in shuf for _ in range(n)]
            p, _n = strategy_pnl([(signals, closes)], half_spread, fee_buy, fee_sell,
                                 allow_short, cores=cores)
            pnl += p
        if pnl >= actual - 1e-12:
            ge += 1
    return ge / reps


# ---------- report ----------
def main():
    ap = argparse.ArgumentParser(description="Walk-forward backtest of the regime filter")
    ap.add_argument("--version", action="version",
                    version="orderflow-station %s" % __version__)
    ap.add_argument("--symbol", default="ASII")
    ap.add_argument("--bar-size", type=int, default=60)
    ap.add_argument("--window", type=int, default=20)
    ap.add_argument("--warmup", type=int, default=20, help="minutes")
    ap.add_argument("--er-trend", type=float, default=0.5)
    ap.add_argument("--er-chop", type=float, default=0.3)
    ap.add_argument("--horizons", default="5,10,20")
    ap.add_argument("--fees", default="0.15,0.25", help="%%/side buy,sell (IDX retail)")
    ap.add_argument("--allow-short", action="store_true",
                    help="add the TREND↓ short leg (info only; retail IDX can't short)")
    ap.add_argument("--reps", type=int, default=1000, help="permutation reps")
    ap.add_argument("--gap-min", type=int, default=GAP_MIN_DEFAULT,
                    help="split segments at trade gaps longer than this (minutes)")
    ap.add_argument("--csv", help="dump per-signal rows to CSV")
    ap.add_argument("--sweep", action="store_true", help="threshold grid (needs >=8 days)")
    args = ap.parse_args()
    horizons = [int(x) for x in args.horizons.split(",")]
    fee_buy, fee_sell = (float(x) for x in args.fees.split(","))

    days = load_days(args.symbol)
    if not days:
        print(f"no captured trades for {args.symbol}")
        return
    if args.sweep and len(days) < 8:
        print(f"--sweep refused: only {len(days)} captured day(s); need >=8 for a "
              f"train/test split that means anything")
        return

    gap_sec = args.gap_min * 60
    day_segs, all_rows = {}, []
    for d, rows in days.items():
        segs = [run_segment(seg, args.bar_size, args.window, args.warmup,
                            args.er_trend, args.er_chop)
                for seg in split_segments(rows, gap_sec)]
        day_segs[d] = segs
        all_rows.extend(rows)
    half_spread = est_halfspread_pct(all_rows)

    nsig = sum(len(s) for segs in day_segs.values() for s, _c in segs)
    print(f"=== {args.symbol}: {len(days)} day(s), "
          f"{sum(len(split_segments(r, gap_sec)) for r in days.values())} segment(s), "
          f"{nsig} regime evaluations (bar {args.bar_size}s, gap split >{args.gap_min}m) ===")
    for d, segs in day_segs.items():
        labs = Counter(s["core"] for sig, _c in segs for s in sig)
        print(f"  {d}: {sum(len(s) for s, _ in segs):4d} signals  {dict(labs)}")

    print(f"\n--- label quality (direction-aligned fwd return, bp; hit%; fwd ER) ---")
    acc = label_metrics(day_segs, horizons)
    hdr = f"{'label':8s} {'k':>3s} {'mode':>7s} {'n':>5s} {'alignedbp':>10s} {'hit%':>6s} {'|move|bp':>9s} {'fwdER':>6s}"
    print(hdr)
    for (label, k, overlap), a in sorted(acc.items(), key=lambda x: (x[0][0], x[0][1], not x[0][2])):
        if a["n"] == 0:
            continue
        print(f"{label:8s} {k:3d} {'overlap' if overlap else 'indep':>7s} {a['n']:5d} "
              f"{1e4 * a['fr'] / a['n']:10.1f} {100 * a['hit'] / a['n']:6.1f} "
              f"{1e4 * a['absfr'] / a['n']:9.1f} {a['fer'] / a['n']:6.2f}")

    print(f"\n--- toy strategy (long TREND↑{' / short TREND↓' if args.allow_short else ''}, "
          f"next-open fills) ---")
    print(f"costs: half-spread {half_spread:.3f}%/side + fees {fee_buy}/{fee_sell}%/side")
    flat_segs = [sc for segs in day_segs.values() for sc in segs]
    pnl, ntr = strategy_pnl(flat_segs, half_spread, fee_buy, fee_sell, args.allow_short)
    pnl0, _ = strategy_pnl(flat_segs, 0.0, 0.0, 0.0, args.allow_short)
    bh = buy_hold_pnl(flat_segs, half_spread, fee_buy, fee_sell)
    pval = permutation_test(flat_segs, pnl, half_spread, fee_buy, fee_sell,
                            args.allow_short, reps=args.reps)
    print(f"strategy: {pnl:+.2f}% net ({pnl0:+.2f}% pre-cost, {ntr} round-trips)")
    print(f"buy&hold: {bh:+.2f}% net   |   permutation p-value: {pval:.3f} "
          f"(P(random labels do >= this) — {args.reps} reps)")
    print(f"\nHONESTY: {len(days)} day(s) of data — direction-of-evidence only. "
          f"Re-run as the capture archive grows; don't tune thresholds on this sample.")

    if args.csv:
        import csv as _csv
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            wcsv = _csv.writer(f)
            wcsv.writerow(["day", "seg", "bar_i", "epoch", "core", "dir", "er", "vr",
                           "rv_pct", "close", "next_open"])
            for d, segs in day_segs.items():
                for si, (signals, closes) in enumerate(segs):
                    for s in signals:
                        wcsv.writerow([d, si, s["i"], f"{s['ep']:.3f}", s["core"], s["dir"],
                                       f"{s['er']:.4f}",
                                       f"{s['vr']:.4f}" if s["vr"] is not None else "",
                                       f"{s['rv_pct']:.1f}", closes[s["i"]], s["next_open"]])
        print(f"wrote {args.csv}")


if __name__ == "__main__":
    main()
