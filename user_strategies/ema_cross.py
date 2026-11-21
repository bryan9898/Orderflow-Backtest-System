"""An example of a user strategy. Any .py file in this folder that defines a Strategy subclass shows up in the app
(the list is re-read every time the strategy menu loads, so you do not need to restart the server).

Rules for a strategy:
  - set `name` (unique, no spaces), `title`, `description` and `params` (each a Param(default, min, max, step, label))
  - read only through ctx: ctx.i is the current second; ctx.F holds the per-second features (see ofbt/features.py);
    use index ctx.i or earlier, never later
  - send orders with ctx.buy(stop_pts, target_pts, max_hold_s, tag) / ctx.sell(...); they fill in the next second
"""
from ofbt.engine import Param, Strategy


class EmaCross(Strategy):
    name = "ema_cross"
    title = "EMA cross (user example)"
    description = "Buy when a fast EMA of the mid crosses above a slow one, sell on the cross below."
    params = dict(fast_s=Param(60, 5, 600, 5, "fast EMA (s)"), slow_s=Param(600, 30, 3600, 30, "slow EMA (s)"),
                  stop_pts=Param(10.0, 1.0, 40.0, 0.5, "stop (pts)"), target_pts=Param(20.0, 1.0, 80.0, 0.5, "target (pts)"),
                  max_hold_s=Param(1200, 30, 7200, 30, "max hold (s)"))

    def on_start(self, ctx):
        self.f, self.s = ctx.F.ema(self.p["fast_s"]), ctx.F.ema(self.p["slow_s"])

    def on_second(self, ctx):
        i, p = ctx.i, self.p
        if i < self.p["slow_s"]:
            return
        up_now, up_before = self.f[i] > self.s[i], self.f[i - 1] > self.s[i - 1]
        if up_now != up_before:
            (ctx.buy if up_now else ctx.sell)(p["stop_pts"], p["target_pts"], p["max_hold_s"], "cross")
