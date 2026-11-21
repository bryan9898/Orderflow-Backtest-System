"""Event-driven backtest engine.

A strategy is a class with a `params` spec and an `on_second(ctx)` method. The engine walks a session one second at a
time. At second i the strategy sees the book snapshot and tape up to the END of second i (through `ctx`), and may call
ctx.buy(...) / ctx.sell(...). The order is filled in second i+1 by the same conservative fill model as the report
(backtest.simulate): enter at the ask / bid, stop filled 1 tick worse, target needs 1 tick through, stop first when
both are hit in one second, time exit, costs. One position at a time; orders sent while a position is open are
rejected. Every position is flat at the end of the session.

Controls: every run also scores OPPOSITE (the other side at the same seconds) and RANDOM (the same brackets at
random seconds of the same sessions), so a result can be compared with what luck gives.
"""
import random

import numpy as np

from . import backtest, config as C, metrics, store
from .features import Features


class Param:
    def __init__(self, default, lo, hi, step, label="", help=""):
        self.default, self.lo, self.hi, self.step, self.label, self.help = default, lo, hi, step, label, help

    def spec(self, name):
        return dict(name=name, default=self.default, min=self.lo, max=self.hi, step=self.step,
                    label=self.label or name.replace("_", " "), help=self.help, int=isinstance(self.default, int))


class Strategy:
    name = "base"
    title = "Strategy"
    description = ""
    params = {}

    def __init__(self, **kw):
        p = {k: v.default for k, v in self.params.items()}
        for k, v in kw.items():
            if k in self.params:
                p[k] = type(self.params[k].default)(v)
        self.p = p

    @classmethod
    def spec(cls):
        return dict(name=cls.name, title=cls.title, description=cls.description, params=[v.spec(k) for k, v in cls.params.items()])

    def on_start(self, ctx):
        pass

    def on_second(self, ctx):
        raise NotImplementedError


class Ctx:
    """What a strategy can see at second i. Nothing here reaches past index i."""

    def __init__(self, B, F, card):
        self.B, self.F, self.card = B, F, card
        self.n = len(B["t"])
        self.i = 0
        self.position = 0
        self._order = None

    # the present
    @property
    def t(self):
        return int(self.B["t"][self.i])

    @property
    def elapsed(self):
        """Seconds since the session start."""
        return self.i

    @property
    def bid(self):
        return self.F.bid[self.i]

    @property
    def ask(self):
        return self.F.ask[self.i]

    @property
    def mid(self):
        return self.F.mid[self.i]

    @property
    def flat(self):
        return self.position == 0

    def now(self, arr):
        """The current value of a feature array, e.g. ctx.now(ctx.F.delta_sum(30))."""
        return arr[self.i]

    def past(self, arr, k=None):
        """A read-only view of a feature array up to and including now (the last k values if k is given)."""
        v = arr[: self.i + 1] if k is None else arr[max(0, self.i + 1 - k): self.i + 1]
        v = v.view()
        v.flags.writeable = False
        return v

    def book(self, levels=10):
        """Best `levels` sizes each side: (bid sizes from the best down, ask sizes from the best up)."""
        return self.B["bid_lad"][self.i, :levels], self.B["ask_lad"][self.i, :levels]

    # orders
    def buy(self, stop_pts, target_pts, max_hold_s=600, tag=""):
        return self._send(1, stop_pts, target_pts, max_hold_s, tag)

    def sell(self, stop_pts, target_pts, max_hold_s=600, tag=""):
        return self._send(-1, stop_pts, target_pts, max_hold_s, tag)

    def _send(self, side, stop_pts, target_pts, hold, tag):
        if self.position != 0 or self._order is not None:
            return False
        self._order = (side, float(stop_pts), float(target_pts), int(hold), tag)
        return True


def run_session(B, strategy, F=None):
    F = F or Features(B)
    ctx = Ctx(B, F, B.get("card", {}))
    strategy.on_start(ctx)
    trades, busy_until = [], -1
    n = len(B["t"])
    for i in range(n - 1):
        ctx.i = i
        ctx.position = 0 if i >= busy_until else ctx.position
        ctx._order = None
        strategy.on_second(ctx)
        if ctx._order is not None:
            side, sp, tp, hold, tag = ctx._order
            tr = backtest.simulate(B, i, side, stop_pts=sp, target_pts=tp, max_hold=hold)
            if tr:
                tr.update(tag=tag, stop_pts=sp, target_pts=tp, hold=hold, session=ctx.card.get("id", ""))
                trades.append(tr)
                busy_until = tr["x_s"]
                ctx.position = side
    return trades


