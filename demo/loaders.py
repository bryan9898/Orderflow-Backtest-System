"""Data loaders. Both return the SAME thing: an iterator of numpy record arrays with Databento's MBO field names
(ts_recv, ts_event, instrument_id, order_id, price, size, flags, action, side, sequence), so everything after this
point cannot tell a real Databento file from the synthetic demo file.

    load_dbn(path)        a real Databento GLBX.MDP3 `mbo` file (.dbn or .dbn.zst) - needs `pip install databento`
    load_synthetic(path)  the synthetic demo file written by synth.py (CSV.gz with the same columns)

No API key is used here: real files are read from disk. Downloading is a separate step (see README, "Real data").
"""
import numpy as np
import pandas as pd

MBO_FIELDS = ["ts_recv", "ts_event", "instrument_id", "order_id", "price", "size", "flags", "action", "side", "sequence"]
MBO_DTYPE = np.dtype([("ts_recv", "<u8"), ("ts_event", "<u8"), ("instrument_id", "<u4"), ("order_id", "<u8"),
                      ("price", "<i8"), ("size", "<u4"), ("flags", "u1"), ("action", "S1"), ("side", "S1"), ("sequence", "<u4")])


def load_dbn(path, chunk=1_000_000):
    """Stream a Databento MBO file in chunks (a full NQ day is tens of millions of records, too big for one DataFrame)."""
    import databento as db                       # imported here so the synthetic demo runs without the package
    store = db.DBNStore.from_file(path)
    meta = dict(dataset=store.dataset, schema=str(store.schema), symbols=list(store.symbols or []),
                stype_in=str(store.stype_in), start=str(store.start), end=str(store.end), source=str(path))
    if str(store.schema) != "mbo":
        raise ValueError(f"expected an mbo file, got schema {store.schema}")

    def chunks():
        for a in store.to_ndarray(count=chunk):
            out = np.empty(len(a), MBO_DTYPE)
            for f in MBO_FIELDS:
                out[f] = a[f]
            yield out
    return meta, chunks()


def load_synthetic(path, chunk=1_000_000):
    df = pd.read_csv(path, dtype={"action": str, "side": str})
    meta = dict(dataset="SYNTHETIC (shaped like GLBX.MDP3)", schema="mbo", symbols=["NQ.SYN"], stype_in="synthetic",
                start=str(pd.Timestamp(int(df.ts_recv.iloc[0]), tz="UTC")), end=str(pd.Timestamp(int(df.ts_recv.iloc[-1]), tz="UTC")),
                source=str(path))
    out = np.empty(len(df), MBO_DTYPE)
    for f in MBO_FIELDS:
        out[f] = df[f].to_numpy().astype("S1") if f in ("action", "side") else df[f].to_numpy()

    def chunks():
        for i in range(0, len(out), chunk):
            yield out[i:i + chunk]
    return meta, chunks()
