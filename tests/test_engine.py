"""Engine checks on the built sessions (run `python -m ofbt build` first):
  1. no look-ahead: every feature computed on a session cut at second k equals the full-session feature up to k
  2. parity: the engine running the default DeltaMomentum gives the same trades as the static report's rule,
     once the report's overlapping trades are removed (the engine holds one position at a time)
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ofbt import backtest, engine, rule, store                     # noqa: E402
from ofbt.features import Features                                 # noqa: E402
from ofbt.strategies import DeltaMomentum                          # noqa: E402


def cut(B, k):
    t_end = (int(B["t"][0]) + k + 1) * 1_000_000_000
    m = B["tr_ns"] < t_end
    out = {key: (v[: k + 1] if isinstance(v, np.ndarray) and len(v) == len(B["t"]) else v) for key, v in B.items()}
    out.update({key: B[key][m] for key in ("tr_ns", "tr_tk", "tr_sz", "tr_sd")})
    return out


def main():
    sid = store.list_sessions()[0]["id"]
    B = store.load(sid)
    full = Features(B)
    for k in (700, 2500, 5000):
        part = Features(cut(B, k))
        checks = dict(mid=lambda F: F.mid, vwap=lambda F: F.vwap, cvd=lambda F: F.cvd, last=lambda F: F.last,
                      d30=lambda F: F.delta_sum(30), thr=lambda F: F.past_pctl("delta_sum", 30, 1800, 95.0),
                      imb=lambda F: F.imbalance(5), hi=lambda F: F.high(900), lo=lambda F: F.low(900),
                      sd=lambda F: F.mid_std(1800), ema=lambda F: F.ema(60))
        for name, f in checks.items():
            a, b = f(full)[: k + 1], f(part)
            assert np.allclose(a, b, equal_nan=True), f"look-ahead in {name} at cut {k}"
    print("no look-ahead: ok")

    if not sid.endswith("-s7"):
        print("parity skipped (first session is not the seed-7 demo)")
        return
    sigs = rule.signals(B, rule.features(B))
    ref, busy = [], -1
    for g in sigs:
        if g["s"] < busy:
            continue
        tr = backtest.simulate(B, g["s"], g["side"])
        if tr:
            ref.append(tr); busy = tr["x_s"]
    got = engine.run_session(B, DeltaMomentum())
    a = [(x["sig_s"], x["side"], x["pts"]) for x in ref]
    b = [(x["sig_s"], x["side"], x["pts"]) for x in got]
    assert a == b, (a, b)
    print(f"parity with the report rule: ok ({len(b)} trades)")


if __name__ == "__main__":
    main()
