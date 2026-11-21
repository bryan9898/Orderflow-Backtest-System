"""Example strategies. These are generic textbook ideas, written to show the engine - not anyone's trading strategy,
and none of them was tuned. Add your own by dropping a file in user_strategies/ (see user_strategies/README.md).

Each one reads only ctx (the present and the past) and sends bracket orders with ctx.buy / ctx.sell.
"""
import importlib.util
import os
import random

import numpy as np

from .engine import Param, Strategy


def _bracket():
    return dict(stop_pts=Param(8.0, 1.0, 40.0, 0.5, "stop (pts)"), target_pts=Param(16.0, 1.0, 80.0, 0.5, "target (pts)"),
                max_hold_s=Param(600, 30, 7200, 30, "max hold (s)"))


class DeltaMomentum(Strategy):
    name = "delta_momentum"
    title = "Delta momentum"
    description = ("Follow aggressive flow when the book agrees: buy when the last-N-seconds aggressor delta is above its "
                   "own recent percentile and the top-of-book depth leans bid (mirror for sells). The default settings are "
                   "the rule in the static report.")
    params = dict(window_s=Param(30, 5, 300, 5, "delta window (s)"), pctl=Param(95.0, 50.0, 99.9, 0.5, "delta percentile"),
                  lookback_s=Param(1800, 300, 3600, 300, "percentile lookback (s)"), levels=Param(5, 1, 10, 1, "book levels"),
                  imb_min=Param(0.20, 0.0, 0.9, 0.05, "min book imbalance"), warmup_s=Param(600, 0, 3600, 60, "warm-up (s)"),
                  cooldown_s=Param(120, 0, 1800, 30, "cooldown (s)"), **_bracket())

    def on_start(self, ctx):
        p = self.p
        self.d = ctx.F.delta_sum(p["window_s"])
        self.thr = ctx.F.past_pctl("delta_sum", p["window_s"], p["lookback_s"], p["pctl"])
        self.imb = ctx.F.imbalance(p["levels"])
        self.last = -10 ** 9

    def on_second(self, ctx):
        p, i = self.p, ctx.i
        if i < p["warmup_s"] or i - self.last < p["cooldown_s"]:
            return
        thr = self.thr[i]
        if not thr > 0:
            return
        side = 1 if self.d[i] >= thr and self.imb[i] >= p["imb_min"] else -1 if self.d[i] <= -thr and self.imb[i] <= -p["imb_min"] else 0
        if side:
            self.last = i
            (ctx.buy if side == 1 else ctx.sell)(p["stop_pts"], p["target_pts"], p["max_hold_s"], "delta")


class VwapReversion(Strategy):
    name = "vwap_reversion"
    title = "VWAP reversion"
    description = ("Fade stretches away from the session VWAP: sell when the mid is more than z standard deviations "
                   "(of the mid over the lookback) above VWAP, buy when below.")
    params = dict(z=Param(2.0, 0.5, 5.0, 0.25, "stretch (z)"), lookback_s=Param(1800, 300, 3600, 300, "std lookback (s)"),
                  warmup_s=Param(900, 0, 3600, 60, "warm-up (s)"), cooldown_s=Param(300, 0, 1800, 30, "cooldown (s)"),
                  stop_pts=Param(10.0, 1.0, 40.0, 0.5, "stop (pts)"), target_pts=Param(10.0, 1.0, 80.0, 0.5, "target (pts)"),
                  max_hold_s=Param(900, 30, 7200, 30, "max hold (s)"))

    def on_start(self, ctx):
        self.sd = ctx.F.mid_std(self.p["lookback_s"])
        self.last = -10 ** 9

    def on_second(self, ctx):
        p, i = self.p, ctx.i
        if i < p["warmup_s"] or i - self.last < p["cooldown_s"] or not self.sd[i] > 0:
            return
        z = (ctx.mid - ctx.F.vwap[i]) / self.sd[i]
        if abs(z) >= p["z"]:
            self.last = i
            (ctx.sell if z > 0 else ctx.buy)(p["stop_pts"], p["target_pts"], p["max_hold_s"], f"z {z:+.1f}")


class OpeningRange(Strategy):
    name = "opening_range"
    title = "Opening-range breakout"
    description = ("Mark the high and low of the first N minutes. Buy the first time the mid trades a buffer above the "
                   "range high, sell the first time it trades below the low. At most one trade each way per session.")
    params = dict(range_min=Param(15, 1, 60, 1, "range (min)"), buffer_ticks=Param(2, 0, 20, 1, "buffer (ticks)"),
                  stop_pts=Param(10.0, 1.0, 40.0, 0.5, "stop (pts)"), target_pts=Param(20.0, 1.0, 80.0, 0.5, "target (pts)"),
                  max_hold_s=Param(1800, 30, 7200, 30, "max hold (s)"))

    def on_start(self, ctx):
        self.done = set()
        self.hi = self.lo = None

    def on_second(self, ctx):
        p, i = self.p, ctx.i
        w = p["range_min"] * 60
        if i < w:
            return
        if self.hi is None:
            self.hi, self.lo = ctx.F.high(w)[i - 1], ctx.F.low(w)[i - 1]
        buf = p["buffer_ticks"] * ctx.F.tick
        if 1 not in self.done and ctx.mid >= self.hi + buf and ctx.flat:
            self.done.add(1); ctx.buy(p["stop_pts"], p["target_pts"], p["max_hold_s"], "OR high")
        elif -1 not in self.done and ctx.mid <= self.lo - buf and ctx.flat:
            self.done.add(-1); ctx.sell(p["stop_pts"], p["target_pts"], p["max_hold_s"], "OR low")


