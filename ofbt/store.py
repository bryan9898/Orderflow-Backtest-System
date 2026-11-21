"""Session store: each session is one rebuilt book (per-second snapshots, depth heatmap and tape) saved as a
compressed .npz plus a small JSON card. Building is the slow step (MBO -> book); everything after it - the web app,
backtests and sweeps - loads the saved session in well under a second.

    data/sessions/          synthetic sessions (safe to share; re-created by `python -m ofbt build`)
    private_data/sessions/  sessions built from real Databento files (licensed: git-ignored, never share)
"""
import json
import os
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from . import book, config as C, loaders, synth

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC_DIR = os.path.join(ROOT, "data", "sessions")
PRIVATE_DIR = os.path.join(ROOT, "private_data", "sessions")
NY = ZoneInfo("America/New_York")

SEC_KEYS = ("t", "bb", "ba", "lo_b", "hi_b", "lo_a", "hi_a")
_cache, _lock = {}, threading.Lock()


def _ny(t):
    return datetime.fromtimestamp(int(t), timezone.utc).astimezone(NY)


def save(B, sid, info, private=False):
    d = PRIVATE_DIR if private else PUBLIC_DIR
    os.makedirs(d, exist_ok=True)
    arrs = {k: B[k].astype(np.int32) for k in SEC_KEYS}
    arrs["t"] = B["t"].astype(np.int64)
    arrs.update(bid_lad=np.minimum(B["bid_lad"], 65535).astype(np.uint16), ask_lad=np.minimum(B["ask_lad"], 65535).astype(np.uint16),
                heat_base=B["heat_base"].astype(np.int32), heat_bid=B["heat_bid"], heat_ask=B["heat_ask"],
                tr_ns=B["tr_ns"].astype(np.int64), tr_tk=B["tr_tk"].astype(np.int32), tr_sz=B["tr_sz"].astype(np.int32),
                tr_sd=B["tr_sd"].astype(np.int8))
    np.savez_compressed(os.path.join(d, sid + ".npz"), **arrs)
    t0, t1 = _ny(B["t"][0]), _ny(B["t"][-1])
    card = dict(id=sid, private=private, tick=C.TICK, point_usd=C.POINT_USD, seconds=int(len(B["t"])), trades=int(len(B["tr_tk"])),
                contracts=int(B["tr_sz"].sum()), records=int(B["records"]), start=t0.isoformat(), end=t1.isoformat(),
                label=f"{t0:%a %d %b %Y} {t0:%H:%M}-{t1:%H:%M} NY", heat_half=book.HEAT,
                open=float(B["ba"][0] + B["bb"][0]) / 2 * C.TICK, **info)
    with open(os.path.join(d, sid + ".json"), "w") as f:
        json.dump(card, f, indent=1)
    return card


def list_sessions():
    out = []
    for d in (PUBLIC_DIR, PRIVATE_DIR):
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".json") and os.path.exists(os.path.join(d, fn[:-5] + ".npz")):
                    with open(os.path.join(d, fn)) as f:
                        out.append(json.load(f))
    return sorted(out, key=lambda c: (c["private"], c["start"]))


def card(sid):
    for c in list_sessions():
        if c["id"] == sid:
            return c
    raise KeyError(sid)


def load(sid):
    """The session as a dict of numpy arrays, in the same shape book.rebuild() returns. Cached in memory."""
    with _lock:
        if sid in _cache:
            return _cache[sid]
    c = card(sid)
    z = np.load(os.path.join(PRIVATE_DIR if c["private"] else PUBLIC_DIR, sid + ".npz"))
    B = {k: z[k] for k in z.files}
    for k in SEC_KEYS[1:]:
        B[k] = B[k].astype(np.int64)
    B.update(card=c, tick=c["tick"], records=c["records"])
    with _lock:
        if len(_cache) > 12:
            _cache.pop(next(iter(_cache)))
        _cache[sid] = B
    return B


def _weekdays(start, n):
    d = date.fromisoformat(start)
    days = []
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def build_synthetic(n=5, first_seed=C.SYNTH_SEED, minutes=C.SYNTH_MINUTES, start=C.SYNTH_DATE, log=print, force=False):
    """n synthetic sessions on consecutive (fake) weekdays, each with its own seed. Each opens near the last one's close."""
    cards, price = [], C.SYNTH_START_PRICE
    for k, day in enumerate(_weekdays(start, n)):
        seed = first_seed + k
        sid = f"syn-{day}-s{seed}"
        if not force and os.path.exists(os.path.join(PUBLIC_DIR, sid + ".npz")):
            c = card(sid)
            log(f"{sid}: already built")
        else:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "mbo.csv.gz")
                synth.generate(path, seed=seed, minutes=minutes, date=day, start_price=price, log=log)
                meta, chunks = loaders.load_synthetic(path)
                B = book.rebuild(meta, chunks, tick=C.TICK, price_scale=C.PRICE_SCALE, log=log)
            c = save(B, sid, dict(kind="synthetic", seed=seed, dataset=meta["dataset"], schema="mbo", symbol="NQ.SYN",
                                  contract="NQ-like synthetic contract (tick 0.25, $20 a point)"))
            log(f"{sid}: saved, {c['seconds']:,} s, {c['trades']:,} trades")
        price = round(load(sid)["bb"][-1] * C.TICK, 2)
        cards.append(c)
    return cards


def build_dbn(path, day=None, t_from="09:30", t_to="16:00", log=print):
    """A session from a real Databento GLBX.MDP3 mbo file already on disk. Saved under private_data/ (licensed)."""
    meta, chunks = loaders.load_dbn(path)
    day = day or meta["start"][:10]
    a = int(datetime.fromisoformat(f"{day}T{t_from}:00").replace(tzinfo=NY).timestamp())
    b = int(datetime.fromisoformat(f"{day}T{t_to}:00").replace(tzinfo=NY).timestamp())
    B = book.rebuild(meta, chunks, tick=C.TICK, price_scale=C.PRICE_SCALE, keep_from=a, keep_to=b, log=log)
    sid = f"real-{day}-{t_from.replace(':', '')}-{t_to.replace(':', '')}"
    return save(B, sid, dict(kind="databento", dataset=meta["dataset"], schema=meta["schema"], symbol=", ".join(meta["symbols"]),
                             stype_in=meta["stype_in"], source=os.path.basename(path),
                             contract="NQ, CME E-mini Nasdaq-100 futures (tick 0.25, $20 a point)"), private=True)
