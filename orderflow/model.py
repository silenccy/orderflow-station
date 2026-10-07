"""
model.py — orderflow state + aggregation (pure Python/numpy, no Qt).

Feed it the ('book'|'trade', ...) events from of_feed (live or replay) and it
maintains everything the chart panels need:
  - BookState     : current bid/ask ladders, best bid/ask, spread
  - Footprint     : (bar, price) -> {buy, sell} volume, per pluggable bar strategy
  - CVD           : cumulative signed volume series
  - VolumeAtPrice : price -> {buy, sell} volume (volume profile)
  - HeatmapBuffer : rolling [price x time] resting-size columns
  - HeatGrid      : those columns as a level matrix the heatmap panel can draw,
                    updated incrementally, with persistent-wall detection

Aggressor side comes from the exchange's own tag when it is present, and is
inferred Lee-Ready style only when it is not (quote rule vs the synced book, tick
rule inside the spread, carry on a zero tick). Both live in aggressor()/classify(),
and diag() reports which source decided each trade.
"""

import math
import warnings
from bisect import bisect_left
from collections import Counter
from datetime import datetime

import numpy as np


def _weighted_pct(counter, pct):
    """Percentile over a {value: count} histogram (value at which the cumulative
    count first reaches pct%). Cheap because distinct values stay few."""
    items = sorted(counter.items())
    total = sum(c for _v, c in items)
    if not total:
        return None
    target = pct / 100.0 * total
    acc = 0
    for v, c in items:
        acc += c
        if acc >= target:
            return v
    return items[-1][0]


# ============================================================
#  Time helpers
# ============================================================
def _parse_iso_epoch(s):
    """Epoch seconds (local tz) from an ISO string, tolerating 9-digit nanos."""
    if not s:
        return None
    if "." in s:
        head, frac = s.split(".", 1)
        tz = ""
        for i, ch in enumerate(frac):
            if ch in "+-":
                tz, frac = frac[i:], frac[:i]
                break
        micro = (frac + "000000")[:6]
        base = datetime.fromisoformat(head + tz).timestamp()
        return base + int(micro) / 1e6
    return datetime.fromisoformat(s).timestamp()


def trade_epoch(rec):
    """Execution epoch (float sec) for a trade record, preferring the event ts."""
    if rec.get("sec") is not None:
        return rec["sec"] + (rec.get("ns") or 0) / 1e9
    return _parse_iso_epoch(rec.get("trade_time") or rec.get("recv_iso"))


# ============================================================
#  Trade classification (Lee-Ready style)
# ============================================================
def classify(price, bid, ask, last_price, last_side):
    """Return 'buy' (buyer-initiated) or 'sell'. Quote rule vs best bid/ask, then
    tick rule inside the spread, carrying the last side on a zero tick."""
    if ask is not None and price >= ask:
        return "buy"
    if bid is not None and price <= bid:
        return "sell"
    if last_price is not None:
        if price > last_price:
            return "buy"
        if price < last_price:
            return "sell"
    return last_side


# The exchange's own aggressor tag, trade field 5. Measured against the quote rule
# over 8,138 BUMI prints on 2026-08-31: flag 1 agreed on 94.7%, flag 2 on 91.6%.
# Where they disagree the tag is likelier right -- the quote rule compares against
# a book whose two sides arrive in separate frames, so it can be momentarily stale.
FLAG_SIDE = {1: "buy", 2: "sell"}


def aggressor(rec, bid, ask, last_price, last_side):
    """(side, source) for one trade. Prefer what the exchange tells us; infer only
    when it does not.

    `source` is 'flag' when the tag decided it, otherwise the Lee-Ready rule that
    fired ('quote' | 'tick' | 'carry'), so diag() can report how much of the delta
    is recorded fact rather than inference.

    A blank tag means non-continuous trading -- the closing auction and negotiated
    blocks -- where there is no meaningful aggressor, so those fall through to the
    inference like any other untagged print."""
    side = FLAG_SIDE.get(rec.get("flag"))
    if side is not None:
        return side, "flag"
    price = rec["price"]
    side = classify(price, bid, ask, last_price, last_side)
    if (ask is not None and price >= ask) or (bid is not None and price <= bid):
        return side, "quote"
    if last_price is not None and price != last_price:
        return side, "tick"
    return side, "carry"


# ============================================================
#  Order book
# ============================================================
class BookState:
    """Latest full snapshot per side (book frames are full per-side snapshots)."""

    def __init__(self):
        self.bids = {}        # price -> resting value (shares); lot = value/100
        self.asks = {}
        self.bid_freq = {}    # price -> order count
        self.ask_freq = {}

    def update(self, side, levels):
        if side == "OFFER":
            self.asks.clear(); self.ask_freq.clear()
            for price, freq, value in levels:
                self.asks[price] = value; self.ask_freq[price] = freq
        else:
            self.bids.clear(); self.bid_freq.clear()
            for price, freq, value in levels:
                self.bids[price] = value; self.bid_freq[price] = freq

    def best_bid(self):
        return max(self.bids) if self.bids else None

    def best_ask(self):
        return min(self.asks) if self.asks else None

    def spread(self):
        b, a = self.best_bid(), self.best_ask()
        return (a - b) if (b is not None and a is not None) else None

    def snapshot(self):
        """Merged {price: value} across both sides (prices don't overlap)."""
        return {**self.bids, **self.asks}


