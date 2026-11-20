"""One place for every setting of the demo run. Nothing here is a private strategy rule."""

# ---------------------------------------------------------------- the data the app is built for (real runs)
DATASET = "GLBX.MDP3"        # CME Globex MDP 3.0, via Databento
SCHEMA = "mbo"               # market by order: every add / cancel / modify / trade / fill, with order IDs
SYMBOL = "NQ.v.0"            # E-mini Nasdaq-100 futures, continuous front month (volume-based roll)
STYPE_IN = "continuous"
TICK = 0.25                  # NQ tick size in index points
POINT_USD = 20.0             # NQ dollar value of one index point (MNQ = 2.0)
PRICE_SCALE = 1_000_000_000  # Databento fixed-point prices: 1 unit = 1e-9

# ---------------------------------------------------------------- the public demo run (synthetic data)
SYNTH_SEED = 7
SYNTH_DATE = "2030-01-07"    # an obviously fake date: the demo day is SYNTHETIC, not market history
SYNTH_START_NY = "09:30"
SYNTH_MINUTES = 120          # 09:30 to 11:30 New York
SYNTH_START_PRICE = 20000.00

# ---------------------------------------------------------------- the demo signal (a generic textbook order-flow rule)
FLOW_WINDOW_S = 30           # aggressor delta is summed over the last 30 s
FLOW_LOOKBACK_S = 1800       # its threshold is a percentile of the previous 30 minutes (relative to the market)
FLOW_PCTL = 95.0
BOOK_LEVELS = 5              # book imbalance uses the best 5 price levels each side
IMB_MIN = 0.20               # (bid depth - ask depth) / (bid depth + ask depth) must agree by this much
WARMUP_S = 600               # no signal in the first 10 minutes (threshold needs history)
COOLDOWN_S = 120             # at least 2 minutes between signals

# ---------------------------------------------------------------- the trade and the fills
STOP_PTS = 8.0
TARGET_PTS = 16.0
MAX_HOLD_S = 600             # time exit after 10 minutes
COST_PTS = 0.62              # commission + fees per round turn, in points
STOP_SLIP_TICKS = 1          # stop is filled one tick worse than the stop price
TARGET_THROUGH_TICKS = 1     # a target counts only if price trades one tick through it
RANDOM_CONTROL_N = 20        # control: the same bracket at 20 random seconds per signal
