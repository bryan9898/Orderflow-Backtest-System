"""Hand-written MBO messages with a known answer: run `python -m pytest tests` (or `python tests/test_book.py`)."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from ofbt import book
from ofbt.loaders import MBO_DTYPE

U = 250_000_000                       # one 0.25 tick in Databento fixed-point price units
S = 1_000_000_000                     # one second in ns
T0 = 1_900_000_000 * S


def msgs(rows):
    a = np.zeros(len(rows), MBO_DTYPE)
    for i, (sec, act, side, tick, size, oid) in enumerate(rows):
        a[i] = (T0 + sec * S, T0 + sec * S, 1, oid, tick * U if tick is not None else 9223372036854775807, size, 0, act, side, i)
    return a


def test_rebuild():
    rows = [
        (0, b"R", b"N", None, 0, 0),
        (0, b"A", b"B", 100, 5, 1), (0, b"A", b"B", 99, 7, 2), (0, b"A", b"A", 101, 4, 3), (0, b"A", b"A", 102, 6, 4),
        (1, b"T", b"B", 101, 3, 0), (1, b"F", b"A", 101, 3, 3), (1, b"M", b"A", 101, 1, 3),      # buyer lifts 3 of 4 at 101
        (2, b"T", b"B", 101, 1, 0), (2, b"F", b"A", 101, 1, 3), (2, b"C", b"A", 101, 1, 3),      # ... and the last 1: 101 empties
        (3, b"M", b"B", 100, 9, 1),                                                               # bid at 100 resized 5 -> 9
        (3, b"C", b"B", 99, 7, 2),                                                                # bid at 99 pulled
        (4, b"A", b"A", 102, 2, 5),
    ]
    B = book.rebuild({}, iter([msgs(rows)]), log=lambda s: None)
    assert B["bb"].tolist() == [100, 100, 100, 100, 100]
    assert B["ba"].tolist() == [101, 101, 102, 102, 102]
    assert B["bid_lad"][0, :2].tolist() == [5, 7] and B["bid_lad"][3, :2].tolist() == [9, 0]
    assert B["ask_lad"][1, 0] == 1 and B["ask_lad"][4, 0] == 8
    assert B["tr_sz"].tolist() == [3, 1] and B["tr_sd"].tolist() == [1, 1]
    assert B["hi_b"][1] == 101                # the trade at 101 counts for target checks in that second


if __name__ == "__main__":
    test_rebuild(); print("ok")
