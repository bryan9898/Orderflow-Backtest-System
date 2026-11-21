"""Rebuild the full order book from MBO messages and sample it once per second.

MBO rules (Databento GLBX.MDP3, CME):
  A add      new resting order (order_id, side, price, size)
  C cancel   order removed (also sent after a resting order is fully filled)
  M modify   order changes price and/or size (a price change loses queue priority)
  R clear    book reset (start of a snapshot) - wipe everything
  T trade    a print; `side` is the AGGRESSOR side (B = buyer lifted the offer, A = seller hit the bid)
  F fill     the resting order's fill; depth changes come from the C / M that follows, so F does not touch depth
Prices are fixed-point integers (1e-9). They are converted to whole ticks of 0.25.

Output of `rebuild()` - one row per second (UTC), state at the END of that second:
  t        unix second
  bb, ba   best bid / best ask in ticks (-1 / -1 when a side is empty)
  bid_lad  sizes resting at bb, bb-1 tick, ... (LADDER levels);  ask_lad  sizes at ba, ba+1 tick, ...
  lo_b, hi_b, lo_a, hi_a   lowest / highest of (that second's trade prices, best bid / ask) - used by the fill model
heat_base / heat_bid / heat_ask  resting size on every tick from mid-50 to mid+50 (the depth heatmap)
plus the tape: every trade (ts_ns, tick, size, aggressor +1 buy / -1 sell / 0 unknown).
"""
import numpy as np

LADDER = 10
HEAT = 50          # depth heatmap: resting size at mid-50 .. mid+50 ticks, every second


def rebuild(meta, chunks, tick=0.25, price_scale=1_000_000_000, keep_from=None, keep_to=None, max_records=None, log=print, heat=True):
    unit = int(round(tick * price_scale))
    orders = {}                        # order_id -> [side(0 bid, 1 ask), tick, size]
    depth = ({}, {})                   # side -> {tick: total resting size}
    rows, tape = [], []
    hb, hbid, hask = [], [], []
    iid = None
    cur = None
    tmin = tmax = None
    n = 0

    def add_depth(sd, px, q):
        d = depth[sd]
        v = d.get(px, 0) + q
        if v > 0:
            d[px] = v
        else:
            d.pop(px, None)

    def close_second(sec):
        bids, asks = depth
        bb = max(bids) if bids else -1
        ba = min(asks) if asks else -1
        bl = [bids.get(bb - k, 0) for k in range(LADDER)] if bb >= 0 else [0] * LADDER
        al = [asks.get(ba + k, 0) for k in range(LADDER)] if ba >= 0 else [0] * LADDER
        if tmin is None:
            lob, hib, loa, hia = bb, bb, ba, ba
        else:
            lob, hib, loa, hia = min(tmin, bb), max(tmax, bb), min(tmin, ba), max(tmax, ba)
        rows.append((sec, bb, ba, lob, hib, loa, hia, *bl, *al))
        if heat:
            mid = (bb + ba) // 2 if bb >= 0 and ba >= 0 else max(bb, ba)
            base = mid - HEAT
            hb.append(base)
            hbid.append([bids.get(base + k, 0) for k in range(2 * HEAT + 1)])
            hask.append([asks.get(base + k, 0) for k in range(2 * HEAT + 1)])

    for a in chunks:
        if iid is None:                # a continuous symbol maps to one instrument; take the one that trades
            tr = a["instrument_id"][a["action"] == b"T"]
            iid = int(tr[0]) if len(tr) else int(a["instrument_id"][0])
        a = a[a["instrument_id"] == iid]
        for ts, act, sd, px, sz, oid in zip(a["ts_recv"].tolist(), a["action"].tolist(), a["side"].tolist(),
                                            a["price"].tolist(), a["size"].tolist(), a["order_id"].tolist()):
            sec = ts // 1_000_000_000
            if cur is None:
                cur = sec
            while sec > cur:           # the second `cur` is finished: sample the book (and any silent seconds)
                if (keep_from is None or cur >= keep_from) and (keep_to is None or cur < keep_to):
                    close_second(cur)
                tmin = tmax = None
                cur += 1
            if keep_to is not None and sec >= keep_to:
                break
            if act == b"R":
                orders.clear(); depth[0].clear(); depth[1].clear()
                continue
            t = px // unit if px != 9223372036854775807 else None
            if act == b"T":
                ag = 1 if sd == b"B" else (-1 if sd == b"A" else 0)
                tape.append((ts, t, sz, ag))
                tmin = t if tmin is None else min(tmin, t)
                tmax = t if tmax is None else max(tmax, t)
            elif act == b"A":
                s = 0 if sd == b"B" else (1 if sd == b"A" else None)
                if s is None:
                    continue
                old = orders.get(oid)
                if old:
                    add_depth(old[0], old[1], -old[2])
                orders[oid] = [s, t, sz]
                add_depth(s, t, sz)
            elif act == b"C":
                old = orders.pop(oid, None)
                if old:
                    add_depth(old[0], old[1], -old[2])
            elif act == b"M":
                old = orders.get(oid)
                if old:
                    add_depth(old[0], old[1], -old[2])
                    old[1], old[2] = t, sz
                    add_depth(old[0], t, sz)
                else:
                    s = 0 if sd == b"B" else (1 if sd == b"A" else None)
                    if s is not None:
                        orders[oid] = [s, t, sz]
                        add_depth(s, t, sz)
            # F (fill): depth is updated by the C / M that follows
            n += 1
            if max_records and n >= max_records:
                break
        else:
            continue
        break
    if cur is not None and (keep_to is None or cur < keep_to):
        close_second(cur)

    R = np.array(rows, dtype=np.int64)
    out = dict(meta=meta, instrument_id=iid, records=n, tick=tick,
               t=R[:, 0], bb=R[:, 1], ba=R[:, 2], lo_b=R[:, 3], hi_b=R[:, 4], lo_a=R[:, 5], hi_a=R[:, 6],
               bid_lad=R[:, 7:7 + LADDER], ask_lad=R[:, 7 + LADDER:7 + 2 * LADDER])
    if heat:
        out.update(heat_base=np.array(hb, dtype=np.int64), heat_bid=np.minimum(np.array(hbid, dtype=np.int64), 65535).astype(np.uint16),
                   heat_ask=np.minimum(np.array(hask, dtype=np.int64), 65535).astype(np.uint16))
    T = np.array(tape, dtype=np.int64).reshape(-1, 4)
    if keep_from is not None:
        T = T[T[:, 0] // 1_000_000_000 >= keep_from]
    out.update(tr_ns=T[:, 0], tr_tk=T[:, 1], tr_sz=T[:, 2], tr_sd=T[:, 3])
    log(f"book rebuilt: {n:,} MBO records, {len(R):,} seconds, {len(T):,} trades, instrument {iid}")
    return out
