"""Trade statistics. R = points / the trade's own stop distance. Dollars are for one contract at $20 a point."""
import random

import numpy as np

from . import config as C


def summary(T):
    if not T:
        return dict(n=0)
    p = np.array([x["pts"] for x in T])
    R = np.array([x["R"] for x in T])
    usd = p * C.POINT_USD
    eq = np.concatenate([[0.0], np.cumsum(usd)])
    dd = float((np.maximum.accumulate(eq) - eq).max())
    gain, loss = usd[usd > 0].sum(), -usd[usd < 0].sum()
    hold = np.array([x["x_s"] - x["e_s"] for x in T])
    hows = [x["how"] for x in T]
    return dict(n=len(T), win=round(100 * float((p > 0).mean()), 1), avg_R=round(float(R.mean()), 3),
                avg_pts=round(float(p.mean()), 2), total_pts=round(float(p.sum()), 2), total_usd=round(float(usd.sum()), 0),
                pf=round(float(gain / loss), 2) if loss > 0 else None, expectancy_usd=round(float(usd.mean()), 1),
                max_dd_usd=round(dd, 0), best_usd=round(float(usd.max()), 0), worst_usd=round(float(usd.min()), 0),
                avg_hold_s=round(float(hold.mean()), 0), longs=int(sum(x["side"] == 1 for x in T)),
                stops=hows.count("stop"), targets=hows.count("target"), timeouts=hows.count("time") + hows.count("session end"))


def equity(T):
    out, cum = [], 0.0
    for k, x in enumerate(T):
        cum += x["usd"]
        out.append(dict(k=k, t=x["t_exit"], usd=round(cum, 2), session=x.get("session", "")))
    return out


def bootstrap_ci(T, session_ids, n_boot=2000, level=0.90, seed=1):
    """Confidence interval for the average R. Resamples whole SESSIONS when there are several (trades on one day are
    not independent), otherwise single trades. With few trades the interval is wide, which is the point."""
    if len(T) < 2:
        return None
    rng = random.Random(seed)
    by = {}
    for x in T:
        by.setdefault(x.get("session", ""), []).append(x["R"])
    groups = [by[s] for s in session_ids if s in by] if len(by) >= 3 else [[x["R"]] for x in T]
    est = []
    for _ in range(n_boot):
        pick = [groups[rng.randrange(len(groups))] for _ in groups]
        vals = [r for g in pick for r in g]
        if vals:
            est.append(sum(vals) / len(vals))
    est.sort()
    lo, hi = est[int((1 - level) / 2 * len(est))], est[int((1 + level) / 2 * len(est)) - 1]
    return dict(level=level, lo=round(lo, 3), hi=round(hi, 3), by="session" if len(by) >= 3 else "trade")
