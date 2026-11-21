"""The contract-roll case study (docs/case_study_roll.md) on synthetic data: a gap screen misses the switch, comparing
two sources finds it, the spread is measured to the tick, and back-adjusting makes the sources agree."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ofbt import rolls                                             # noqa: E402


def main():
    r = rolls.run_case(log=lambda s: None)
    assert "2030-09-09" not in r["gap_flags"], "the hidden switch should look like an ordinary gap"
    assert r["disagree"] == ["2030-09-09", "2030-09-10"], r["disagree"]
    assert r["spread"] == rolls.SPREAD, r["spread"]
    assert r["worst_after"] == 0.0 and r["level_diff_after"] == 0.0, r
    assert abs(r["level_diff_before"]) > 100, r
    print(f"roll case: ok (spread {r['spread']:+.2f}, multi-day level off by {r['level_diff_before']:+.2f} before the fix, 0 after)")


if __name__ == "__main__":
    main()