def controls(B, trades, seed=1, n_random=C.RANDOM_CONTROL_N):
    """Random seconds are drawn from the part of the session where the strategy was able to trade (from its first signal)."""
    opp, rnd = [], []
    rng = random.Random(seed)
    n = len(B["t"])
    warmup = min((tr["sig_s"] for tr in trades), default=0)
    for tr in trades:
        o = backtest.simulate(B, tr["sig_s"], -tr["side"], tr["stop_pts"], tr["target_pts"], tr["hold"])
        if o:
            o.update(session=tr["session"]); opp.append(o)
        for _ in range(n_random):
            r = backtest.simulate(B, rng.randint(warmup, n - 2), tr["side"], tr["stop_pts"], tr["target_pts"], tr["hold"])
            if r:
                r.update(session=tr["session"]); rnd.append(r)
    return opp, rnd


def backtest_run(strategy_cls, params, session_ids, seed=1, with_controls=True):
    """Run one strategy with one parameter set on several sessions. Returns trades, controls and metrics."""
    all_tr, all_opp, all_rnd, per = [], [], [], []
    for k, sid in enumerate(session_ids):
        B = store.load(sid)
        F = _features(sid, B)
        tr = run_session(B, strategy_cls(**params), F)
        all_tr += tr
        if with_controls:
            o, r = controls(B, tr, seed=seed + k)
            all_opp += o; all_rnd += r
        per.append(dict(session=sid, label=B["card"]["label"], **metrics.summary(tr)))
    out = dict(strategy=strategy_cls.name, params=strategy_cls(**params).p, sessions=session_ids,
               summary=metrics.summary(all_tr), per_session=per, trades=all_tr,
               equity=metrics.equity(all_tr), ci=metrics.bootstrap_ci(all_tr, session_ids, seed=seed))
    if with_controls:
        out.update(controls=dict(opposite=metrics.summary(all_opp), random=metrics.summary(all_rnd)))
    return out


def sweep(strategy_cls, base, grid, train_ids, test_ids, min_trades=5, max_combos=400):
    """Grid search on the TRAIN sessions; the best form (by average R, with at least min_trades) is then scored once
    on the TEST sessions, next to the default parameters. Returns every cell of the grid."""
    names = list(grid)
    combos = [()]
    for k in names:
        combos = [c + (v,) for c in combos for v in grid[k]]
    if len(combos) > max_combos:
        raise ValueError(f"{len(combos)} combinations; the limit is {max_combos}")
    cells = []
    for c in combos:
        p = dict(base, **dict(zip(names, c)))
        tr = []
        for sid in train_ids:
            B = store.load(sid)
            tr += run_session(B, strategy_cls(**p), _features(sid, B))
        s = metrics.summary(tr)
        cells.append(dict(values=list(c), n=s["n"], avg_R=s.get("avg_R"), total_usd=s.get("total_usd"), win=s.get("win"), pf=s.get("pf")))
    ok = [x for x in cells if x["n"] >= min_trades]
    best = max(ok, key=lambda x: x["avg_R"]) if ok else None
    out = dict(names=names, grid={k: list(v) for k, v in grid.items()}, cells=cells, train=train_ids, test=test_ids, best=best,
               n_tested=len(cells))
    if best and test_ids:
        bp = dict(base, **dict(zip(names, best["values"])))
        out["best_test"] = backtest_run(strategy_cls, bp, test_ids, with_controls=True)
        out["default_test"] = backtest_run(strategy_cls, base, test_ids, with_controls=False)["summary"]
        for k in ("trades", "equity"):
            out["best_test"].pop(k, None)
    return out


_fcache = {}


def _features(sid, B):
    if sid not in _fcache:
        if len(_fcache) > 12:
            _fcache.pop(next(iter(_fcache)))
        _fcache[sid] = Features(B)
    return _fcache[sid]
