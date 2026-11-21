"""Futures contract rolls in multi-day data: detect them, measure them, adjust for them.

A continuous futures series (Databento `NQ.v.0`, or a trading platform's "continuous" chart) is a chain of different
contracts. When it switches from the expiring contract to the next one, prices jump by the calendar spread (for NQ,
roughly 1% of the index, a few hundred points), although the market did not move. Anything that compares prices
across the switch - multi-day levels, prior closes, N-day moves - is wrong by that amount unless it is adjusted.

Two lessons from a real case (docs/case_study_roll.md):
  1. Detect switches from the instrument, never from price gaps. A roll step can hide inside an ordinary-looking gap.
  2. Two sources that roll on different days disagree by exactly the spread between those days. Comparing them at the
     same timestamps finds the problem and measures the spread to the tick.

    python -m ofbt rollcase     the synthetic reproduction, step by step
"""
import numpy as np

TICK = 0.25


# ---------------------------------------------------------------- tools
def gap_screen(sessions, k=4.0):
    """The tempting check: flag a session whose open is far from the previous close (k x the median absolute gap)."""
    gaps = np.array([s["open"] - p["close"] for p, s in zip(sessions, sessions[1:])])
    lim = k * max(np.median(np.abs(gaps)), TICK)
    return [dict(session=s["session"], gap=float(g), flagged=bool(abs(g) > lim), limit=float(lim))
            for s, g in zip(sessions[1:], gaps)]


def find_switches(sessions):
    """Contract switches from the instrument itself (Databento: instrument_id / raw symbol of the records)."""
    return [dict(session=s["session"], old=p["symbol"], new=s["symbol"], gap=float(s["open"] - p["close"]))
            for p, s in zip(sessions, sessions[1:]) if s["instrument_id"] != p["instrument_id"]]


def compare_sources(a, b):
    """Median difference a - b at matching timestamps, per session. Sources that agree differ by 0."""
    out = []
    for sa, sb in zip(a, b):
        assert sa["session"] == sb["session"]
        d = sa["px"] - sb["px"]
        out.append(dict(session=sa["session"], median=float(np.median(d)), min=float(d.min()), max=float(d.max()),
                        a=sa["symbol"], b=sb["symbol"]))
    return out


def measure_spread(old_px, new_px):
    """Calendar spread from prices of BOTH contracts at the same timestamps: median(new - old), and its spread."""
    d = np.asarray(new_px) - np.asarray(old_px)
    return float(np.median(d)), float(d.max() - d.min())


def back_adjust(sessions, switches, spreads):
    """Difference back-adjustment: add each roll's spread to every price BEFORE that roll, so the whole history is
    expressed in the newest contract's terms. Returns new session dicts; the input is not changed."""
    out = [dict(s, px=s["px"].astype(float).copy()) for s in sessions]
    order = [s["session"] for s in sessions]
    for sw in switches:
        k = order.index(sw["session"])
        for s in out[:k]:
            s["px"] += spreads[sw["session"]]
    for s in out:
        s["open"], s["close"] = float(s["px"][0]), float(s["px"][-1])
    return out


def multi_day_level(sessions, k, n=5):
    """A generic multi-day reference: the average price of the previous n sessions, used at the open of session k."""
    return float(np.mean(np.concatenate([s["px"] for s in sessions[max(0, k - n):k]])))


# ---------------------------------------------------------------- the synthetic reproduction
SPREAD = 294.50           # the calendar spread in the real case (December over September), in points
DAYS = ["2030-09-05", "2030-09-06", "2030-09-09", "2030-09-10", "2030-09-11", "2030-09-12", "2030-09-13"]
OLD, NEW = dict(symbol="NQU0", instrument_id=1001), dict(symbol="NQZ0", instrument_id=1002)


