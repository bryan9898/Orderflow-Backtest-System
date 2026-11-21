# Case study: the 294.50-point contract roll

A troubleshooting write-up from the research project this tool came from, with a synthetic reproduction you can run:

```bash
python -m ofbt rollcase       # the reproduction, step by step
python tests/test_rolls.py    # the same steps as a test
```

## The setting

Two price sources for NQ futures were used side by side:

- **Minute bars** exported from a trading platform's continuous chart. These went back further and were used for multi-day reference levels: multi-session profiles, prior closes, 20-session moves.
- **Per-second books** rebuilt from Databento `GLBX.MDP3` `mbo` data for `NQ.v.0` (continuous, rolled by volume) with the same rebuild as `ofbt/book.py`. These were used for entries and fills.

## The symptom

Around the September 2026 roll, multi-day levels stopped lining up with the per-second book. When a multi-day reference level for 17 Sep 2026 was rebuilt independently, it came out on the other side of the price.

## The first check found nothing

The first thing to check for a roll is a price jump between sessions. The minute record's gap into the session where it changed contract was **−10.75 points**, an ordinary overnight gap. A screen for unusual gaps would not have flagged it.

## How it was found

The two sources were compared at the same timestamps, session by session:

| Session | Minute record − per-second book |
|---|---|
| 11 Sep 2026 | 0 (agree to the tick) |
| 14 Sep 2026 | **+294.50** |
| 15 Sep 2026 | **+294.50** |
| 16 Sep 2026 | 0 (agree to the tick) |

A glitch in one source would come and go, or vary in size. Instead, the offset was **constant to the tick across two whole sessions**, which means the two sources were quoting different contracts:

- The platform's continuous chart switched to the **December** contract at the 18:00 break into the 14 Sep session.
- Databento's `NQ.v.0` rolls **by volume**, and stayed on **September** until the 16 Sep session.

So December traded 294.50 points over September, and the minute record had carried that step into its history without adjusting it. Every multi-day level whose look-back crossed 14 Sep mixed September prices with December prices. The gap looked ordinary only because the real market move across that break happened to cancel most of the 294.50.

## What was changed

- **Data warning:** it was added to the research notes. Any rule comparing prices across that break adds +294.50 points to every minute bar before the 14 Sep session (a difference back-adjustment), or drops the days whose look-back crosses it. Ranges within one session are not affected.
- **Roll sessions:** these were excluded from tests.
- **June 2025 roll:** the same reasoning flagged it as suspect. The minute record shows a +120.00 move into the 16 Jun 2025 session and nothing was adjusted. There was no per-second data to measure it against, so results whose look-back crosses mid-June 2025 were tagged rather than corrected.
- **In this repository:** the checks are written as reusable code in `ofbt/rolls.py`, and each saved session records its `instrument_id`, so a multi-day study can see which contract every session is on.

## The synthetic reproduction

`ofbt/rolls.py`, `synthetic_case()` builds seven sessions of two contracts, `NQU0` and `NQZ0`, with a constant 294.50-point spread. It has two sources: **A** switches contract at the session after a weekend, like the platform chart did; **B** switches two sessions later, like the volume roll. The weekend includes a real 305.25-point drop, so A's raw gap at its switch is −10.75, as in the real case. The dates are deliberately fake.

`python -m ofbt rollcase` prints:

```
1. Symptom: a multi-day level built from source A does not match the same level built from source B.
   5-session average at the open of 2030-09-11: A 20,042.33   B 19,895.08   difference +147.25 points
2. The obvious check finds nothing: A's day-to-day gaps look normal.
   2030-09-06  gap    -0.25  ok  (limit +/-31.50)
   2030-09-09  gap   -10.75  ok  (limit +/-31.50)
   2030-09-10  gap   -13.75  ok  (limit +/-31.50)
   2030-09-11  gap   +10.25  ok  (limit +/-31.50)
   2030-09-12  gap    -5.50  ok  (limit +/-31.50)
   2030-09-13  gap    +1.00  ok  (limit +/-31.50)
3. Compare the two sources at the same minutes, session by session.
   2030-09-05  A-B median    +0.00  (min +0.00, max +0.00)   A=NQU0  B=NQU0
   2030-09-06  A-B median    +0.00  (min +0.00, max +0.00)   A=NQU0  B=NQU0
   2030-09-09  A-B median  +294.50  (min +294.50, max +294.50)   A=NQZ0  B=NQU0
   2030-09-10  A-B median  +294.50  (min +294.50, max +294.50)   A=NQZ0  B=NQU0
   2030-09-11  A-B median    +0.00  (min +0.00, max +0.00)   A=NQZ0  B=NQZ0
   2030-09-12  A-B median    +0.00  (min +0.00, max +0.00)   A=NQZ0  B=NQZ0
   2030-09-13  A-B median    +0.00  (min +0.00, max +0.00)   A=NQZ0  B=NQZ0
   they agree to the tick except on 2030-09-09, 2030-09-10, by a constant +294.50: a constant offset over whole sessions is a contract difference, not a data glitch.
4. Ask the instrument, not the price: where does each source change contract?
   source A: NQU0 -> NQZ0 at session 2030-09-09 (raw gap there -10.75)
   source B: NQU0 -> NQZ0 at session 2030-09-11 (raw gap there +304.75)
5. Measure the spread with both contracts at the same timestamps (the sessions where the sources disagree).
   NQZ0 - NQU0 = +294.50 points (range 0.00)
6. Fix: back-adjust each source at its own switch, then re-check.
   largest A-B difference on any session after adjusting: 0.00 points
   5-session average at the open of 2030-09-11: A 20,189.58   B 20,189.58   (true December-terms value 20,189.58)
```

The gap screen gets it wrong both ways. On source A it misses the switch. On source B it would flag both the real 305-point weekend drop and the 304.75 switch gap, and it cannot tell the market move from the contract change. Only the instrument can.

## Doing this with Databento

- **Which contract each record is on:** every MBO record carries `instrument_id`. For a continuous symbol, the mapping from `NQ.v.0` to the actual contract changes on roll dates. It can be listed ahead of time:

  ```python
  import databento as db
  client = db.Historical()          # key from DATABENTO_API_KEY
  m = client.symbology.resolve(dataset="GLBX.MDP3", symbols=["NQ.v.0"], stype_in="continuous",
                               stype_out="instrument_id", start_date="2026-09-08", end_date="2026-09-19")
  print(m["result"])                # one entry per date range: which instrument NQ.v.0 was on
  ```

- **Measure the spread from both contracts at the same time:** request the two raw symbols for the same window (for example `symbols=["NQU6", "NQZ6"], stype_in="raw_symbol"`) and take the median difference of their mids. This is what step 5 does. Check the cost with `client.metadata.get_cost(...)` first.
- **Choose the roll rule on purpose:** `.v.` (volume), `.n.` (open interest) and `.c.` (calendar) continuous symbols roll on different days. Two tools using different rules will disagree for a few sessions around every roll, by exactly the spread, as here.

## Lessons

1. Detect rolls from the instrument (`instrument_id` or the raw symbol), never from price gaps.
2. When two sources disagree, look at the shape of the difference. A constant offset over whole sessions means different contracts; a difference that varies from moment to moment points to a timing or data problem.
3. Measure the spread with both contracts at the same timestamps, then back-adjust and re-run the comparison. It should come back to zero.
4. When a roll can't be measured, tag the results that depend on it instead of guessing a correction.
