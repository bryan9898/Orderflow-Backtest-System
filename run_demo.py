"""One command, start to finish.

    python run_demo.py                         synthetic demo: generate data -> rebuild book -> rule -> fills -> out/report.html
    python run_demo.py --dbn FILE.dbn.zst \
        --from 09:30 --to 11:30                a real Databento GLBX.MDP3 mbo file you already have (local only; output goes
                                               to private_out/, which must never be shared)
"""
import argparse
import json
import os
import sys
import time
import webbrowser
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from demo import backtest, book, config as C, loaders, report, rule, synth

HERE = os.path.dirname(os.path.abspath(__file__))
NY = ZoneInfo("America/New_York")


def ny_window(day, t_from, t_to):
    a = datetime.fromisoformat(f"{day}T{t_from}:00").replace(tzinfo=NY)
    b = datetime.fromisoformat(f"{day}T{t_to}:00").replace(tzinfo=NY)
    return int(a.timestamp()), int(b.timestamp())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dbn", help="a real Databento mbo file (.dbn / .dbn.zst) on disk")
    ap.add_argument("--day", help="New York date of the session in the file, YYYY-MM-DD (default: the file's start date)")
    ap.add_argument("--from", dest="t_from", default="09:30")
    ap.add_argument("--to", dest="t_to", default="11:30")
    ap.add_argument("--no-open", action="store_true", help="do not open the report in a browser")
    a = ap.parse_args()
    t_start = time.time()

    if a.dbn:
        out_dir = os.path.join(HERE, "private_out")
        meta, chunks = loaders.load_dbn(a.dbn)
        day = a.day or meta["start"][:10]
        k0, k1 = ny_window(day, a.t_from, a.t_to)
        print(f"[1/5] real file {a.dbn}: {meta['dataset']} {meta['schema']} {meta['symbols']} ({meta['stype_in']}), file covers {meta['start']} to {meta['end']}")
        synthetic = False
    else:
        out_dir = os.path.join(HERE, "out")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "synthetic_mbo.csv.gz")
        print("[1/5] generating synthetic MBO messages ...")
        synth.generate(path, log=lambda s: print("      " + s))
        meta, chunks = loaders.load_synthetic(path)
        k0 = k1 = None
        synthetic = True
    os.makedirs(out_dir, exist_ok=True)

    print("[2/5] rebuilding the order book from MBO and sampling it every second ...")
    B = book.rebuild(meta, chunks, tick=C.TICK, price_scale=C.PRICE_SCALE, keep_from=k0, keep_to=k1, log=lambda s: print("      " + s))
    print("[3/5] features and the demo rule ...")
    F = rule.features(B)
    sigs = rule.signals(B, F)
    print(f"      {len(sigs)} signals")
    print("[4/5] fills and controls ...")
    res = backtest.run(B, sigs)
    for k, s in res["stats"].items():
        if s["n"]:
            print(f"      {k:9s} {s['n']:4d} trades  won {s['win']:5.1f}%  avg {s['avg_pts']:+6.2f} pts ({s['avg_R']:+.3f} R)  total {s['total_pts']:+8.2f} pts")
        else:
            print(f"      {k:9s} no trades")

    def ny(t):
        return datetime.fromtimestamp(int(t), timezone.utc).astimezone(NY).strftime("%Y-%m-%d %H:%M:%S")
    run_info = dict(synthetic=synthetic,
                    contract="NQ-like synthetic contract (tick 0.25, $20 a point)" if synthetic else "NQ, CME E-mini Nasdaq-100 futures, front month (tick 0.25, $20 a point)",
                    dataset=meta["dataset"], schema=meta["schema"], symbols=", ".join(meta["symbols"]), stype_in=meta["stype_in"],
                    range=f"{ny(B['t'][0])} to {ny(B['t'][-1])} New York",
                    records=int(B["records"]), prints=int(len(B["tr_tk"])), source=os.path.basename(meta["source"]),
                    built=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    print("[5/5] writing the report ...")
    rp = report.write(os.path.join(out_dir, "report.html"), B, F, sigs, res, run_info)
    with open(os.path.join(out_dir, "results.json"), "w") as f:
        json.dump(dict(run=run_info, stats=res["stats"], trades=res["rule"]), f, indent=1)
    print(f"done in {time.time() - t_start:.0f} s -> {rp}")
    if not a.no_open:
        webbrowser.open("file://" + rp)


if __name__ == "__main__":
    sys.exit(main())
