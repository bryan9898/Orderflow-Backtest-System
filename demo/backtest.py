"""Fill model and statistics.

A signal at the end of second s is acted on in second s+1 (no same-second fill):
  long  enters at the best ASK of second s+1, short at the best BID (you pay the spread)
  stop   hit when the bid (long) / ask (short) or any trade reaches the stop price; filled 1 tick WORSE
  target hit only when price trades 1 tick THROUGH the target (a touch is not a fill)
  if stop and target fall inside the same second, the STOP is assumed first (pessimistic)
  time exit after MAX_HOLD_S at the bid (long) / ask (short); cost 0.62 points per round turn
Controls, scored with the identical bracket:
  OPPOSITE  the other side at the same seconds
  RANDOM    the same side mix at 20 random seconds per signal (seeded), drawn from the same tradable window
"""
import random

import numpy as np

from . import config as C


def simulate(B, s, side):
    tk = C.TICK
    n = len(B["t"])
    e = s + 1
    if e >= n or B["bb"][e] < 0 or B["ba"][e] < 0:
        return None
    entry = int(B["ba"][e] if side == 1 else B["bb"][e])
    stop = entry - side * int(round(C.STOP_PTS / tk))
    tgt = entry + side * int(round(C.TARGET_PTS / tk))
    last = min(n - 1, e + C.MAX_HOLD_S)
    for j in range(e + 1, last + 1):
        if side == 1:
            hit_s = B["lo_b"][j] <= stop
            hit_t = B["hi_b"][j] >= tgt + C.TARGET_THROUGH_TICKS
        else:
            hit_s = B["hi_a"][j] >= stop
            hit_t = B["lo_a"][j] <= tgt - C.TARGET_THROUGH_TICKS
        if hit_s:
            return _res(B, s, side, entry, stop, tgt, j, stop - side * C.STOP_SLIP_TICKS, "stop")
        if hit_t:
            return _res(B, s, side, entry, stop, tgt, j, tgt, "target")
    xp = int(B["bb"][last] if side == 1 else B["ba"][last])
    return _res(B, s, side, entry, stop, tgt, last, xp, "time")


def _res(B, s, side, entry, stop, tgt, j, xp, how):
    tk = C.TICK
    pts = (xp - entry) * side * tk - C.COST_PTS
    return dict(sig_s=s, e_s=s + 1, x_s=j, t_entry=int(B["t"][s + 1]), t_exit=int(B["t"][j]), side=side,
                entry=entry * tk, stop=stop * tk, target=tgt * tk, exit=xp * tk, how=how,
                pts=round(pts, 2), R=round(pts / C.STOP_PTS, 3), usd=round(pts * C.POINT_USD, 2))


def stats(T):
    if not T:
        return dict(n=0)
    p = np.array([x["pts"] for x in T])
    eq = np.cumsum(p)
    dd = float((np.maximum.accumulate(np.concatenate([[0], eq])) - np.concatenate([[0], eq])).max())
    return dict(n=len(T), win=round(100 * float((p > 0).mean()), 1), avg_pts=round(float(p.mean()), 2),
                avg_R=round(float(p.mean()) / C.STOP_PTS, 3), total_pts=round(float(p.sum()), 2),
                total_usd=round(float(p.sum()) * C.POINT_USD, 0), max_dd_pts=round(dd, 2),
                stops=sum(x["how"] == "stop" for x in T), targets=sum(x["how"] == "target" for x in T),
                timeouts=sum(x["how"] == "time" for x in T))


def run(B, sigs, seed=1):
    rule = [r for r in (simulate(B, g["s"], g["side"]) for g in sigs) if r]
    opp = [r for r in (simulate(B, g["s"], -g["side"]) for g in sigs) if r]
    rng = random.Random(seed)
    lo, hi = C.WARMUP_S, len(B["t"]) - 2
    rnd = []
    for g in sigs:
        for _ in range(C.RANDOM_CONTROL_N):
            r = simulate(B, rng.randint(lo, hi), g["side"])
            if r:
                rnd.append(r)
    return dict(rule=rule, opposite=opp, random=rnd,
                stats=dict(rule=stats(rule), opposite=stats(opp), random=stats(rnd)))
