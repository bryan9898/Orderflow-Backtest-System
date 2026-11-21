"""Command line.

    python -m ofbt build [--sessions 8]                 build the synthetic sessions (data/sessions/)
    python -m ofbt build --dbn FILE.dbn.zst [--day D] [--from 09:30] [--to 16:00]
                                                        a session from a real Databento mbo file (private_data/, never share)
    python -m ofbt serve [--port 8765] [--no-open]      the replay + backtest web app
    python -m ofbt backtest STRATEGY [--set k=v ...]    a backtest in the terminal on every built session
    python -m ofbt list                                 sessions and strategies
"""
import argparse
import json
import sys

from . import engine, store, strategies


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python -m ofbt")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sessions", type=int, default=8)
    b.add_argument("--force", action="store_true")
    b.add_argument("--dbn")
    b.add_argument("--day")
    b.add_argument("--from", dest="t_from", default="09:30")
    b.add_argument("--to", dest="t_to", default="16:00")
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--no-open", action="store_true")
    t = sub.add_parser("backtest")
    t.add_argument("strategy")
    t.add_argument("--set", nargs="*", default=[], help="parameter overrides, e.g. stop_pts=6 target_pts=12")
    t.add_argument("--sessions", nargs="*")
    t.add_argument("--json", action="store_true")
    sub.add_parser("list")
    a = ap.parse_args(argv)

    if a.cmd == "build":
        if a.dbn:
            c = store.build_dbn(a.dbn, a.day, a.t_from, a.t_to, log=lambda m: print("  " + m))
            print(f"saved {c['id']} (private_data/, licensed: do not share)")
        else:
            for c in store.build_synthetic(n=a.sessions, force=a.force, log=lambda m: print("  " + m)):
                print(f"{c['id']:22s} {c['label']}  {c['trades']:,} trades")
    elif a.cmd == "serve":
        from .server import serve
        serve(a.host, a.port, not a.no_open)
    elif a.cmd == "list":
        for c in store.list_sessions():
            print(f"session  {c['id']:28s} {c['label']}  {c['kind']}")
        for k, v in strategies.registry().items():
            print(f"strategy {k:28s} {v.title}")
    elif a.cmd == "backtest":
        cls = strategies.registry()[a.strategy]
        params = dict(kv.split("=", 1) for kv in a.set)
        ids = a.sessions or [c["id"] for c in store.list_sessions() if not c["private"]]
        out = engine.backtest_run(cls, params, ids)
        if a.json:
            print(json.dumps({k: v for k, v in out.items() if k not in ("trades", "equity")}, indent=1, default=str))
            return
        print(f"{cls.title}  {json.dumps(out['params'])}")
        for row in out["per_session"]:
            print(f"  {row['label']:34s} {row['n']:4d} trades" + (f"  {row['avg_R']:+.3f} R  ${row['total_usd']:+,.0f}" if row["n"] else ""))
        for k, v in [("strategy", out["summary"]), *out["controls"].items()]:
            print(f"  {k:9s} " + (f"{v['n']:5d} trades  won {v['win']:5.1f}%  {v['avg_R']:+.3f} R  PF {v['pf']}  ${v['total_usd']:+,.0f}" if v["n"] else "no trades"))
        if out["ci"]:
            print(f"  90% interval for the strategy's average R (resampled by {out['ci']['by']}): {out['ci']['lo']:+.3f} to {out['ci']['hi']:+.3f}")


if __name__ == "__main__":
    sys.exit(main())