# ============================================================
#  Bar strategies — decide which footprint column a trade lands in
# ============================================================
class TimeBars:
    """Clock bars; bar id = bar start epoch (seconds)."""
    name = "time"

    def __init__(self, period_sec=60):
        self.period = period_sec

    def bar_for(self, rec, ep):
        return int(ep // self.period) * self.period


class TickBars:
    """N-trades-per-bar; bar id = sequential int."""
    name = "tick"

    def __init__(self, n=50):
        self.n = n
        self._count = 0
        self._bar = 0

    def bar_for(self, rec, ep):
        b = self._bar
        self._count += 1
        if self._count >= self.n:
            self._count = 0
            self._bar += 1
        return b


class VolumeBars:
    """N-lots-per-bar (1 lot = 100 shares); bar id = sequential int."""
    name = "volume"

    def __init__(self, lots=1000):
        self.cap = lots * 100
        self._acc = 0.0
        self._bar = 0

    def bar_for(self, rec, ep):
        b = self._bar
        self._acc += rec["qty"]
        if self._acc >= self.cap:
            self._acc = 0.0
            self._bar += 1
        return b


def make_bars(kind, size):
    if kind == "time":
        return TimeBars(size)
    if kind == "tick":
        return TickBars(int(size))
    if kind == "volume":
        return VolumeBars(int(size))
    raise ValueError(f"unknown bar kind {kind!r}")


# ============================================================
#  The aggregate model
# ============================================================
class OrderflowModel:
    """Stateful aggregator. Drive it with on_event(); read the public dicts/lists.
    Rebuild from scratch (new instance) when the bar strategy changes."""

    def __init__(self, bar_strategy=None, heatmap_every_sec=0.0, heatmap_max=1500):
        self.bars = bar_strategy or TimeBars(60)
        self.book = BookState()
        self.footprint = {}     # bar_id -> {price -> {'buy':v,'sell':v}}
        self.bar_meta = {}      # bar_id -> {'t0':ep,'t1':ep,'n':int}
        self.vap = {}           # price -> {'buy':v,'sell':v}
        self.cvd_x = []         # epochs
        self.cvd_y = []         # cumulative signed volume
        self.heatmap = []       # [(epoch, {price: value}, mid_price), ...]
        self.trades = []        # raw trade records (for the tape)
        self.gaps = []          # recorded holes in the tape (see feed.gap_record)
        # order-size analytics: shares traded at each price since that side's last
        # book snapshot, and the reload evidence that falls out of comparing the
        # two. Thresholds are applied at DISPLAY time (see reload_rows) so tuning
        # them never needs a model rebuild.
        self._consumed = Counter()
        self.reloads = {}       # price -> {count, shares, consumed, freq_delta, last}
        self.summary = None     # latest session OHLC/turnover dict
        self._cvd = 0.0
        self._last_price = None
        self._last_side = "buy"
        self._seen_ids = set()   # dedupe trades across preloaded history + live backfill
        self._heatmap_every = heatmap_every_sec
        self._last_heatmap_t = None
        self._heatmap_max = heatmap_max   # cap columns so a long session stays bounded
        # --debug counters: catch buy/sell skew, book desync, dedup, heatmap runaway
        self._diag = {"book_frames": 0, "trade_frames": 0, "summary_frames": 0,
                      "dedup_skips": 0, "no_book": 0, "crossed_book": 0,
                      "heatmap_trimmed": 0, "cls_flag": 0, "cls_quote": 0,
                      "cls_tick": 0, "cls_carry": 0, "buys": 0, "sells": 0}
        self._spread_hist = Counter()    # spread value -> frames; for the distribution

    # ---- event intake ----
    def on_event(self, ev):
        if ev[0] == "book":
            self._on_book(ev)
        elif ev[0] == "trade":
            self._on_trade(ev[1])
        elif ev[0] == "summary":
            self.summary = ev[1]
            self._diag["summary_frames"] += 1
        elif ev[0] == "gap":
            self.gaps.append(ev[1])

    def _detect_reloads(self, side, levels, ep):
        """Compare the incoming side against the state it is about to replace.

        A level that was hit and topped back up inside one snapshot shows NO net
        change, so "did it grow?" is the wrong test. Measure against what should
        have been left instead:

            expected = old_value - consumed_by_trades
            reload   = new_value - expected

        The order count decides what a reload means: roughly unchanged implies one
        order reloading, a jump implies new participants arriving. Without order
        ids neither can be proven -- this is evidence, not fact."""
        old_val = self.book.asks if side == "OFFER" else self.book.bids
        old_frq = self.book.ask_freq if side == "OFFER" else self.book.bid_freq
        for price, freq, value in levels:
            consumed = self._consumed.pop(price, 0.0)
            if consumed <= 0 or price not in old_val:
                continue
            expected = old_val[price] - consumed
            refill = value - expected
            if refill <= 0:
                continue                      # ordinary consumption, nothing added back
            e = self.reloads.get(price)
            if e is None:
                e = self.reloads[price] = {"count": 0, "shares": 0.0,
                                           "consumed": 0.0, "freq_delta": 0,
                                           "first": ep, "last": ep}
            e["count"] += 1
            e["shares"] += refill
            e["consumed"] += consumed
            e["freq_delta"] += freq - old_frq.get(price, freq)
            e["last"] = ep

    def _on_book(self, ev):
        _, _sym, side, levels, ts = ev
        ep_book = _parse_iso_epoch(ts) or 0.0
        self._detect_reloads(side, levels, ep_book)   # BEFORE the state is replaced
        self.book.update(side, levels)
        self._diag["book_frames"] += 1
        bb, ba = self.book.best_bid(), self.book.best_ask()
        if bb is not None and ba is not None:
            if bb >= ba:
                # best bid >= best ask. Per-side snapshots interleave, so a few transient
                # crosses are normal (stale opposite side); a *spike* means a real desync.
                self._diag["crossed_book"] += 1
            else:
                self._spread_hist[ba - bb] += 1   # real (positive) spread only
        ep = ep_book
        if (self._heatmap_every <= 0 or self._last_heatmap_t is None
                or ep - self._last_heatmap_t >= self._heatmap_every):
            mid = (bb + ba) / 2 if (bb is not None and ba is not None) else self._last_price
            self.heatmap.append((ep, self.book.snapshot(), mid))
            self._last_heatmap_t = ep
            if len(self.heatmap) > self._heatmap_max:
                del self.heatmap[0]
                self._diag["heatmap_trimmed"] += 1

    def _on_trade(self, rec):
        tid = rec.get("id")
        if tid is not None:
            if tid in self._seen_ids:
                self._diag["dedup_skips"] += 1
                return
            self._seen_ids.add(tid)
        price, qty = rec["price"], rec["qty"]
        bid, ask = self.book.best_bid(), self.book.best_ask()
        side, source = aggressor(rec, bid, ask, self._last_price, self._last_side)
        self._diag["trade_frames"] += 1
        self._diag["buys" if side == "buy" else "sells"] += 1
        if bid is None or ask is None:
            self._diag["no_book"] += 1
        self._diag["cls_" + source] += 1     # flag | quote | tick | carry
        self._last_side, self._last_price = side, price
        ep = trade_epoch(rec) or 0.0

        bar = self.bars.bar_for(rec, ep)
        cell = self.footprint.setdefault(bar, {}).setdefault(
            price, {"buy": 0.0, "sell": 0.0})
        cell[side] += qty

        m = self.bar_meta.get(bar)
        if m is None:                          # o/c = first/last trade price (candle)
            self.bar_meta[bar] = {"t0": ep, "t1": ep, "n": 1, "o": price, "c": price}
        else:
            m["n"] += 1
            # timestamp-robust: correct even if a backfill arrives out of order
            if ep < m["t0"]:
                m["t0"] = ep; m["o"] = price
            if ep >= m["t1"]:
                m["t1"] = ep; m["c"] = price

        vp = self.vap.setdefault(price, {"buy": 0.0, "sell": 0.0})
        vp[side] += qty

        self._consumed[price] += qty      # for reload detection on the next snapshot
        self._cvd += qty if side == "buy" else -qty
        self.cvd_x.append(ep)
        self.cvd_y.append(self._cvd)

        rec = dict(rec)
        rec["side"] = side
        self.trades.append(rec)

    # ---- derived views for the GUI ----
    def bar_ids(self):
        return sorted(self.footprint)

    def bar_cells(self, bar_id):
        """[(price, buy, sell, delta, total), ...] ascending price, + POC price."""
        cells = self.footprint.get(bar_id, {})
        rows = []
        poc_price, poc_vol = None, -1.0
        for price in sorted(cells):
            buy = cells[price]["buy"]
            sell = cells[price]["sell"]
            total = buy + sell
            rows.append((price, buy, sell, buy - sell, total))
            if total > poc_vol:
                poc_vol, poc_price = total, price
        return rows, poc_price

    def vap_rows(self):
        """[(price, buy, sell, total), ...] ascending price."""
        out = []
        for price in sorted(self.vap):
            b, s = self.vap[price]["buy"], self.vap[price]["sell"]
            out.append((price, b, s, b + s))
        return out

    def vap_for_bars(self, bar_ids):
        """Volume profile restricted to `bar_ids` — the visible-range profile.
        Same row shape as vap_rows(); summing over every bar id reproduces it
        exactly (the footprint and vap are fed from the same trades)."""
        agg = {}
        for b in bar_ids:
            for price, cell in self.footprint.get(b, {}).items():
                e = agg.setdefault(price, {"buy": 0.0, "sell": 0.0})
                e["buy"] += cell["buy"]
                e["sell"] += cell["sell"]
        return [(p, v["buy"], v["sell"], v["buy"] + v["sell"])
                for p, v in sorted(agg.items())]

    def total_volume(self):
        return sum(b + s for v in self.vap.values()
                   for b, s in [(v["buy"], v["sell"])])

    def vwap(self):
        """Session VWAP — the summary's avg if present, else computed from VAP."""
        if self.summary and self.summary.get("avg"):
            return self.summary["avg"]
        num = den = 0.0
        for p, v in self.vap.items():
            q = v["buy"] + v["sell"]
            num += p * q
            den += q
        return num / den if den else None

    def value_area(self, coverage=0.70):
        """(VAL, POC, VAH) prices — the band holding `coverage` of volume,
        grown outward from the POC by the heavier neighbour. Or None."""
        vol = {p: v["buy"] + v["sell"] for p, v in self.vap.items()}
        prices = sorted(vol)
        total = sum(vol.values())
        if not prices or total <= 0:
            return None
        poc = max(prices, key=lambda p: vol[p])
        lo = hi = prices.index(poc)
        acc, target = vol[poc], total * coverage
        while acc < target and (lo > 0 or hi < len(prices) - 1):
            below = vol[prices[lo - 1]] if lo > 0 else -1
            above = vol[prices[hi + 1]] if hi < len(prices) - 1 else -1
            if above >= below:
                hi += 1; acc += vol[prices[hi]]
            else:
                lo -= 1; acc += vol[prices[lo]]
        return prices[lo], poc, prices[hi]

    def two_sided_ladder(self, depth=14):
        """Stockbit-style book: (bid_rows, ask_rows), each [(price, freq, value), ...]
        best-first (bids high->low, asks low->high). lot = value/100."""
        b = self.book
        bids = sorted(b.bids, reverse=True)[:depth]
        asks = sorted(b.asks)[:depth]
        return ([(p, b.bid_freq.get(p), b.bids.get(p)) for p in bids],
                [(p, b.ask_freq.get(p), b.asks.get(p)) for p in asks])

    def bar_deltas(self):
        """[(bar_id, delta, cum_delta), ...] in bar order; delta = sum(buy-sell)."""
        out, cum = [], 0.0
        for bar in self.bar_ids():
            d = sum(delta for _p, _b, _s, delta, _t in self.bar_cells(bar)[0])
            cum += d
            out.append((bar, d, cum))
        return out

    def ladder(self, depth=20):
        """DOM around the touch, high->low:
        [(price, bid_freq, bid_value, ask_freq, ask_value), ...]. lot = value/100."""
        b = self.book
        prices = sorted(set(b.bids) | set(b.asks), reverse=True)
        bb, ba = b.best_bid(), b.best_ask()
        if bb is not None and ba is not None:
            mid = (bb + ba) / 2
            prices = sorted(prices, key=lambda p: abs(p - mid))[:depth * 2]
            prices = sorted(prices, reverse=True)
        return [(p, b.bid_freq.get(p), b.bids.get(p),
                 b.ask_freq.get(p), b.asks.get(p)) for p in prices]

    def coverage(self):
        """(fraction, lost_sec, span_sec) of the session actually captured, or None.

        Measured between the first and last trade rather than against clock hours:
        that excludes overnight and weekends for free, where a market calendar
        would need holidays and schedule changes kept correct forever to avoid
        reporting confident nonsense."""
        if not self.cvd_x:
            return None
        t0, t1 = self.cvd_x[0], self.cvd_x[-1]
        span = t1 - t0
        if span <= 0:
            return None
        lost = 0.0
        for g in self.gaps:
            gs = _parse_iso_epoch(g.get("started")) or 0.0
            ge = _parse_iso_epoch(g.get("ended")) or gs
            lo, hi = max(gs, t0), min(ge, t1)     # clip to the session
            if hi > lo:
                lost += hi - lo
        return (max(0.0, span - lost) / span, lost, span)

    def order_size_rows(self, depth=20):
        """[(price, side, avg_lots, freq, lots)] around the touch.

        avg = value / freq: the average size of ONE resting order at that price.
        A level holding 4,000 lots across 8 orders is a different animal from the
        same 4,000 across 400, and depth alone cannot tell them apart."""
        b = self.book
        out = []
        for price, _bf, bv, _af, av in self.ladder(depth):
            for value, freq, side in ((bv, b.bid_freq.get(price), "bid"),
                                      (av, b.ask_freq.get(price), "ask")):
                if not value:
                    continue
                lots = value / 100.0
                avg = (value / freq / 100.0) if freq else lots   # freq 0 -> treat as one
                out.append((price, side, avg, freq or 0, lots))
        return out

    def reload_rows(self, min_frac=0.5, freq_tol=3, min_count=2, window_sec=300):
        """Levels being topped back up after trades eat into them: replenished
        (hidden) liquidity, as [(price, count, lots, per_min, last_ep)].

        `min_frac`   the reload must be at least this share of what was consumed
        `freq_tol`   average order-count increase still consistent with ONE order
                     reloading rather than a crowd arriving
        `min_count`  how many times before it is worth showing
        `window_sec` only levels active this recently; 0 for the whole session

        Reported as a RATE as well as a total, because a cumulative count over a
        session is nearly tautological for a stock trading in a few ticks -- every
        traded price gets refilled eventually. Replenishment per minute is what
        separates a level being actively worked from one that merely existed.

        Filtering happens here, not during detection, so changing the thresholds
        never requires rebuilding the model.

        HEURISTIC, not proof: the feed is level-aggregated with no order ids, so
        one order reloading and one order leaving while a similar one arrives are
        indistinguishable."""
        now = max((e["last"] for e in self.reloads.values()), default=0.0)
        out = []
        for price, e in self.reloads.items():
            if e["count"] < min_count or e["consumed"] <= 0:
                continue
            if window_sec and now - e["last"] > window_sec:
                continue
            if e["shares"] < min_frac * e["consumed"]:
                continue
            if e["freq_delta"] / max(e["count"], 1) > freq_tol:
                continue                       # order count climbed: a crowd, not a reload
            span_min = max((e["last"] - e["first"]) / 60.0, 1 / 60.0)
            out.append((price, e["count"], e["shares"] / 100.0,
                        e["count"] / span_min, e["last"]))
        out.sort(key=lambda r: -r[3])          # busiest first
        return out

    def diag(self):
        """Diagnostics snapshot for the --debug readout: raw counters plus derived
        health (buy %, spread, heatmap depth, and the footprint==VAP invariant —
        fp_sh and vap_sh must agree or aggregation has a bug)."""
        d = dict(self._diag)
        tot = d["buys"] + d["sells"]
        d["buy_pct"] = (100.0 * d["buys"] / tot) if tot else 0.0
        bb, ba = self.book.best_bid(), self.book.best_ask()
        d["best_bid"], d["best_ask"] = bb, ba
        d["spread"] = (ba - bb) if (bb is not None and ba is not None) else None
        d["heatmap_cols"] = len(self.heatmap)
        ids = self.bar_ids()
        d["bars"] = len(ids)
        ns = [self.bar_meta[b]["n"] for b in ids if b in self.bar_meta]
        d["tpb_mean"] = (sum(ns) / len(ns)) if ns else 0.0     # mean trades / bar
        d["tpb_last"] = ns[-1] if ns else 0                    # trades in the latest bar
        d["spread_med"] = _weighted_pct(self._spread_hist, 50)
        d["spread_p90"] = _weighted_pct(self._spread_hist, 90)
        d["spread_mode"] = (self._spread_hist.most_common(1)[0][0]
                            if self._spread_hist else None)
        d["reloads"] = len(self.reload_rows())
        d["gaps"] = len(self.gaps)
        cov = self.coverage()
        d["coverage"] = None if cov is None else 100.0 * cov[0]
        d["gap_sec"] = 0.0 if cov is None else cov[1]
        d["vap_sh"] = self.total_volume()
        d["fp_sh"] = sum(c["buy"] + c["sell"]
                         for cells in self.footprint.values() for c in cells.values())
        return d

    @staticmethod
    def _variance_ratio(rets, q):
        """Lo-MacKinlay VR(q) = Var(q-period return) / (q * Var(1-period return)).
        >1 trending (positive autocorrelation), <1 mean-reverting, ~1 random walk."""
        n = len(rets)
        if q < 2 or n < 2 * q:
            return None
        mu = sum(rets) / n
        var1 = sum((r - mu) ** 2 for r in rets) / (n - 1)
        if var1 <= 0:
            return None
        qsums = [sum(rets[i:i + q]) for i in range(n - q + 1)]
        m = len(qsums)
        muq = q * mu
        varq = sum((s - muq) ** 2 for s in qsums) / (m - 1)
        return (varq / q) / var1

    def tick_size(self):
        """Instrument tick, estimated from the book ladder (dense and exactly
        tick-spaced by the exchange) — robust when the day's traded prices are
        sparse or contain off-tick negotiated prints. Falls back to traded-price
        gaps (ties broken toward the LARGER gap: off-tick prints create small
        bogus gaps). None if fewer than 2 prices exist anywhere."""
        prices = sorted(set(self.book.bids) | set(self.book.asks))
        if len(prices) < 3:
            prices = sorted(self.vap)
        if len(prices) < 2:
            return None
        gaps = Counter(round(b - a, 6) for a, b in zip(prices, prices[1:]))
        return max(gaps.items(), key=lambda kv: (kv[1], kv[0]))[0]

    def er_series(self, window=20):
        """[(bar_index, ER)] rolling Kaufman Efficiency Ratio over the bar closes —
        the regime panel's history curve. Index aligns with the footprint x-axis."""
        closes = [self.bar_meta[b]["c"] for b in self.bar_ids()]
        out = []
        for i in range(window, len(closes)):
            seg = closes[i - window:i + 1]
            net = abs(seg[-1] - seg[0])
            path = sum(abs(seg[j] - seg[j - 1]) for j in range(1, len(seg)))
            out.append((i, net / path if path > 0 else 0.0))
        return out

    def regime(self, window=20, warmup_min=20, er_trend=0.5, er_chop=0.3, vr_q=4):
        """Intraday regime read from the bar closes: Kaufman Efficiency Ratio (trend
        vs chop) + a realized-vol percentile (hi/lo vol), with a warm-up gate so the
        window is fully populated with real bars AND >= warmup_min of session before
        anything is computed (ER/RV on a half-window are noise). Returns a dict with
        'ready'; when False only {ready, label='WARMUP', bars, span} are meaningful."""
        ids = self.bar_ids()
        n = len(ids)
        if n == 0:
            return {"ready": False, "label": "WARMUP", "bars": 0, "span": 0.0}
        span = self.bar_meta[ids[-1]]["t1"] - self.bar_meta[ids[0]]["t0"]
        if n < window + 1 or span < warmup_min * 60:
            return {"ready": False, "label": "WARMUP", "bars": n, "span": span}

        closes = [self.bar_meta[b]["c"] for b in ids]
        seg = closes[-(window + 1):]                       # Efficiency Ratio
        net = abs(seg[-1] - seg[0])
        path = sum(abs(seg[i] - seg[i - 1]) for i in range(1, len(seg)))
        er = (net / path) if path > 0 else 0.0
        direction = 1 if seg[-1] >= seg[0] else -1

        rets = [math.log(closes[i] / closes[i - 1])        # per-bar log returns
                for i in range(1, len(closes))
                if closes[i] > 0 and closes[i - 1] > 0]

        def rv_at(k):                                      # stdev of the window ending at k
            w = rets[k - window + 1:k + 1]
            if len(w) < 2:
                return 0.0
            mu = sum(w) / len(w)
            return (sum((x - mu) ** 2 for x in w) / (len(w) - 1)) ** 0.5

        rv = rv_at(len(rets) - 1)
        hist = [rv_at(k) for k in range(window - 1, len(rets))]
        rv_pct = (100.0 * sum(1 for h in hist if h <= rv) / len(hist)) if hist else 50.0
        vol_state = "HI VOL" if rv_pct >= 70 else "LO VOL" if rv_pct <= 30 else "MID VOL"

        if er >= er_trend:
            core = "TREND↑" if direction >= 0 else "TREND↓"
        elif er <= er_chop:
            core = "CHOP"
        else:
            core = "MIXED"
        vr = self._variance_ratio(rets[-max(2 * vr_q, window):], vr_q)
        return {"ready": True, "er": er, "direction": direction, "rv": rv,
                "rv_pct": rv_pct, "vol_state": vol_state, "core": core,
                "label": f"{core} · {vol_state}", "vr": vr, "bars": n, "span": span}


# ============================================================
#  Heatmap grid — the columns as something you can actually draw
# ============================================================
class HeatGrid:
    """OrderflowModel.heatmap as a level matrix, kept in step incrementally.

    levels[i, j] is the resting size at ladder[i] in column j, and it carries
    three distinct meanings that the old pixel grid collapsed into one:
      NaN  outside that column's visible depth — the feed did not show it, which
           is NOT the same as "nobody is there"
      0    a price inside the visible depth with nothing resting
      > 0  resting size

    Display rows come from rows(): each level paints a band of price around
    itself, so a tick-2 level covers two tick-1 rows. On IDX the tick changes
    at 200/500/2000/5000, and the old grid used one global minimum tick — on
    BUMI every odd row above 200 was a price that cannot exist, permanently
    empty, which striped the heatmap and let smoothing halve each real level.
    The y-axis stays in price, so the heatmap still links to the footprint.

    update() mirrors model.heatmap: columns are strictly epoch-increasing, the
    model only appends at the back and trims at the front, so most refreshes
    append one column or none. A full rebuild happens only when a new price
    enters the ladder or the column list is not the one we were mirroring.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.ladder = []
        self._index = {}
        self.eps = []
        self.mids = []
        self.snaps = []
        self.levels = np.zeros((0, 0))
        self.rebuilds = 0
        self.version = 0          # bumps whenever levels change; lets the panel skip work
        self._bands = None
        self._rows = None

    # ---------------- mirroring ----------------
    def update(self, cols):
        """Bring the grid in line with cols. Returns 'rebuilt', 'appended',
        'trimmed' or 'unchanged'."""
        if not cols:
            if self.eps:
                self.reset()
                self.version += 1
                return "rebuilt"
            return "unchanged"
        k = bisect_left(self.eps, cols[0][0])          # front columns the model dropped
        keep = len(self.eps) - k
        same = (keep > 0 and keep <= len(cols)
                and cols[0][0] == self.eps[k] and cols[keep - 1][0] == self.eps[-1])
        if not same:
            return self._rebuild(cols)
        new = cols[keep:]
        if any(p not in self._index for _e, s, _m in new for p in s):
            return self._rebuild(cols)
        if k == 0 and not new:
            return "unchanged"
        block = np.full((len(self.ladder), len(new)), np.nan)
        for j, (_e, s, _m) in enumerate(new):
            self._fill(block, j, s)
        self.levels = np.hstack([self.levels[:, k:], block]) if self.levels.size else block
        self.eps = self.eps[k:] + [e for e, _s, _m in new]
        self.mids = self.mids[k:] + [m for _e, _s, m in new]
        self.snaps = self.snaps[k:] + [s for _e, s, _m in new]
        self.version += 1
        return "appended" if new else "trimmed"

    def _rebuild(self, cols):
        self.ladder = sorted({p for _e, s, _m in cols for p in s})
        self._index = {p: i for i, p in enumerate(self.ladder)}
        self.levels = np.full((len(self.ladder), len(cols)), np.nan)
        for j, (_e, s, _m) in enumerate(cols):
            self._fill(self.levels, j, s)
        self.eps = [e for e, _s, _m in cols]
        self.mids = [m for _e, _s, m in cols]
        self.snaps = [s for _e, s, _m in cols]
        self._bands = self._rows = None
        self.rebuilds += 1
        self.version += 1
        return "rebuilt"

    def _fill(self, arr, j, snap):
        if not snap:
            return                                   # no book at all: whole column unknown
        idx = [self._index[p] for p in snap]
        arr[min(idx):max(idx) + 1, j] = 0.0          # inside visible depth: known
        for p, v in snap.items():
            arr[self._index[p], j] = v

    # ---------------- geometry ----------------
    def bands(self):
        """(lo, hi) price edges of each ladder level's display band.

        Neighbours one exchange tick apart meet at their midpoint. A gap larger
        than any tick seen at least twice is a hole — a valid price nobody
        quoted all window — and is left unpainted rather than smeared over."""
        if self._bands is not None:
            return self._bands
        lad = np.asarray(self.ladder, dtype=float)
        n = len(lad)
        if n == 0:
            self._bands = (lad, lad)
            return self._bands
        if n == 1:
            self._bands = (lad - 0.5, lad + 0.5)
            return self._bands
        gaps = np.diff(lad)
        vals, counts = np.unique(np.round(gaps, 6), return_counts=True)
        frequent = vals[counts >= 2]
        adj_max = frequent.max() if frequent.size else gaps.max()
        common = vals[counts.argmax()]
        adjacent = gaps <= adj_max + 1e-9
        tick = np.empty(n)
        for i in range(n):
            near = [gaps[g] for g in (i - 1, i) if 0 <= g < n - 1 and adjacent[g]]
            tick[i] = min(near) if near else common
        lo, hi = lad - tick / 2, lad + tick / 2
        for i in range(n - 1):
            if adjacent[i]:
                lo[i + 1] = hi[i] = (lad[i] + lad[i + 1]) / 2
        self._bands = (lo, hi)
        return self._bands

    def rows(self):
        """(y0, row_h, row2lvl): the display grid. Row r spans
        [y0 + r*row_h, y0 + (r+1)*row_h); row2lvl[r] is the ladder level that
        paints it, or -1 for a hole."""
        if self._rows is not None:
            return self._rows
        lo, hi = self.bands()
        if not len(lo):
            self._rows = (0.0, 1.0, np.zeros(0, dtype=int))
            return self._rows
        # Row height = the finest spacing the ladder actually uses. Band edges at
        # tick boundaries fall on half-ticks, but rows are assigned by centre, so
        # they need not divide the grid -- including them would halve every row.
        lad = np.rint(np.asarray(self.ladder) * 100).astype(np.int64)
        g = int(np.gcd.reduce(np.diff(lad))) if len(lad) > 1 else 100
        row_h = (g or 100) / 100.0
        first, span = float(self.ladder[0]), float(self.ladder[-1] - self.ladder[0])
        if span / row_h > 5000:                      # pathological ladder: coarsen
            row_h = float((hi - lo).min()) or 1.0
        # centres sit ON prices, so every level owns the row at its own price
        nrows = int(round(span / row_h)) + 1
        y0 = first - row_h / 2
        centres = first + np.arange(nrows) * row_h
        lvl = np.searchsorted(lo, centres, side="right") - 1
        ok = (lvl >= 0) & (centres < hi[np.clip(lvl, 0, len(hi) - 1)])
        self._rows = (y0, row_h, np.where(ok, lvl, -1))
        return self._rows

    def display(self, values):
        """values (ladder x cols, e.g. scaled levels) -> image rows x cols,
        NaN where nothing is known."""
        _y0, _h, row2lvl = self.rows()
        ext = np.vstack([values, np.full((1, values.shape[1]), np.nan)])
        return ext[row2lvl]                          # -1 picks the NaN row

    def level_at(self, price):
        """Ladder index whose band contains price, or None."""
        lo, hi = self.bands()
        if not len(lo):
            return None
        i = int(np.searchsorted(lo, price, side="right")) - 1
        return i if 0 <= i and price < hi[i] else None

    def value_at(self, col, price):
        """(level price, size or NaN, side, epoch) under the cursor, or None.
        side is 'bid' below the column's mid, 'ask' above, 'mid' at it."""
        if not self.eps:
            return None
        col = int(min(max(col, 0), len(self.eps) - 1))
        i = self.level_at(price)
        if i is None:
            return None
        p, v, mid = self.ladder[i], float(self.levels[i, col]), self.mids[col]
        side = "mid" if mid is None or p == mid else ("bid" if p < mid else "ask")
        return p, v, side, self.eps[col]

    def gaps(self, min_sec):
        """[(col, seconds)] where the next column is more than min_sec later —
        a disconnect, a quiet stretch or IDX's lunch break, which a column axis
        would otherwise compress into a single step."""
        if len(self.eps) < 2:
            return []
        d = np.diff(np.asarray(self.eps, dtype=float))
        return [(int(j), float(d[j])) for j in np.flatnonzero(d > min_sec)]

    # ---------------- walls ----------------
    def persistent_walls(self, mult=3.0, min_secs=30.0, blink=2):
        """Levels holding >= mult x the column's median resting size for at
        least min_secs, tolerating `blink` missing columns. Computed on levels,
        not display rows, so a tick-2 wall is one wall, not two.

        Returns [{price, j0, j1, size, side, alive}] where size is the size at
        the wall's last column and alive means it still stands at the right edge.
        mult is the DOM's wall_mult, so "a wall" means the same in both panels."""
        L = self.levels
        if not L.size:
            return []
        # an empty column's median is NaN, which compares False below -- exactly
        # right; silence numpy's "All-NaN slice" RuntimeWarning (errstate does
        # not cover it: it is a warnings.warn, not a floating-point error)
        with np.errstate(all="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            pos = np.where(L > 0, L, np.nan)
            med = np.nanmedian(pos, axis=0) if np.isfinite(pos).any() else None
        if med is None:
            return []
        with np.errstate(invalid="ignore"):
            big = L >= mult * med[None, :]
        eps = np.asarray(self.eps, dtype=float)
        last = L.shape[1] - 1
        out = []
        for i in range(L.shape[0]):
            cols = np.flatnonzero(big[i])
            if not cols.size:
                continue
            br = np.flatnonzero(np.diff(cols) > blink + 1)
            for a, b in zip(np.r_[cols[0], cols[br + 1]], np.r_[cols[br], cols[-1]]):
                if eps[b] - eps[a] < min_secs:
                    continue
                p, mid = self.ladder[i], self.mids[b]
                out.append({"price": p, "j0": int(a), "j1": int(b),
                            "size": float(L[i, b]),
                            "side": None if mid is None else ("bid" if p < mid else "ask"),
                            "alive": b >= last - blink})
        return out


def heat_scale(levels, mode="equalize", gamma=3.0, pctile=97):
    """Map resting sizes to colour values. Returns (scaled, hi, order):
    scaled has the same shape with NaN preserved, the colour range is [0, hi],
    and order is the sorted non-zero sizes (what heat_unscale inverts against).

    equalize ranks sizes, so colour = size percentile ** gamma: the field stays
    readable whatever the size distribution, and only the top few percent reach
    the bright end."""
    nz = levels[levels > 0]
    order = np.sort(nz) if nz.size else np.zeros(0)
    with np.errstate(invalid="ignore"):
        if mode == "equalize":
            if order.size:
                ranks = np.searchsorted(order, levels, side="right") / len(order)
                scaled = np.where(levels > 0, ranks ** gamma, levels)   # 0 stays 0, NaN stays NaN
            else:
                scaled = levels.copy()
            return scaled, 1.0, order
        if mode == "log":
            scaled = np.log1p(levels)
        elif mode == "sqrt":
            scaled = np.sqrt(levels)
        else:
            scaled = levels.astype(float, copy=True)
    snz = scaled[scaled > 0]
    hi = float(np.percentile(snz, pctile)) if snz.size else 1.0
    return scaled, hi or 1.0, order


def heat_unscale(value, mode="equalize", gamma=3.0, order=None):
    """Colour value -> resting size: the inverse of heat_scale, so the legend can
    label its colours in lots rather than in percentile-to-the-gamma."""
    v = max(float(value), 0.0)
    if mode == "equalize":
        if order is None or not len(order):
            return 0.0
        return float(np.quantile(order, min(v, 1.0) ** (1.0 / gamma)))
    if mode == "log":
        return float(math.expm1(v))
    if mode == "sqrt":
        return v * v
    return v


def build_model(events, bar_kind="time", bar_size=60, heatmap_every_sec=0.0, heatmap_max=1500):
    """Convenience: run an iterable of events through a fresh model."""
    m = OrderflowModel(make_bars(bar_kind, bar_size), heatmap_every_sec, heatmap_max)
    for ev in events:
        m.on_event(ev)
    return m
