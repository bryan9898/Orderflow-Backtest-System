"""The demo rule: a generic, textbook order-flow momentum rule. It is NOT anyone's trading strategy; it is here to show
how the app turns per-second book + tape features into decisions. Every input is known at the end of the second it is
used, and the threshold is relative to the recent market (a percentile of the last 30 minutes), not a fixed lot size.

At the end of each second s:
  delta30  = aggressive buy volume - aggressive sell volume over the last 30 s (from the tape's aggressor side)
  thr      = the 95th percentile of |delta30| over the previous 30 minutes (seconds s-1800 .. s-1, so s itself is excluded)
  imb      = (bid size on the best 5 levels - ask size on the best 5 levels) / (their sum)
  LONG  if delta30 >= thr and imb >= +0.20      (buyers are aggressive AND the book leans bid)
  SHORT if delta30 <= -thr and imb <= -0.20
  no signal in the first 10 minutes, and at least 2 minutes between signals.
"""
import numpy as np

from . import config as C


def features(B):
    n = len(B["t"])
    t0 = int(B["t"][0])
    sec = B["tr_ns"] // 1_000_000_000 - t0
    ok = (sec >= 0) & (sec < n)
    flow = np.zeros(n)
    np.add.at(flow, sec[ok], (B["tr_sz"] * B["tr_sd"])[ok])
    vol = np.zeros(n)
    np.add.at(vol, sec[ok], B["tr_sz"][ok])
    cs = np.concatenate([[0.0], np.cumsum(flow)])
    W = C.FLOW_WINDOW_S
    delta = cs[1:] - cs[np.maximum(np.arange(1, n + 1) - W, 0)]
    bid5 = B["bid_lad"][:, :C.BOOK_LEVELS].sum(1).astype(float)
    ask5 = B["ask_lad"][:, :C.BOOK_LEVELS].sum(1).astype(float)
    imb = np.where(bid5 + ask5 > 0, (bid5 - ask5) / np.maximum(bid5 + ask5, 1), 0.0)
    thr = np.full(n, np.nan)
    L = C.FLOW_LOOKBACK_S
    ad = np.abs(delta)
    for s in range(C.WARMUP_S, n):                     # past seconds only
        thr[s] = np.percentile(ad[max(0, s - L):s], C.FLOW_PCTL)
    return dict(delta=delta, imb=imb, thr=thr, vol=vol)


def signals(B, F):
    out = []
    last = -10 ** 9
    for s in range(C.WARMUP_S, len(B["t"]) - 1):
        if s - last < C.COOLDOWN_S or not np.isfinite(F["thr"][s]) or F["thr"][s] <= 0:
            continue
        side = 0
        if F["delta"][s] >= F["thr"][s] and F["imb"][s] >= C.IMB_MIN:
            side = 1
        elif F["delta"][s] <= -F["thr"][s] and F["imb"][s] <= -C.IMB_MIN:
            side = -1
        if side:
            out.append(dict(s=s, side=side, delta=float(F["delta"][s]), thr=float(F["thr"][s]), imb=float(F["imb"][s])))
            last = s
    return out
