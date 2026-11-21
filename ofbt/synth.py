"""Synthetic MBO generator for the public demo.

Writes a CSV.gz with the same columns and conventions as a Databento GLBX.MDP3 `mbo` file (see loaders.py), for an
invented NQ-like contract on an invented date. It is a toy order-book simulation:
  - a hidden "fair price" follows a random walk whose volatility comes in bursts (calm stretches, then fast ones)
  - liquidity providers add, cancel and resize limit orders around the fair price (A / C / M messages);
    quotes that end up far from it, or on the wrong side of it, get pulled
  - takers send market orders when the fair price moves through the inside, plus some random market orders;
    each order walks the book FIFO and every match emits T (aggressor side) + F (resting fill) + C or M, like CME MBO
The fair price is a random walk, so by construction nothing here can be predicted: a rule run on this data should
do about as well as its random-seconds control. Nothing here is real market data and nothing was fitted to real data.
It exists so a reviewer can run the whole pipeline without a Databento account, and no licensed data is redistributed.
"""
import gzip
import math
import random
from datetime import datetime
from zoneinfo import ZoneInfo

from . import config as C

UNIT = int(round(C.TICK * C.PRICE_SCALE))
NULL_PRICE = 9223372036854775807


def generate(path, seed=C.SYNTH_SEED, minutes=C.SYNTH_MINUTES, rate=60.0, log=print, date=C.SYNTH_DATE,
             start_ny=C.SYNTH_START_NY, start_price=C.SYNTH_START_PRICE):
    rng = random.Random(seed)
    ny = ZoneInfo("America/New_York")
    t0 = datetime.fromisoformat(f"{date}T{start_ny}:00").replace(tzinfo=ny)
    ns = int(t0.timestamp()) * 1_000_000_000
    end_ns = ns + minutes * 60 * 1_000_000_000
    fair = start_price / C.TICK          # in ticks, real-valued

    levels = ({}, {})                            # side (0 bid, 1 ask) -> {tick: [order ids, FIFO]}
    orders = {}                                  # oid -> [side, tick, size]
    live, pos = [], {}                           # live order ids, for O(1) random picks
    rows = []
    seq = 0
    nxt = 10_000_000

    def emit(ts, act, sd, tick, size, oid):
        nonlocal seq
        seq += 1
        rows.append(f"{ts},{ts - 150_000 - (seq * 7919) % 300_000},4242,{oid},{NULL_PRICE if tick is None else tick * UNIT},{size},0,{act},{sd},{seq}")

    def best(s):
        lv = levels[s]
        return (max(lv) if s == 0 else min(lv)) if lv else None

    def add(ts, s, tick, size):
        nonlocal nxt
        nxt += 1
        orders[nxt] = [s, tick, size]
        levels[s].setdefault(tick, []).append(nxt)
        pos[nxt] = len(live); live.append(nxt)
        emit(ts, "A", "BA"[s], tick, size, nxt)

    def remove(oid):
        s, tick, _ = orders.pop(oid)
        q = levels[s][tick]; q.remove(oid)
        if not q:
            del levels[s][tick]
        i = pos.pop(oid); last = live.pop()
        if last != oid:
            live[i] = last; pos[last] = i

    def take(ts, ag, want):                      # ag 0 = buyer lifts offers, 1 = seller hits bids
        maker = 1 - ag
        while want > 0 and levels[maker]:
            tick = best(maker)
            oid = levels[maker][tick][0]
            q = orders[oid][2]
            fill = min(q, want)
            emit(ts, "T", "BA"[ag], tick, fill, 0)
            emit(ts, "F", "BA"[maker], tick, fill, oid)
            if fill == q:
                emit(ts, "C", "BA"[maker], tick, q, oid); remove(oid)
            else:
                orders[oid][2] = q - fill
                emit(ts, "M", "BA"[maker], tick, q - fill, oid)
            want -= fill

    def quote(ts, s):
        dist = int(rng.expovariate(1 / 1.6))
        bb, ba = best(0), best(1)
        if s == 0:
            tick = math.floor(fair) - dist
            if ba is not None and tick >= ba:
                tick = ba - 1
        else:
            tick = math.floor(fair) + 1 + dist
            if bb is not None and tick <= bb:
                tick = bb + 1
        add(ts, s, tick, rng.choice((1, 1, 1, 1, 2, 2, 3, 4, 5, 8)))

    emit(ns, "R", "N", None, 0, 0)               # snapshot: clear, then a full book
    for s in (0, 1):
        for k in range(1, 21):
            for _ in range(rng.randint(2, 6)):
                add(ns, s, int(fair) - k if s == 0 else int(fair) + k, rng.choice((1, 1, 2, 3, 5, 10)))

    vol = 1.0
    ts = ns
    step_sd = 1.4 / math.sqrt(rate)              # about 1.4 ticks a second in calm stretches
    while ts < end_ns:
        ts += int(rng.expovariate(rate) * 1e9) + 1
        vol = 1.0 + 0.9995 * (vol - 1.0) + (rng.random() < 0.0004) * rng.uniform(1, 3)   # rare bursts that fade
        fair += rng.gauss(0, step_sd * vol)
        bb, ba = best(0), best(1)
        u = rng.random()
        if bb is None or ba is None:
            quote(ts, 0 if bb is None else 1)
        elif fair > ba + 0.5 and u < 0.20:       # fair price moved through the offer: buyers take
            take(ts, 0, rng.randint(1, 3) + int(fair - ba))
        elif fair < bb - 0.5 and u < 0.20:
            take(ts, 1, rng.randint(1, 3) + int(bb - fair))
        elif u < 0.50:
            quote(ts, rng.randint(0, 1))
        elif u < 0.88:                           # cancel: far-away or wrong-side quotes go first
            for _ in range(4):
                oid = live[rng.randrange(len(live))]
                s, tick, q = orders[oid]
                far = abs(tick - fair) > 12 or (s == 0 and tick > fair + 1) or (s == 1 and tick < fair - 1)
                if far or rng.random() < 0.25 or len(live) > 350:
                    emit(ts, "C", "BA"[s], tick, q, oid); remove(oid); break
        elif u < 0.93:                           # resize (M at the same price)
            oid = live[rng.randrange(len(live))]
            s, tick, q = orders[oid]
            nq = max(1, q + rng.choice((-2, -1, 1, 2)))
            orders[oid][2] = nq
            emit(ts, "M", "BA"[s], tick, nq, oid)
        else:                                    # a random market order (noise)
            take(ts, rng.randint(0, 1), min(25, int(rng.paretovariate(1.8))))
        for s in (0, 1):                         # never let a side run out: fill the nearest empty price behind the inside
            while len(levels[s]) < 15:
                bst = best(s)
                tick = (bst if bst is not None else math.floor(fair) + s) - (1 if s == 0 else -1) * (bst is not None)
                while tick in levels[s]:
                    tick += -1 if s == 0 else 1
                add(ts, s, tick, rng.choice((1, 2, 3, 5)))

    with gzip.open(path, "wt", newline="") as f:
        f.write("ts_recv,ts_event,instrument_id,order_id,price,size,flags,action,side,sequence\n")
        f.write("\n".join(rows)); f.write("\n")
    log(f"synthetic MBO written: {len(rows):,} messages, {minutes} minutes from {date} {start_ny} New York -> {path}")
    return path