def synthetic_case(seed=3):
    """Two contracts with a constant 294.50-point spread, one minute per row, 23-hour sessions. Two sources:
      A  a platform-style continuous minute record that switches to December at the session that starts after the
         weekend (2030-09-09), like the real record did;
      B  a book rebuilt from a continuous-by-volume MBO feed, which switches two sessions later (2030-09-11), when
         December's volume overtakes September's.
    The weekend brings a real 305.25-point drop, so A's raw gap at its switch is an ordinary-looking -10.75 points."""
    rng = np.random.default_rng(seed)
    sep = []
    p = 20000.0
    for d, day in enumerate(DAYS):
        if day == "2030-09-09":
            p -= 305.25                                            # the weekend move
        elif d > 0:
            p += np.round(rng.normal(0, 8) / TICK) * TICK          # an ordinary gap across the daily break
        steps = np.round(rng.normal(0, 1.2, 1380) / TICK) * TICK
        steps[0] = 0.0
        path = p + np.cumsum(steps)
        sep.append(path)
        p = path[-1]
    dec = [x + SPREAD for x in sep]                                # same market, other contract
    a_switch, b_switch = DAYS.index("2030-09-09"), DAYS.index("2030-09-11")

    def source(switch):
        out = []
        for k, day in enumerate(DAYS):
            c = NEW if k >= switch else OLD
            px = dec[k] if k >= switch else sep[k]
            out.append(dict(session=day, px=px, open=float(px[0]), close=float(px[-1]), **c))
        return out
    return source(a_switch), source(b_switch), sep, dec


def run_case(log=print):
    A, B, sep, dec = synthetic_case()
    res = {}
    log("1. Symptom: a multi-day level built from source A does not match the same level built from source B.")
    k = DAYS.index("2030-09-11")
    la, lb = multi_day_level(A, k), multi_day_level(B, k)
    log(f"   5-session average at the open of {DAYS[k]}: A {la:,.2f}   B {lb:,.2f}   difference {la - lb:+,.2f} points")
    res["level_diff_before"] = la - lb

    log("2. The obvious check finds nothing: A's day-to-day gaps look normal.")
    for g in gap_screen(A):
        log(f"   {g['session']}  gap {g['gap']:+8.2f}  {'FLAGGED' if g['flagged'] else 'ok'}  (limit +/-{g['limit']:.2f})")
    res["gap_flags"] = [g["session"] for g in gap_screen(A) if g["flagged"]]

    log("3. Compare the two sources at the same minutes, session by session.")
    for c in compare_sources(A, B):
        log(f"   {c['session']}  A-B median {c['median']:+8.2f}  (min {c['min']:+.2f}, max {c['max']:+.2f})   A={c['a']}  B={c['b']}")
    cmp_ = compare_sources(A, B)
    bad = [c for c in cmp_ if abs(c["median"]) > TICK / 2]
    log(f"   they agree to the tick except on {', '.join(c['session'] for c in bad)}, by a constant {bad[0]['median']:+.2f}:"
        " a constant offset over whole sessions is a contract difference, not a data glitch.")
    res["disagree"] = [c["session"] for c in bad]

    log("4. Ask the instrument, not the price: where does each source change contract?")
    swa, swb = find_switches(A), find_switches(B)
    for name, sw in (("A", swa), ("B", swb)):
        for s in sw:
            log(f"   source {name}: {s['old']} -> {s['new']} at session {s['session']} (raw gap there {s['gap']:+.2f})")

    log("5. Measure the spread with both contracts at the same timestamps (the sessions where the sources disagree).")
    sp, rng_ = measure_spread(np.concatenate([B[DAYS.index(c['session'])]["px"] for c in bad]),
                              np.concatenate([A[DAYS.index(c['session'])]["px"] for c in bad]))
    log(f"   {NEW['symbol']} - {OLD['symbol']} = {sp:+.2f} points (range {rng_:.2f})")
    res["spread"] = sp

    log("6. Fix: back-adjust each source at its own switch, then re-check.")
    A2 = back_adjust(A, swa, {s["session"]: sp for s in swa})
    B2 = back_adjust(B, swb, {s["session"]: sp for s in swb})
    worst = max(abs(c["median"]) for c in compare_sources(A2, B2))
    la2, lb2 = multi_day_level(A2, k), multi_day_level(B2, k)
    log(f"   largest A-B difference on any session after adjusting: {worst:.2f} points")
    log(f"   5-session average at the open of {DAYS[k]}: A {la2:,.2f}   B {lb2:,.2f}   (true December-terms value {multi_day_level([dict(px=x) for x in dec], k):,.2f})")
    res.update(worst_after=worst, level_diff_after=la2 - lb2)
    return res
