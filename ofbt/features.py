"""Per-second features for strategies. Every value at index i uses only data known at the END of second i (the book
snapshot at i and trades with timestamps inside seconds <= i), so a strategy that reads index i cannot see the future.
tests/test_engine.py checks this by recomputing every feature on a truncated session and comparing the prefix.

All features are numpy arrays of length n (one per second) and are computed once and cached per session.
Prices are in index points (not ticks).
"""
import numpy as np


class Features:
    def __init__(self, B):
        self.B = B
        self.n = n = len(B["t"])
        self.tick = tk = float(B.get("tick", 0.25))
        bb, ba = B["bb"].astype(float), B["ba"].astype(float)
        ok = (bb >= 0) & (ba >= 0)
        self.bid = np.where(ok, bb, np.nan) * tk
        self.ask = np.where(ok, ba, np.nan) * tk
        self.mid = (self.bid + self.ask) / 2
        self.spread_ticks = np.where(ok, ba - bb, np.nan)
        t0 = int(B["t"][0])
        sec = B["tr_ns"] // 1_000_000_000 - t0
        m = (sec >= 0) & (sec < n)
        sec, sz, sd, px = sec[m], B["tr_sz"][m].astype(float), B["tr_sd"][m], B["tr_tk"][m].astype(float) * tk
        self.vol = np.bincount(sec, weights=sz, minlength=n)[:n]
        self.buy_vol = np.bincount(sec, weights=sz * (sd > 0), minlength=n)[:n]
        self.sell_vol = np.bincount(sec, weights=sz * (sd < 0), minlength=n)[:n]
        self.delta = self.buy_vol - self.sell_vol
        self.cvd = np.cumsum(self.delta)
        pv = np.cumsum(np.bincount(sec, weights=sz * px, minlength=n)[:n])
        cv = np.cumsum(self.vol)
        self.vwap = np.where(cv > 0, pv / np.maximum(cv, 1), self.mid)
        last = np.full(n, np.nan)                 # last trade price in each second, carried forward
        if len(sec):
            idx = np.r_[np.nonzero(np.diff(sec))[0], len(sec) - 1]
            last[sec[idx]] = px[idx]
        self.last = _ffill(last, self.mid)
        self._c = {}

    # ---------------------------------------------------------------- windowed features (cached by arguments)
    def _cached(self, key, fn):
        if key not in self._c:
            self._c[key] = fn()
        return self._c[key]

    def delta_sum(self, w):
        """Aggressor delta (buy volume - sell volume) over the last w seconds, including the current one."""
        return self._cached(("delta", w), lambda: _rolling_sum(self.delta, w))

    def vol_sum(self, w):
        return self._cached(("vol", w), lambda: _rolling_sum(self.vol, w))

    def past_pctl(self, name, w, lookback, pctl, absolute=True):
        """Percentile of a windowed series over the previous `lookback` seconds (i-lookback .. i-1: i itself excluded)."""
        def f():
            x = getattr(self, name)(w)
            x = np.abs(x) if absolute else x
            out = np.full(self.n, np.nan)
            for i in range(min(lookback, self.n), self.n):
                out[i] = np.percentile(x[i - lookback:i], pctl)
            for i in range(60, min(lookback, self.n)):
                out[i] = np.percentile(x[:i], pctl)
            return out
        return self._cached(("pctl", name, w, lookback, pctl, absolute), f)

    def imbalance(self, levels):
        """(bid size - ask size) / (sum) over the best `levels` price levels of the book."""
        def f():
            b = self.B["bid_lad"][:, :levels].sum(1).astype(float)
            a = self.B["ask_lad"][:, :levels].sum(1).astype(float)
            return np.where(a + b > 0, (b - a) / np.maximum(a + b, 1), 0.0)
        return self._cached(("imb", levels), f)

    def high(self, w):
        """Highest trade or mid price over the last w seconds."""
        return self._cached(("hi", w), lambda: _rolling_ext(np.fmax(self.last, self.mid), w, np.maximum))

    def low(self, w):
        return self._cached(("lo", w), lambda: _rolling_ext(np.fmin(self.last, self.mid), w, np.minimum))

    def mid_std(self, w):
        """Standard deviation of the mid price over the last w seconds."""
        def f():
            m = _ffill(self.mid, np.full(self.n, np.nanmean(self.mid)))
            s1, s2 = _rolling_sum(m, w), _rolling_sum(m * m, w)
            k = np.minimum(np.arange(1, self.n + 1), w)
            return np.sqrt(np.maximum(s2 / k - (s1 / k) ** 2, 0))
        return self._cached(("std", w), f)

    def ema(self, span):
        def f():
            m = _ffill(self.mid, np.full(self.n, np.nanmean(self.mid)))
            a, out, v = 2.0 / (span + 1), np.empty(self.n), m[0]
            for i, x in enumerate(m):
                v += a * (x - v)
                out[i] = v
            return out
        return self._cached(("ema", span), f)


def _rolling_sum(x, w):
    c = np.concatenate([[0.0], np.cumsum(x)])
    i = np.arange(1, len(x) + 1)
    return c[i] - c[np.maximum(i - w, 0)]


def _rolling_ext(x, w, op):
    out = x.copy()
    k = 1
    while k < w:                                  # doubling trick: O(n log w)
        s = min(k, w - k)
        sh = np.concatenate([np.full(s, np.nan), out[:-s]])
        out = np.where(np.isnan(sh), out, op(out, sh))
        k += s
    return out


def _ffill(x, fallback):
    x = x.copy()
    bad = np.isnan(x)
    if bad.all():
        return fallback.copy()
    idx = np.where(~bad, np.arange(len(x)), 0)
    np.maximum.accumulate(idx, out=idx)
    out = x[idx]
    first = np.argmax(~bad)
    out[:first] = x[first]
    return out
