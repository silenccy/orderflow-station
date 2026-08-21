"""
model.py — orderflow state + aggregation (pure Python/numpy, no Qt).

Feed it the ('book'|'trade', ...) events from of_feed (live or replay) and it
maintains everything the chart panels need:
  - BookState     : current bid/ask ladders, best bid/ask, spread
  - Footprint     : (bar, price) -> {buy, sell} volume, per pluggable bar strategy
  - CVD           : cumulative signed volume series
  - VolumeAtPrice : price -> {buy, sell} volume (volume profile)
  - HeatmapBuffer : rolling [price x time] resting-size columns

Trade classification is the quote rule vs the synced book at trade time, with a
tick-rule fallback inside the spread (Lee-Ready style). Isolated in classify().
"""

import math
from collections import Counter
from datetime import datetime


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
                      "heatmap_trimmed": 0, "cls_quote": 0, "cls_tick": 0,
                      "cls_carry": 0, "buys": 0, "sells": 0}
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

    def _on_book(self, ev):
        _, _sym, side, levels, ts = ev
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
        ep = _parse_iso_epoch(ts) or 0.0
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
        side = classify(price, bid, ask, self._last_price, self._last_side)
        # mirror classify()'s decision to record which rule fired (before _last_price moves)
        self._diag["trade_frames"] += 1
        self._diag["buys" if side == "buy" else "sells"] += 1
        if bid is None or ask is None:
            self._diag["no_book"] += 1
        if (ask is not None and price >= ask) or (bid is not None and price <= bid):
            self._diag["cls_quote"] += 1
        elif self._last_price is not None and price != self._last_price:
            self._diag["cls_tick"] += 1
        else:
            self._diag["cls_carry"] += 1
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


def build_model(events, bar_kind="time", bar_size=60, heatmap_every_sec=0.0, heatmap_max=1500):
    """Convenience: run an iterable of events through a fresh model."""
    m = OrderflowModel(make_bars(bar_kind, bar_size), heatmap_every_sec, heatmap_max)
    for ev in events:
        m.on_event(ev)
    return m