class QueueImbalance(Strategy):
    name = "queue_imbalance"
    title = "Top-of-book queue imbalance"
    description = ("A short-horizon microstructure idea: when the spread is one tick and the best bid queue holds most of "
                   "the size at the inside for several seconds in a row, expect the ask to be taken (mirror for sells).")
    params = dict(qi_min=Param(0.80, 0.55, 0.98, 0.01, "queue share"), persist_s=Param(3, 1, 30, 1, "persist (s)"),
                  warmup_s=Param(60, 0, 3600, 60, "warm-up (s)"), cooldown_s=Param(60, 0, 1800, 30, "cooldown (s)"),
                  stop_pts=Param(4.0, 1.0, 40.0, 0.25, "stop (pts)"), target_pts=Param(4.0, 1.0, 80.0, 0.25, "target (pts)"),
                  max_hold_s=Param(120, 10, 3600, 10, "max hold (s)"))

    def on_start(self, ctx):
        b, a = ctx.B["bid_lad"][:, 0].astype(float), ctx.B["ask_lad"][:, 0].astype(float)
        self.qi = np.where(a + b > 0, b / np.maximum(a + b, 1), 0.5)
        self.run = 0
        self.last = -10 ** 9

    def on_second(self, ctx):
        p, i = self.p, ctx.i
        q = self.qi[i] if ctx.F.spread_ticks[i] == 1 else 0.5
        side = 1 if q >= p["qi_min"] else -1 if q <= 1 - p["qi_min"] else 0
        self.run = self.run + side if side and (self.run == 0 or (self.run > 0) == (side > 0)) else side
        if i < p["warmup_s"] or i - self.last < p["cooldown_s"]:
            return
        if abs(self.run) >= p["persist_s"]:
            self.last = i
            (ctx.buy if self.run > 0 else ctx.sell)(p["stop_pts"], p["target_pts"], p["max_hold_s"], f"qi {q:.2f}")
            self.run = 0


class SweepFollow(Strategy):
    name = "sweep_follow"
    title = "Large-print follow"
    description = ("When one second's traded volume is far above normal (a percentile of the recent past) and that second's "
                   "delta is one-sided, trade in the direction of the aggressors.")
    params = dict(pctl=Param(99.5, 90.0, 99.99, 0.1, "volume percentile"), lookback_s=Param(1800, 300, 3600, 300, "lookback (s)"),
                  min_share=Param(0.75, 0.5, 1.0, 0.05, "aggressor share"), warmup_s=Param(600, 0, 3600, 60, "warm-up (s)"),
                  cooldown_s=Param(180, 0, 1800, 30, "cooldown (s)"), **_bracket())

    def on_start(self, ctx):
        self.thr = ctx.F.past_pctl("vol_sum", 1, self.p["lookback_s"], self.p["pctl"])
        self.last = -10 ** 9

    def on_second(self, ctx):
        p, i, F = self.p, ctx.i, ctx.F
        if i < p["warmup_s"] or i - self.last < p["cooldown_s"] or not self.thr[i] > 0 or F.vol[i] < self.thr[i]:
            return
        share = F.buy_vol[i] / F.vol[i]
        if share >= p["min_share"] or share <= 1 - p["min_share"]:
            self.last = i
            (ctx.buy if share > 0.5 else ctx.sell)(p["stop_pts"], p["target_pts"], p["max_hold_s"], f"{int(F.vol[i])} lots")


class RandomEntry(Strategy):
    name = "random_entry"
    title = "Random entry (baseline)"
    description = "A coin flip every N minutes with the same bracket. Any idea worth trading has to beat this."
    params = dict(every_min=Param(10, 1, 60, 1, "every (min)"), seed=Param(1, 1, 999, 1, "seed"), **_bracket())

    def on_start(self, ctx):
        self.rng = random.Random(self.p["seed"] * 1000 + int(ctx.B["t"][0]) % 1000)

    def on_second(self, ctx):
        p = self.p
        if ctx.i > 0 and ctx.i % (p["every_min"] * 60) == 0 and ctx.flat:
            (ctx.buy if self.rng.random() < 0.5 else ctx.sell)(p["stop_pts"], p["target_pts"], p["max_hold_s"], "coin")


BUILTIN = [DeltaMomentum, VwapReversion, OpeningRange, QueueImbalance, SweepFollow, RandomEntry]
USER_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_strategies")


def registry():
    """Built-in strategies plus every Strategy subclass found in user_strategies/*.py (re-read on each call)."""
    reg = {c.name: c for c in BUILTIN}
    if os.path.isdir(USER_DIR):
        for fn in sorted(os.listdir(USER_DIR)):
            if fn.endswith(".py") and not fn.startswith("_"):
                spec = importlib.util.spec_from_file_location("user_strategies." + fn[:-3], os.path.join(USER_DIR, fn))
                mod = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(mod)
                except Exception as e:                       # a broken user file must not take the app down
                    print(f"user_strategies/{fn}: {e}")
                    continue
                for v in vars(mod).values():
                    if isinstance(v, type) and issubclass(v, Strategy) and v is not Strategy and v.__module__ == mod.__name__:
                        reg[v.name] = v
    return reg
