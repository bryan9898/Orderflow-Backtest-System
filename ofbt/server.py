"""Local web server for the replay and backtest app (standard library only). Binds to 127.0.0.1 by default.

    GET  /                               the app (web/)
    GET  /api/sessions                   session cards
    GET  /api/session/<id>/sec.bin       int32, n x 7: bb, ba, lo_b, hi_b, lo_a, hi_a, heat_base (ticks), one row a second
    GET  /api/session/<id>/heat.bin      uint16, n x 101 bid sizes then n x 101 ask sizes (ticks heat_base .. +100)
    GET  /api/session/<id>/tape.bin      int32, m x 4: ms since session start, tick, size, aggressor (+1 buy, -1 sell)
    GET  /api/config                     fill-model constants (the sim trader in the browser uses the same ones)
    GET  /api/strategies                 strategy specs (built-in + user_strategies/)
    POST /api/backtest                   {strategy, params, sessions}  -> trades, metrics, controls
    POST /api/sweep                      {strategy, params, grid: {param: [values]}, train, test} -> grid + out-of-sample
"""
import json
import mimetypes
import os
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

import numpy as np

from . import book, config as C, engine, store, strategies

WEB = os.path.join(store.ROOT, "web")
_bin, _bin_lock = {}, threading.Lock()


def session_bin(sid, kind):
    key = (sid, kind)
    with _bin_lock:
        if key in _bin:
            return _bin[key]
    B = store.load(sid)
    if kind == "sec":
        a = np.stack([B["bb"], B["ba"], B["lo_b"], B["hi_b"], B["lo_a"], B["hi_a"], B["heat_base"]], 1).astype("<i4")
    elif kind == "heat":
        a = np.concatenate([B["heat_bid"].ravel(), B["heat_ask"].ravel()]).astype("<u2")
    elif kind == "tape":
        ms = (B["tr_ns"] - int(B["t"][0]) * 1_000_000_000) // 1_000_000
        a = np.stack([ms, B["tr_tk"], B["tr_sz"], B["tr_sd"]], 1).astype("<i4")
    else:
        raise KeyError(kind)
    data = a.tobytes()
    with _bin_lock:
        _bin[key] = data
    return data


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, float) and (o != o or o in (float("inf"), float("-inf"))):
        return None
    return o


class Handler(BaseHTTPRequestHandler):
    server_version = "ofbt"

    def log_message(self, fmt, *args):
        if args and "/api/" in str(args[0]):
            print(f"{time.strftime('%H:%M:%S')} {fmt % args}")

    def _send(self, code, body, ctype="application/json", cache=False):
        if isinstance(body, (dict, list)):
            body = json.dumps(_clean(body), separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "max-age=3600" if cache else "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        try:
            if path == "/api/sessions":
                return self._send(200, store.list_sessions())
            if path == "/api/config":
                return self._send(200, dict(tick=C.TICK, point_usd=C.POINT_USD, cost_pts=C.COST_PTS, stop_slip_ticks=C.STOP_SLIP_TICKS,
                                            target_through_ticks=C.TARGET_THROUGH_TICKS, heat_half=book.HEAT))
            if path == "/api/strategies":
                return self._send(200, [c.spec() for c in strategies.registry().values()])
            if path.startswith("/api/session/"):
                _, _, _, sid, fn = path.split("/", 4)
                if fn == "card":
                    return self._send(200, store.card(sid))
                return self._send(200, session_bin(sid, fn.replace(".bin", "")), "application/octet-stream", cache=True)
            fn = os.path.normpath(os.path.join(WEB, path.lstrip("/") or "index.html"))
            if not fn.startswith(WEB + os.sep) or not os.path.isfile(fn):
                return self._send(404, dict(error="not found"))
            with open(fn, "rb") as f:
                return self._send(200, f.read(), mimetypes.guess_type(fn)[0] or "application/octet-stream")
        except KeyError as e:
            return self._send(404, dict(error=f"unknown: {e}"))
        except Exception as e:
            traceback.print_exc()
            return self._send(500, dict(error=str(e)))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            reg = strategies.registry()
            cls = reg[req["strategy"]]
            t0 = time.time()
            if path == "/api/backtest":
                out = engine.backtest_run(cls, req.get("params", {}), req["sessions"], seed=int(req.get("seed", 1)))
            elif path == "/api/sweep":
                grid = {k: [type(cls.params[k].default)(v) for v in vals] for k, vals in req["grid"].items()}
                out = engine.sweep(cls, cls(**req.get("params", {})).p, grid, req["train"], req.get("test", []),
                                   min_trades=int(req.get("min_trades", 5)))
            else:
                return self._send(404, dict(error="not found"))
            out["seconds"] = round(time.time() - t0, 2)
            return self._send(200, out)
        except KeyError as e:
            return self._send(400, dict(error=f"missing or unknown: {e}"))
        except Exception as e:
            traceback.print_exc()
            return self._send(400, dict(error=str(e)))


def serve(host="127.0.0.1", port=8765, open_browser=True):
    if not store.list_sessions():
        print("no sessions yet: building the synthetic sessions first (about 30 s each) ...")
        store.build_synthetic(log=lambda s: print("  " + s))
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"replay + backtest app on {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
