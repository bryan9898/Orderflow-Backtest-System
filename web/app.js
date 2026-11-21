// Order-flow replay + backtest app. Plain ES module, no dependencies.
// Data comes from the local Python server (ofbt/server.py) as compact binary arrays; everything is drawn on canvas.

const $ = (s) => document.querySelector(s);
const $$ = (s) => [...document.querySelectorAll(s)];
const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const fmtInt = (v) => Math.round(v).toLocaleString("en-US");
const fmtUsd = (v) => (v < 0 ? "-$" : "$") + Math.abs(Math.round(v)).toLocaleString("en-US");
const sgn = (v, d = 3) => (v > 0 ? "+" : "") + Number(v).toFixed(d);
const cls = (v) => (v > 0 ? "pos" : v < 0 ? "neg" : "");
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const st = {
  cfg: null, sessions: [], strategies: [], sid: null, S: null,
  tf: 60, now: 0, playing: false, speed: 10, follow: true,
  view: { a: 0, b: 3600 }, y: { auto: true, lo: 0, hi: 1 },
  show: { heat: true, vwap: true, bub: true, trades: true },
  bt: null, sel: null, sweep: null, hover: null,
  sim: { open: null, closed: [] }, dirty: true, theme: null,
};

// ------------------------------------------------------------------ data
async function getJSON(url, body) {
  const r = await fetch(url, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

async function loadSession(id) {
  status(`<span class="spin"></span> loading ${esc(id)} ...`);
  const card = st.sessions.find((c) => c.id === id);
  const [sec, heat, tape] = await Promise.all(["sec", "heat", "tape"].map((k) => fetch(`/api/session/${encodeURIComponent(id)}/${k}.bin?v=${card.records}-${card.trades}-${card.seconds}`).then((r) => r.arrayBuffer())));
  const s = new Int32Array(sec), n = s.length / 7, W = 2 * card.heat_half + 1;
  const col = (k) => { const a = new Int32Array(n); for (let i = 0; i < n; i++) a[i] = s[i * 7 + k]; return a; };
  const S = { card, n, W, bb: col(0), ba: col(1), lob: col(2), hib: col(3), loa: col(4), hia: col(5), hbase: col(6) };
  const h = new Uint16Array(heat);
  S.hBid = h.subarray(0, n * W); S.hAsk = h.subarray(n * W);
  const tp = new Int32Array(tape), m = tp.length / 4;
  S.m = m; S.tms = new Int32Array(m); S.ttk = new Int32Array(m); S.tsz = new Int32Array(m); S.tsd = new Int8Array(m);
  for (let i = 0; i < m; i++) { S.tms[i] = tp[4 * i]; S.ttk[i] = tp[4 * i + 1]; S.tsz[i] = tp[4 * i + 2]; S.tsd[i] = tp[4 * i + 3]; }
  // per-second aggregates from the tape
  S.first = new Int32Array(n + 1);
  S.o = new Int32Array(n); S.h = new Int32Array(n); S.l = new Int32Array(n); S.c = new Int32Array(n);
  S.vol = new Float64Array(n); S.buy = new Float64Array(n); S.sell = new Float64Array(n);
  S.vwap = new Float64Array(n); S.cvd = new Float64Array(n); S.pxv = new Float64Array(n);
  let j = 0, pv = 0, cv = 0, cd = 0, prev = Math.round((S.bb[0] + S.ba[0]) / 2);
  for (let i = 0; i < n; i++) {
    S.first[i] = j;
    let o = -1, hi = -1e9, lo = 1e9, c = prev, spv = 0;
    while (j < m && S.tms[j] < (i + 1) * 1000) {
      const p = S.ttk[j], q = S.tsz[j];
      if (o < 0) o = p;
      if (p > hi) hi = p; if (p < lo) lo = p; c = p;
      S.vol[i] += q; spv += p * q;
      if (S.tsd[j] > 0) S.buy[i] += q; else if (S.tsd[j] < 0) S.sell[i] += q;
      j++;
    }
    if (o < 0) { o = hi = lo = c = prev; }
    S.o[i] = o; S.h[i] = hi; S.l[i] = lo; S.c[i] = c; prev = c;
    S.pxv[i] = S.vol[i] ? spv / S.vol[i] : c;
    pv += spv; cv += S.vol[i]; cd += S.buy[i] - S.sell[i];
    S.vwap[i] = cv ? pv / cv : c; S.cvd[i] = cd;
  }
  S.first[n] = m;
  S.heatCap = pctl(sample(S.hBid, 40000).concat(sample(S.hAsk, 40000)).filter((v) => v > 0), 0.985) || 50;
  const vs = Array.from(S.vol).filter((v) => v > 0);
  S.bigVol = pctl(vs, 0.985);
  S.bigTrade = pctl(sample(S.tsz, 50000), 0.995);
  S.t0 = Date.parse(card.start) / 1000;
  const mt = /([+-])(\d\d):(\d\d)$/.exec(card.start);
  S.off = mt ? (mt[1] === "-" ? -1 : 1) * (+mt[2] * 60 + +mt[3]) * 60 : 0;
  st.S = S; st.sid = id; st.bars = {};
  st.sim = { open: null, closed: st.sim.closed };
  $("#slider").max = n - 1;
  $("#kind").textContent = card.kind === "synthetic" ? "synthetic" : "Databento · private";
  $("#kind").className = "tag" + (card.kind === "synthetic" ? "" : " live");
  $("#sessInfo").textContent = `${card.contract} · ${fmtInt(card.trades)} trades · ${fmtInt(card.records)} MBO msgs`;
  setNow(n - 1, false);
  fitAll();
  status("");
  markDirty();
}

function sample(a, k) { const out = [], step = Math.max(1, Math.floor(a.length / k)); for (let i = 0; i < a.length; i += step) out.push(a[i]); return out; }
function pctl(a, q) { if (!a.length) return 0; const b = Float64Array.from(a).sort(); return b[Math.min(b.length - 1, Math.floor(q * b.length))]; }

function bars(tf) {
  const S = st.S;
  if (st.bars[tf]) return st.bars[tf];
  const k = Math.ceil(S.n / tf), B = { tf, k, o: new Int32Array(k), h: new Int32Array(k), l: new Int32Array(k), c: new Int32Array(k), buy: new Float64Array(k), sell: new Float64Array(k) };
  for (let b = 0; b < k; b++) fillBar(B, b, Math.min(S.n - 1, (b + 1) * tf - 1));
  return (st.bars[tf] = B);
}
function fillBar(B, b, last) {             // bar b using seconds b*tf .. last (last < the bar end while replaying)
  const S = st.S, a = b * B.tf;
  let hi = -1e9, lo = 1e9, buy = 0, sell = 0;
  for (let i = a; i <= last; i++) { if (S.h[i] > hi) hi = S.h[i]; if (S.l[i] < lo) lo = S.l[i]; buy += S.buy[i]; sell += S.sell[i]; }
  B.o[b] = S.o[a]; B.h[b] = hi; B.l[b] = lo; B.c[b] = S.c[last]; B.buy[b] = buy; B.sell[b] = sell;
}

// ------------------------------------------------------------------ time + view helpers
const nowS = () => Math.floor(st.now);
function clock(s, ms = false) {
  const S = st.S; if (!S) return "--:--:--";
  const d = new Date((S.t0 + s + S.off) * 1000);
  const p = (v, k = 2) => String(v).padStart(k, "0");
  return `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}` + (ms ? "." + p(Math.floor((s % 1) * 1000), 3) : "");
}
function setNow(v, keepView = true) {
  const S = st.S; if (!S) return;
  const old = st.now;
  st.now = Math.max(0, Math.min(S.n - 1, v));
  if (st.now < old && st.sim.open) { st.sim.open = null; toast("Sim position cancelled (replay moved back)"); }
  $("#slider").value = nowS();
  $("#clock").textContent = clock(nowS());
  const full = nowS() >= S.n - 1;
  $("#mode").textContent = full ? "full session" : "replay";
  $("#mode").className = "tag" + (full ? "" : " live");
  if (keepView && st.follow) followView();
  simUpdate();
  markDirty();
}
function followView() {
  const w = st.view.b - st.view.a;                  // the replay cursor sits at 80% of the width; history scrolls left
  st.view.a = st.now + 1 - 0.8 * w; st.view.b = st.view.a + w;
}
function fitAll() { const S = st.S; st.view = { a: -S.n * 0.01, b: S.n * 1.04 }; st.y.auto = true; }
function frame(a, b) { st.view = { a, b }; st.y.auto = true; markDirty(); }
function markDirty() { st.dirty = true; }

// ------------------------------------------------------------------ chart
const cv = $("#chart"), ctx = cv.getContext("2d");
const AX = 70, TAXIS = 22, LOWER = 96;
let W = 0, H = 0, dpr = 1, heatImg = null, heatCanvas = document.createElement("canvas");

function resize() {
  const r = cv.getBoundingClientRect();
  dpr = window.devicePixelRatio || 1;
  W = Math.max(200, r.width); H = Math.max(200, r.height);
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  const d = $("#dom"), dr = d.getBoundingClientRect();
  d.width = Math.round(dr.width * dpr); d.height = Math.round(dr.height * dpr);
  markDirty();
}
new ResizeObserver(resize).observe($("#chartWrap"));
new ResizeObserver(resize).observe($("#domBox"));

const geo = () => {
  const pw = W - AX, mainH = H - TAXIS - LOWER - 6;
  return { pw, mainH, lowTop: mainH + 6, lowH: LOWER, x: (s) => ((s - st.view.a) / (st.view.b - st.view.a)) * pw, s: (x) => st.view.a + (x / pw) * (st.view.b - st.view.a) };
};

function autoY(g) {
  if (!st.y.auto) return;
  const S = st.S, B = bars(st.tf), ns = nowS();
  const b0 = Math.max(0, Math.floor(st.view.a / st.tf)), b1 = Math.min(B.k - 1, Math.floor(Math.min(st.view.b, ns) / st.tf));
  let lo = 1e9, hi = -1e9;
  for (let b = b0; b <= b1; b++) { const last = Math.min(ns, (b + 1) * st.tf - 1); if (last < b * st.tf) continue; if (last < (b + 1) * st.tf - 1) fillBar(B, b, last); if (B.h[b] > hi) hi = B.h[b]; if (B.l[b] < lo) lo = B.l[b]; }
  if (hi < lo) { lo = S.bb[ns] - 10; hi = S.ba[ns] + 10; }
  const pad = Math.max(4, (hi - lo) * 0.08);
  st.y.lo = lo - pad; st.y.hi = hi + pad;
}

function rampLUT(stops, bg) {
  const lut = new Uint32Array(256);
  const hex = (h) => [parseInt(h.slice(1, 3), 16), parseInt(h.slice(3, 5), 16), parseInt(h.slice(5, 7), 16)];
  const S = stops.map((s) => [s[0], hex(s[1])]), B = hex(bg);
  for (let i = 0; i < 256; i++) {
    const v = i / 255;
    let k = 0; while (k < S.length - 2 && v > S[k + 1][0]) k++;
    const [a, ca] = S[k], [b, cb] = S[k + 1], f = Math.min(1, Math.max(0, (v - a) / (b - a)));
    const c = ca.map((x, j) => x + (cb[j] - x) * f);
    const al = Math.min(1, v * 1.6);
    const r = B[0] + (c[0] - B[0]) * al, g = B[1] + (c[1] - B[1]) * al, bl = B[2] + (c[2] - B[2]) * al;
    lut[i] = (255 << 24) | (Math.round(bl) << 16) | (Math.round(g) << 8) | Math.round(r);
  }
  return lut;
}
let LUT = null;
function buildLUTs() {
  const dark = isDark(), bg = toHex(cssVar("--chart-bg"));
  LUT = dark
    ? { bid: rampLUT([[0, "#0c2a5c"], [0.45, "#1479c9"], [0.8, "#38d9e8"], [1, "#eafcff"]], bg), ask: rampLUT([[0, "#4a1420"], [0.45, "#b5402a"], [0.8, "#f4a13a"], [1, "#fff6d0"]], bg), bg }
    : { bid: rampLUT([[0, "#d8e9ff"], [0.5, "#5b9cf5"], [1, "#163a8a"]], bg), ask: rampLUT([[0, "#ffe4cc"], [0.5, "#f58b42"], [1, "#8a2c0c"]], bg), bg };
}
function isDark() { const t = document.documentElement.dataset.theme; return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; }
function toHex(c) { if (c.startsWith("#")) return c.length === 4 ? "#" + [...c.slice(1)].map((x) => x + x).join("") : c; const m = c.match(/\d+/g); return "#" + m.slice(0, 3).map((x) => (+x).toString(16).padStart(2, "0")).join(""); }

function drawHeat(g) {
  const S = st.S, pw = Math.floor(g.pw * dpr), ph = Math.floor(g.mainH * dpr);
  if (pw <= 0 || ph <= 0) return;
  if (!heatImg || heatImg.width !== pw || heatImg.height !== ph) { heatImg = new ImageData(pw, ph); heatCanvas.width = pw; heatCanvas.height = ph; }
  const px = new Uint32Array(heatImg.data.buffer), ns = nowS(), { lo, hi } = st.y, Wd = S.W;
  const bgc = LUT.bid[0] & 0 | rampBg();
  const rowTick = new Int32Array(ph);
  for (let y = 0; y < ph; y++) rowTick[y] = Math.round(hi - ((y + 0.5) / ph) * (hi - lo));
  const scale = 255 / Math.log1p(S.heatCap);
  for (let x = 0; x < pw; x++) {
    const s = Math.floor(st.view.a + ((x + 0.5) / pw) * (st.view.b - st.view.a));
    if (s < 0 || s > ns || s >= S.n) { for (let y = 0; y < ph; y++) px[y * pw + x] = bgc; continue; }
    const base = S.hbase[s], off = s * Wd, mid2 = S.bb[s] + S.ba[s];
    for (let y = 0; y < ph; y++) {
      const k = rowTick[y] - base;
      let c = bgc;
      if (k >= 0 && k < Wd) {
        const isAsk = 2 * rowTick[y] > mid2;
        const v = isAsk ? S.hAsk[off + k] : S.hBid[off + k];
        if (v > 0) c = (isAsk ? LUT.ask : LUT.bid)[Math.min(255, Math.floor(Math.log1p(v) * scale))];
      }
      px[y * pw + x] = c;
    }
  }
  heatCanvas.getContext("2d").putImageData(heatImg, 0, 0);
  ctx.drawImage(heatCanvas, 0, 0, pw, ph, 0, 0, pw / dpr, ph / dpr);
}
function rampBg() { const h = LUT.bg; return (255 << 24) | (parseInt(h.slice(5, 7), 16) << 16) | (parseInt(h.slice(3, 5), 16) << 8) | parseInt(h.slice(1, 3), 16); }

function draw() {
  const S = st.S; if (!S) return;
  if (!LUT || st.theme !== isDark()) { st.theme = isDark(); buildLUTs(); }
  const g = geo(), ns = nowS(), tk = st.cfg.tick;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = cssVar("--chart-bg"); ctx.fillRect(0, 0, W, H);
  autoY(g);
  const { lo, hi } = st.y, Y = (p) => ((hi - p) / (hi - lo)) * g.mainH;
  if (st.show.heat) drawHeat(g);

  // grid + price axis
  ctx.font = "11px " + cssVar("--mono"); ctx.textBaseline = "middle";
  const stepTicks = niceStep((hi - lo) / Math.max(3, g.mainH / 46));
  ctx.strokeStyle = cssVar("--grid"); ctx.lineWidth = 1; ctx.fillStyle = cssVar("--axis");
  for (let p = Math.ceil(lo / stepTicks) * stepTicks; p <= hi; p += stepTicks) {
    const y = Math.round(Y(p)) + 0.5; ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(g.pw, y); ctx.stroke();
    ctx.fillText((p * tk).toFixed(2), g.pw + 6, y);
  }
  // time grid
  const span = st.view.b - st.view.a, tstep = niceTime(span / Math.max(2, g.pw / 110));
  ctx.textBaseline = "top";
  for (let s = Math.ceil(st.view.a / tstep) * tstep; s <= st.view.b; s += tstep) {
    const x = Math.round(g.x(s)) + 0.5; if (x < 0 || x > g.pw) continue;
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H - TAXIS); ctx.stroke();
    ctx.fillText(clock(s).slice(0, tstep >= 60 ? 5 : 8), x - 18, H - TAXIS + 5);
  }

  ctx.save(); ctx.beginPath(); ctx.rect(0, 0, g.pw, g.mainH); ctx.clip();
  // VWAP
  if (st.show.vwap) {
    ctx.strokeStyle = cssVar("--vwap"); ctx.lineWidth = 1.5; ctx.beginPath();
    const s0 = Math.max(0, Math.floor(st.view.a)), s1 = Math.min(ns, Math.ceil(st.view.b)), step = Math.max(1, Math.floor((s1 - s0) / g.pw));
    for (let s = s0; s <= s1; s += step) { const x = g.x(s), y = Y(S.vwap[s]); s === s0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
    ctx.stroke();
  }
  // candles
  const B = bars(st.tf), bw = (st.tf / span) * g.pw;
  const b0 = Math.max(0, Math.floor(st.view.a / st.tf)), b1 = Math.min(B.k - 1, Math.floor(Math.min(st.view.b, ns) / st.tf));
  const up = cssVar("--up"), dn = cssVar("--down");
  for (let b = b0; b <= b1; b++) {
    const last = Math.min(ns, (b + 1) * st.tf - 1); if (last < b * st.tf) continue;
    fillBar(B, b, last);
    const xc = g.x(b * st.tf + st.tf / 2), col = B.c[b] >= B.o[b] ? up : dn;
    ctx.strokeStyle = col; ctx.fillStyle = col; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(Math.round(xc) + 0.5, Y(B.h[b] + 0.4)); ctx.lineTo(Math.round(xc) + 0.5, Y(B.l[b] - 0.4)); ctx.stroke();
    if (bw >= 3) { const w = Math.max(1, bw * 0.66), y1 = Y(Math.max(B.o[b], B.c[b]) + 0.4), y2 = Y(Math.min(B.o[b], B.c[b]) - 0.4); ctx.fillRect(xc - w / 2, y1, w, Math.max(1, y2 - y1)); }
  }
  // large prints
  if (st.show.bub) {
    const s0 = Math.max(0, Math.floor(st.view.a)), s1 = Math.min(ns, Math.ceil(st.view.b));
    const thr = S.bigVol * (span > 3600 ? 1.6 : 1);
    for (let s = s0; s <= s1; s++) {
      if (S.vol[s] < thr) continue;
      const r = Math.min(18, 3 + Math.sqrt(S.vol[s] / S.bigVol) * 4);
      ctx.fillStyle = S.buy[s] >= S.sell[s] ? cssVar("--bubble-buy") : cssVar("--bubble-sell");
      ctx.beginPath(); ctx.arc(g.x(s + 0.5), Y(S.pxv[s]), r, 0, 7); ctx.fill();
    }
  }
  // trades (backtest + sim)
  if (st.show.trades) {
    const mine = (st.bt ? st.bt.trades.filter((t) => t.session === st.sid && t.e_s <= ns) : []).map((t) => ({ ...t, kind: "bt" }));
    const sim = st.sim.closed.filter((t) => t.session === st.sid).map((t) => ({ ...t, kind: "sim" }));
    if (st.sim.open && st.sim.open.e_s <= ns) sim.push({ ...st.sim.open, x_s: ns, exit: null, kind: "sim", open: true });
    for (const t of mine.concat(sim)) drawTrade(t, g, Y, tk, ns);
  }
  ctx.restore();

  // replay cursor + best bid/ask tags
  if (ns < S.n - 1) {
    const x = Math.round(g.x(ns + 1)) + 0.5;
    ctx.strokeStyle = cssVar("--warn"); ctx.setLineDash([4, 4]); ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H - TAXIS); ctx.stroke(); ctx.setLineDash([]);
  }
  ctx.textBaseline = "middle";
  axisTag(Y(S.ba[ns]), (S.ba[ns] * tk).toFixed(2), dn, g);
  axisTag(Y(S.bb[ns]), (S.bb[ns] * tk).toFixed(2), up, g);

  drawLower(g, B, b0, b1, ns);

  // crosshair
  const hv = st.hover;
  if (hv && hv.x < g.pw) {
    ctx.strokeStyle = cssVar("--cross"); ctx.setLineDash([3, 3]); ctx.beginPath();
    ctx.moveTo(Math.round(hv.x) + 0.5, 0); ctx.lineTo(Math.round(hv.x) + 0.5, H - TAXIS);
    if (hv.y < g.mainH) { ctx.moveTo(0, Math.round(hv.y) + 0.5); ctx.lineTo(g.pw, Math.round(hv.y) + 0.5); }
    ctx.stroke(); ctx.setLineDash([]);
    if (hv.y < g.mainH) axisTag(hv.y, ((hi - (hv.y / g.mainH) * (hi - lo)) * tk).toFixed(2), cssVar("--muted"), g);
  }
  tooltip(g, B);
  drawDOM(); drawTape();
}

function drawTrade(t, g, Y, tk, ns) {
  const xe = g.x(t.e_s + 0.5), ye = Y(t.entry / tk), sel = st.sel && st.sel === t.key;
  const xs = Math.min(t.x_s, ns), xx = g.x(xs + 0.5);
  const long = t.side === 1, col = t.kind === "sim" ? cssVar("--warn") : long ? cssVar("--up") : cssVar("--down");
  const bg = cssVar("--chart-bg"), big = sel || (st.view.b - st.view.a) < 4 * 3600;
  if (sel) { ctx.fillStyle = cssVar("--text"); ctx.globalAlpha = 0.06; ctx.fillRect(xe - 2, 0, Math.max(4, xx - xe + 4), g.mainH); ctx.globalAlpha = 1; }
  if (t.stop != null && big) {               // stop (red) and target (green) zones for the life of the trade
    const ys = Y(t.stop / tk), yt = Y(t.target / tk), w = Math.max(2, xx - xe);
    ctx.globalAlpha = 0.22; ctx.fillStyle = cssVar("--down"); ctx.fillRect(xe, Math.min(ye, ys), w, Math.abs(ys - ye));
    ctx.fillStyle = cssVar("--up"); ctx.fillRect(xe, Math.min(ye, yt), w, Math.abs(yt - ye)); ctx.globalAlpha = 1;
    ctx.lineWidth = 1; ctx.setLineDash([3, 3]);
    ctx.strokeStyle = cssVar("--down"); ctx.beginPath(); ctx.moveTo(xe, ys); ctx.lineTo(xe + w, ys); ctx.stroke();
    ctx.strokeStyle = cssVar("--up"); ctx.beginPath(); ctx.moveTo(xe, yt); ctx.lineTo(xe + w, yt); ctx.stroke(); ctx.setLineDash([]);
  }
  if (t.exit != null && t.x_s <= ns) {
    const yx = Y(t.exit / tk);
    ctx.strokeStyle = cssVar("--text"); ctx.lineWidth = sel ? 2 : 1.2; ctx.setLineDash([5, 3]);
    ctx.beginPath(); ctx.moveTo(xe, ye); ctx.lineTo(xx, yx); ctx.stroke(); ctx.setLineDash([]);
    ctx.fillStyle = t.pts > 0 ? cssVar("--up") : cssVar("--down"); ctx.strokeStyle = bg; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.rect(xx - 4.5, yx - 4.5, 9, 9); ctx.stroke(); ctx.fill();
    if (big) {
      const lab = `${t.pts > 0 ? "+" : ""}${t.pts.toFixed(2)}`;
      ctx.font = "600 11px " + cssVar("--mono"); ctx.textBaseline = "middle";
      const tw = ctx.measureText(lab).width + 8;
      ctx.fillStyle = bg; ctx.globalAlpha = 0.85; ctx.fillRect(xx + 7, yx - 8, tw, 16); ctx.globalAlpha = 1;
      ctx.fillStyle = t.pts > 0 ? cssVar("--up") : cssVar("--down"); ctx.fillText(lab, xx + 11, yx);
    }
  }
  const s = sel ? 9 : 7;                     // entry: an arrow pointing the way of the trade, outlined so it shows on the heatmap
  ctx.fillStyle = col; ctx.strokeStyle = sel ? cssVar("--text") : bg; ctx.lineWidth = 2; ctx.beginPath();
  if (long) { ctx.moveTo(xe, ye + 1); ctx.lineTo(xe - s, ye + 2 * s); ctx.lineTo(xe + s, ye + 2 * s); }
  else { ctx.moveTo(xe, ye - 1); ctx.lineTo(xe - s, ye - 2 * s); ctx.lineTo(xe + s, ye - 2 * s); }
  ctx.closePath(); ctx.stroke(); ctx.fill();
}

function axisTag(y, text, color, g) {
  if (y < 0 || y > g.mainH) return;
  ctx.fillStyle = color; ctx.fillRect(g.pw, y - 8, AX, 16);
  ctx.fillStyle = "#fff"; ctx.fillText(text, g.pw + 6, y);
}

function drawLower(g, B, b0, b1, ns) {
  const top = g.lowTop, h = g.lowH, span = st.view.b - st.view.a, bw = (st.tf / span) * g.pw;
  ctx.strokeStyle = cssVar("--line"); ctx.beginPath(); ctx.moveTo(0, top - 3.5); ctx.lineTo(W, top - 3.5); ctx.stroke();
  let vmax = 1;
  for (let b = b0; b <= b1; b++) vmax = Math.max(vmax, B.buy[b] + B.sell[b]);
  const w = Math.max(1, bw * 0.7);
  for (let b = b0; b <= b1; b++) {
    if (Math.min(ns, (b + 1) * st.tf - 1) < b * st.tf) continue;
    const x = g.x(b * st.tf + st.tf / 2) - w / 2, hb = (B.buy[b] / vmax) * (h - 4), hs = (B.sell[b] / vmax) * (h - 4);
    ctx.fillStyle = cssVar("--up"); ctx.globalAlpha = 0.55; ctx.fillRect(x, top + h - hb, w, hb);
    ctx.fillStyle = cssVar("--down"); ctx.fillRect(x, top + h - hb - hs, w, hs); ctx.globalAlpha = 1;
  }
  // cumulative delta on its own scale
  const S = st.S, s0 = Math.max(0, Math.floor(st.view.a)), s1 = Math.min(ns, Math.ceil(st.view.b));
  if (s1 <= s0) return;
  let lo = 1e18, hi = -1e18;
  for (let s = s0; s <= s1; s++) { if (S.cvd[s] < lo) lo = S.cvd[s]; if (S.cvd[s] > hi) hi = S.cvd[s]; }
  if (hi - lo < 1) { hi += 1; lo -= 1; }
  const step = Math.max(1, Math.floor((s1 - s0) / g.pw));
  ctx.strokeStyle = cssVar("--cvd"); ctx.lineWidth = 1.4; ctx.beginPath();
  for (let s = s0; s <= s1; s += step) { const x = g.x(s), y = top + 4 + (1 - (S.cvd[s] - lo) / (hi - lo)) * (h - 8); s === s0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y); }
  ctx.stroke();
  ctx.textBaseline = "top"; ctx.font = "11px " + cssVar("--mono");
  const l1 = "volume (buy / sell)", w1 = ctx.measureText(l1).width;
  ctx.fillStyle = cssVar("--chart-bg"); ctx.globalAlpha = 0.75; ctx.fillRect(2, top, w1 + ctx.measureText("cum. delta " + fmtInt(S.cvd[s1])).width + 26, 16); ctx.globalAlpha = 1;
  ctx.fillStyle = cssVar("--axis"); ctx.fillText(l1, 6, top + 2);
  ctx.fillStyle = cssVar("--cvd"); ctx.fillText("cum. delta " + fmtInt(S.cvd[s1]), w1 + 18, top + 2);
}

function tooltip(g, B) {
  const tip = $("#tip"), hv = st.hover, S = st.S, tk = st.cfg.tick;
  let s = hv && hv.x < g.pw ? Math.floor(g.s(hv.x)) : nowS();
  s = Math.max(0, Math.min(nowS(), s));
  const b = Math.floor(s / st.tf), last = Math.min(nowS(), (b + 1) * st.tf - 1);
  if (last < b * st.tf) { tip.textContent = ""; return; }
  fillBar(B, b, last);
  const f = (v) => (v * tk).toFixed(2), d = B.buy[b] - B.sell[b];
  let txt = `${clock(b * st.tf)}  O ${f(B.o[b])}  H ${f(B.h[b])}  L ${f(B.l[b])}  C ${f(B.c[b])}\nvol ${fmtInt(B.buy[b] + B.sell[b])}  delta ${d > 0 ? "+" : ""}${fmtInt(d)}  vwap ${(S.vwap[last] * tk).toFixed(2)}`;
  if (hv && hv.y < g.mainH && hv.x < g.pw) {
    const p = Math.round(st.y.hi - (hv.y / g.mainH) * (st.y.hi - st.y.lo)), k = p - S.hbase[s];
    if (k >= 0 && k < S.W) { const v = 2 * p > S.bb[s] + S.ba[s] ? S.hAsk[s * S.W + k] : S.hBid[s * S.W + k]; txt += `\n${clock(s)}  ${f(p)}  resting ${fmtInt(v)}`; }
  }
  tip.textContent = txt;
}

function niceStep(raw) { const p = Math.pow(10, Math.floor(Math.log10(Math.max(raw, 1e-9)))); for (const m of [1, 2, 4, 5, 10]) if (m * p >= raw) return m * p; return 10 * p; }
function niceTime(raw) { for (const s of [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200]) if (s >= raw) return s; return 14400; }

// ------------------------------------------------------------------ DOM ladder + tape
function drawDOM() {
  const S = st.S, c = $("#dom"), x = c.getContext("2d"), w = c.width / dpr, h = c.height / dpr, s = nowS(), tk = st.cfg.tick;
  x.setTransform(dpr, 0, 0, dpr, 0, 0); x.fillStyle = cssVar("--panel"); x.fillRect(0, 0, w, h);
  const rh = 16, rows = Math.floor(h / rh), bb = S.bb[s], ba = S.ba[s], mid = Math.round((bb + ba) / 2);
  const top = mid + Math.floor(rows / 2), base = S.hbase[s], off = s * S.W;
  // traded volume at each price over the last 60 s
  const tb = {}, tsell = {}, j0 = S.first[Math.max(0, s - 59)], j1 = S.first[s + 1];
  for (let j = j0; j < j1; j++) { const p = S.ttk[j]; if (S.tsd[j] > 0) tb[p] = (tb[p] || 0) + S.tsz[j]; else tsell[p] = (tsell[p] || 0) + S.tsz[j]; }
  let vmax = 1, tmax = 1;
  const size = (p) => { const k = p - base; if (k < 0 || k >= S.W) return 0; return p >= ba ? S.hAsk[off + k] : p <= bb ? S.hBid[off + k] : 0; };
  for (let r = 0; r < rows; r++) { const p = top - r; vmax = Math.max(vmax, size(p)); tmax = Math.max(tmax, (tb[p] || 0) + (tsell[p] || 0)); }
  const cw = [w * 0.3, w * 0.28, w * 0.3, w * 0.12];
  x.font = "11px " + cssVar("--mono"); x.textBaseline = "middle";
  const last = S.c[s];
  for (let r = 0; r < rows; r++) {
    const p = top - r, y = r * rh, v = size(p);
    if (p === last) { x.fillStyle = cssVar("--panel-2"); x.fillRect(0, y, w, rh); }
    if (p <= bb && v) { x.fillStyle = cssVar("--up"); x.globalAlpha = 0.35; const bw = (v / vmax) * cw[0]; x.fillRect(cw[0] - bw, y + 1, bw, rh - 2); x.globalAlpha = 1; x.fillStyle = cssVar("--text"); x.textAlign = "right"; x.fillText(fmtInt(v), cw[0] - 4, y + rh / 2); }
    if (p >= ba && v) { x.fillStyle = cssVar("--down"); x.globalAlpha = 0.35; const bw = (v / vmax) * cw[2]; x.fillRect(cw[0] + cw[1], y + 1, bw, rh - 2); x.globalAlpha = 1; x.fillStyle = cssVar("--text"); x.textAlign = "left"; x.fillText(fmtInt(v), cw[0] + cw[1] + 4, y + rh / 2); }
    x.textAlign = "center"; x.fillStyle = p === bb ? cssVar("--up") : p === ba ? cssVar("--down") : cssVar("--muted");
    x.font = (p === bb || p === ba ? "600 " : "") + "11px " + cssVar("--mono");
    x.fillText((p * tk).toFixed(2), cw[0] + cw[1] / 2, y + rh / 2);
    x.font = "11px " + cssVar("--mono");
    const tv = (tb[p] || 0) + (tsell[p] || 0);
    if (tv) { const x0 = cw[0] + cw[1] + cw[2] + 2, full = cw[3] - 4, bwb = ((tb[p] || 0) / tmax) * full, bws = ((tsell[p] || 0) / tmax) * full;
      x.fillStyle = cssVar("--up"); x.fillRect(x0, y + 3, bwb, rh - 6); x.fillStyle = cssVar("--down"); x.fillRect(x0 + bwb, y + 3, bws, rh - 6); }
  }
  x.strokeStyle = cssVar("--line"); x.beginPath();
  for (const cx of [cw[0], cw[0] + cw[1], cw[0] + cw[1] + cw[2]]) { x.moveTo(Math.round(cx) + 0.5, 0); x.lineTo(Math.round(cx) + 0.5, h); }
  x.stroke();
  const b5 = sumBook(S.hBid, off, bb - base, -1), a5 = sumBook(S.hAsk, off, ba - base, 1);
  $("#domInfo").textContent = `spread ${(ba - bb)} t · top-5 ${fmtInt(b5)} / ${fmtInt(a5)}`;
}
function sumBook(a, off, k, dir) { let t = 0; for (let i = 0; i < 5; i++) { const kk = k + dir * i; if (kk >= 0 && kk < st.S.W) t += a[off + kk]; } return t; }

let tsKey = -1;
function drawTape() {
  const S = st.S, s = nowS(), j1 = S.first[s + 1];
  if (tsKey === j1 + st.sid) return; tsKey = j1 + st.sid;
  const rows = Math.max(5, Math.floor($("#ts").clientHeight / 17)), out = [], tk = st.cfg.tick;
  for (let j = j1 - 1; j >= Math.max(0, j1 - rows); j--) {
    const big = S.tsz[j] >= S.bigTrade, sd = S.tsd[j] > 0 ? "b" : S.tsd[j] < 0 ? "s" : "";
    out.push(`<div class="${sd}${big ? " big" : ""}"><span>${clock(S.tms[j] / 1000, true)}</span><span>${(S.ttk[j] * tk).toFixed(2)}</span><span>${S.tsz[j]}</span></div>`);
  }
  $("#ts").innerHTML = out.join("");
  let v = 0, d = 0; for (let k = Math.max(0, s - 59); k <= s; k++) { v += S.vol[k]; d += S.buy[k] - S.sell[k]; }
  $("#tsInfo").textContent = `60 s: ${fmtInt(v)} lots, Δ ${d > 0 ? "+" : ""}${fmtInt(d)}`;
}

// ------------------------------------------------------------------ interaction
let drag = null;
cv.addEventListener("pointerdown", (e) => {
  const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, g = geo();
  if (e.shiftKey && x < g.pw) { const s = Math.floor(g.s(x)); setNow(s, false); toast(`Replay from ${clock(s)} — press Space to play`); return; }
  const hit = x < g.pw ? hitTrade(x, y, g) : null;
  if (hit) { selectTrade(hit, false); return; }
  cv.setPointerCapture(e.pointerId);
  drag = { x, y, view: { ...st.view }, ylo: st.y.lo, yhi: st.y.hi, axis: x >= g.pw };
  if (!drag.axis) st.follow = false, ($("#follow").checked = false);
});
cv.addEventListener("pointermove", (e) => {
  const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, g = geo();
  st.hover = { x, y };
  if (drag) {
    if (drag.axis) {
      const f = Math.exp((y - drag.y) / 150), c = (drag.ylo + drag.yhi) / 2, hlf = ((drag.yhi - drag.ylo) / 2) * f;
      st.y = { auto: false, lo: c - hlf, hi: c + hlf };
    } else {
      const ds = ((x - drag.x) / g.pw) * (drag.view.b - drag.view.a);
      st.view = { a: drag.view.a - ds, b: drag.view.b - ds };
      if (!st.y.auto) { const dp = ((y - drag.y) / g.mainH) * (drag.yhi - drag.ylo); st.y.lo = drag.ylo + dp; st.y.hi = drag.yhi + dp; }
    }
  }
  markDirty();
});
cv.addEventListener("pointerup", () => (drag = null));
cv.addEventListener("pointerleave", () => { st.hover = null; markDirty(); });
cv.addEventListener("wheel", (e) => {
  e.preventDefault();
  const r = cv.getBoundingClientRect(), x = e.clientX - r.left, g = geo(), f = Math.exp(e.deltaY * 0.0015);
  if (x >= g.pw) { const c = (st.y.lo + st.y.hi) / 2, h = ((st.y.hi - st.y.lo) / 2) * f; st.y = { auto: false, lo: c - h, hi: c + h }; }
  else {
    const s = g.s(x), span = Math.min(st.S.n * 1.5, Math.max(20, (st.view.b - st.view.a) * f));
    const k = (s - st.view.a) / (st.view.b - st.view.a);
    st.view = { a: s - k * span, b: s - k * span + span };
  }
  markDirty();
}, { passive: false });
cv.addEventListener("dblclick", () => { fitAll(); if (nowS() < st.S.n - 1) { st.view = { a: st.now - 1800, b: st.now + 300 }; } markDirty(); });

function hitTrade(x, y, g) {
  if (!st.bt) return null;
  const tk = st.cfg.tick, Y = (p) => ((st.y.hi - p) / (st.y.hi - st.y.lo)) * g.mainH;
  for (const t of st.bt.trades) if (t.session === st.sid && t.e_s <= nowS()) { if (Math.abs(g.x(t.e_s + 0.5) - x) < 8 && Math.abs(Y(t.entry / tk) - y) < 14) return t; }
  return null;
}

window.addEventListener("keydown", (e) => {
  if (!st.S || e.target.matches("input, select, textarea")) return;
  const k = e.key;
  if (k === " ") { e.preventDefault(); togglePlay(); }
  else if (k === "ArrowRight") { e.preventDefault(); setNow(nowS() + (e.shiftKey ? st.tf : 1)); }
  else if (k === "ArrowLeft") { e.preventDefault(); setNow(nowS() - (e.shiftKey ? st.tf : 1)); }
  else if (k === "Home") startReplay();
  else if (k === "End") endReplay();
  else if (k === "+" || k === "=") speedStep(1);
  else if (k === "-") speedStep(-1);
  else if (k === "f" || k === "F") { st.follow = !st.follow; $("#follow").checked = st.follow; if (st.follow) followView(); markDirty(); }
  else if (k === "h" || k === "H") { $("#optHeat").click(); }
  else if (k === "b" || k === "B") simOrder(1);
  else if (k === "s" || k === "S") simOrder(-1);
  else if (k === "x" || k === "X") simFlat();
});

function togglePlay() {
  if (!st.playing && nowS() >= st.S.n - 1) startReplay();
  st.playing = !st.playing;
  $("#bPlay").textContent = st.playing ? "❚❚" : "▶";
  if (st.playing) { st.follow = true; $("#follow").checked = true; }
}
function startReplay() { setNow(0, false); const w = Math.max(600, st.tf * 90); st.view = { a: 1 - 0.8 * w, b: 1 + 0.2 * w }; st.y.auto = true; st.follow = true; $("#follow").checked = true; markDirty(); }
function endReplay() { st.playing = false; $("#bPlay").textContent = "▶"; setNow(st.S.n - 1, false); fitAll(); markDirty(); }
function speedStep(d) { const o = $("#speed"), i = Math.max(0, Math.min(o.options.length - 1, o.selectedIndex + d)); o.selectedIndex = i; st.speed = +o.value; toast(`speed ${o.value}×`); }

$("#bPlay").onclick = togglePlay;
$("#bStart").onclick = startReplay;
$("#bEnd").onclick = endReplay;
$("#bBack").onclick = () => setNow(nowS() - st.tf);
$("#bFwd").onclick = () => setNow(nowS() + st.tf);
$("#speed").onchange = (e) => (st.speed = +e.target.value);
$("#simBuy").onclick = () => simOrder(1);
$("#simSell").onclick = () => simOrder(-1);
$("#simFlat").onclick = () => simFlat();
$("#slider").oninput = (e) => setNow(+e.target.value, true);
$("#follow").onchange = (e) => { st.follow = e.target.checked; if (st.follow) followView(); markDirty(); };
$$("#tf button").forEach((b) => (b.onclick = () => { $$("#tf button").forEach((x) => x.classList.toggle("on", x === b)); st.tf = +b.dataset.tf; markDirty(); }));
for (const [id, key] of [["#optHeat", "heat"], ["#optVwap", "vwap"], ["#optBub", "bub"], ["#optTrades", "trades"]]) $(id).onchange = (e) => { st.show[key] = e.target.checked; markDirty(); };
$("#theme").onclick = () => { const d = isDark(); document.documentElement.dataset.theme = d ? "light" : "dark"; try { localStorage.setItem("ofbt-theme", d ? "light" : "dark"); } catch (_) {} markDirty(); tsKey = -1; };
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", markDirty);

let lastT = performance.now();
function loop(t) {
  const dt = Math.min(0.25, (t - lastT) / 1000); lastT = t;
  if (st.playing && st.S) {
    const before = nowS();
    st.now = Math.min(st.S.n - 1, st.now + dt * st.speed);
    if (nowS() !== before) setNow(st.now);
    if (st.now >= st.S.n - 1) { st.playing = false; $("#bPlay").textContent = "▶"; }
  }
  if (st.dirty && st.S) { st.dirty = false; draw(); }
  requestAnimationFrame(loop);
}

// ------------------------------------------------------------------ sim trading (same fill model as ofbt/backtest.py)
function simOrder(side) {
  const S = st.S, s = nowS();
  if (s >= S.n - 2) { toast("Start a replay first (Home, then Space): sim orders fill in the next second."); return; }
  if (st.sim.open) { toast("One position at a time: flatten first."); return; }
  const stop = +$("#simStop").value, tgt = +$("#simTgt").value, tk = st.cfg.tick, e = s + 1;
  const entry = side === 1 ? S.ba[e] : S.bb[e];
  st.sim.open = { key: "sim" + Date.now(), session: st.sid, side, sig_s: s, e_s: e, entry: entry * tk, entryT: entry,
    stopT: entry - side * Math.round(stop / tk), tgtT: entry + side * Math.round(tgt / tk), stop_pts: stop, checked: e };
  st.sim.open.stop = st.sim.open.stopT * tk; st.sim.open.target = st.sim.open.tgtT * tk;
  toast(`${side === 1 ? "Buy" : "Sell"} fills at ${clock(e)} ${(entry * tk).toFixed(2)}`);
  simUpdate(); markDirty();
}
function simUpdate() {
  const o = st.sim.open, S = st.S; if (!o || !S || o.session !== st.sid) return renderSimPnl();
  const c = st.cfg, ns = nowS();
  for (let j = o.checked + 1; j <= ns; j++) {
    const hs = o.side === 1 ? S.lob[j] <= o.stopT : S.hia[j] >= o.stopT;
    const ht = o.side === 1 ? S.hib[j] >= o.tgtT + c.target_through_ticks : S.loa[j] <= o.tgtT - c.target_through_ticks;
    if (hs) return simClose(j, o.stopT - o.side * c.stop_slip_ticks, "stop");
    if (ht) return simClose(j, o.tgtT, "target");
    o.checked = j;
  }
  renderSimPnl();
}
function simFlat() {
  const o = st.sim.open; if (!o) return;
  const s = Math.max(o.e_s, nowS());
  simClose(s, o.side === 1 ? st.S.bb[s] : st.S.ba[s], "flatten");
}
function simClose(j, xpT, how) {
  const o = st.sim.open, tk = st.cfg.tick;
  const pts = (xpT - o.entryT) * o.side * tk - st.cfg.cost_pts;
  st.sim.closed.push({ ...o, x_s: j, exit: xpT * tk, how, pts: +pts.toFixed(2), R: +(pts / o.stop_pts).toFixed(3), usd: pts * st.cfg.point_usd });
  st.sim.open = null;
  toast(`Sim ${how}: ${pts >= 0 ? "+" : ""}${pts.toFixed(2)} pts (${fmtUsd(pts * st.cfg.point_usd)})`);
  renderSim(); renderSimPnl(); markDirty();
}
function renderSimPnl() {
  const tot = st.sim.closed.reduce((a, t) => a + t.usd, 0), o = st.sim.open;
  let txt = st.sim.closed.length ? `closed ${fmtUsd(tot)}` : "";
  if (o && o.session === st.sid && nowS() >= o.e_s) {
    const S = st.S, s = nowS(), mark = o.side === 1 ? S.bb[s] : S.ba[s], u = ((mark - o.entryT) * o.side * st.cfg.tick - st.cfg.cost_pts) * st.cfg.point_usd;
    txt = `${o.side === 1 ? "LONG" : "SHORT"} @ ${o.entry.toFixed(2)} open ${fmtUsd(u)}` + (txt ? " · " + txt : "");
  } else if (o) txt = "order pending" + (txt ? " · " + txt : "");
  $("#simPnl").innerHTML = txt;
}
function renderSim() {
  const T = st.sim.closed;
  if (!T.length) return;
  const tot = T.reduce((a, t) => a + t.usd, 0), w = T.filter((t) => t.pts > 0).length;
  $("#p-sim").innerHTML = `<div class="stats">${statBox("Sim trades", T.length)}${statBox("Won", ((100 * w) / T.length).toFixed(0) + "%")}${statBox("Total", fmtUsd(tot), tot)}${statBox("Avg R", sgn(T.reduce((a, t) => a + t.R, 0) / T.length), T.reduce((a, t) => a + t.R, 0))}</div>` +
    tradeTable(T, false);
  bindTradeRows($("#p-sim"), T);
}

// ------------------------------------------------------------------ strategy panel
function status(h) { $("#status").innerHTML = h; }
function toast(t) { const el = $("#toast"); el.textContent = t; el.style.display = "block"; clearTimeout(toast.h); toast.h = setTimeout(() => (el.style.display = "none"), 2600); }
function tab(name) { $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name)); $$(".pane").forEach((p) => p.classList.toggle("on", p.id === "p-" + name)); if (name === "results") setTimeout(drawEquity, 0); }
window.addEventListener("resize", () => setTimeout(drawEquity, 0));
$$(".tabs button").forEach((b) => (b.onclick = () => tab(b.dataset.tab)));

function curStrat() { return st.strategies.find((s) => s.name === $("#strat").value); }
function renderParams(values) {
  const s = curStrat();
  $("#stratDesc").textContent = s.description;
  $("#params").innerHTML = s.params.map((p) => `<label title="${esc(p.help || "")}">${esc(p.label)}<input type="number" data-p="${p.name}" value="${values && values[p.name] != null ? values[p.name] : p.default}" min="${p.min}" max="${p.max}" step="${p.step}"></label>`).join("");
  const opts = s.params.map((p) => `<option value="${p.name}">${esc(p.label)}</option>`).join("");
  $("#swX").innerHTML = opts; $("#swY").innerHTML = `<option value="">(none)</option>` + opts;
  const stopI = s.params.findIndex((p) => p.name === "stop_pts"), tgtI = s.params.findIndex((p) => p.name === "target_pts");
  if (stopI >= 0) $("#swX").value = "stop_pts";
  if (tgtI >= 0) $("#swY").value = "target_pts";
  suggestValues();
}
function suggestValues() {
  const s = curStrat(), pick = (name) => s.params.find((p) => p.name === name);
  for (const [sel, inp] of [["#swX", "#swXv"], ["#swY", "#swYv"]]) {
    const p = pick($(sel).value); if (!p) { $(inp).value = ""; continue; }
    const d = p.default, vals = [d * 0.5, d * 0.75, d, d * 1.5, d * 2].map((v) => (p.int ? Math.round(v) : Math.round(v / p.step) * p.step)).filter((v) => v >= p.min && v <= p.max);
    $(inp).value = [...new Set(vals.map((v) => +v.toFixed(4)))].join(", ");
  }
}
$("#swX").onchange = suggestValues; $("#swY").onchange = suggestValues;
function paramValues() { const o = {}; $$("#params input").forEach((i) => (o[i.dataset.p] = +i.value)); return o; }
function selectedSessions() { return $$("#sessList input:checked").map((i) => i.value); }
$("#strat").onchange = () => renderParams();
$("#resetParams").onclick = () => renderParams();
$("#sessAll").onclick = () => $$("#sessList input").forEach((i) => (i.checked = true));
$("#sessNone").onclick = () => $$("#sessList input").forEach((i) => (i.checked = false));
$("#sessCur").onclick = () => $$("#sessList input").forEach((i) => (i.checked = i.value === st.sid));

$("#runBt").onclick = async () => {
  const ids = selectedSessions(); if (!ids.length) return toast("Pick at least one session.");
  const btn = $("#runBt"); btn.disabled = true; $("#btInfo").innerHTML = `<span class="spin"></span> running on ${ids.length} session(s) ...`;
  try {
    const r = await getJSON("/api/backtest", { strategy: $("#strat").value, params: paramValues(), sessions: ids });
    r.trades.forEach((t, k) => (t.key = "bt" + k));
    st.bt = r; st.sel = null;
    $("#btInfo").textContent = `${r.summary.n} trades in ${r.seconds} s`;
    renderResults(); renderTrades(); tab("results"); markDirty();
  } catch (e) { $("#btInfo").textContent = "error: " + e.message; }
  btn.disabled = false;
};

function statBox(label, value, sign) { return `<div class="stat"><b class="${sign != null ? cls(sign) : ""}">${value}</b><span>${label}</span></div>`; }

function verdict(r) {
  const s = r.summary, c = r.controls, ci = r.ci;
  if (!s.n) return [`No trades. Loosen the settings or pick more sessions.`, "bad"];
  const beatsR = c.random.n && s.avg_R > c.random.avg_R, beatsO = c.opposite.n && s.avg_R > c.opposite.avg_R, ciPos = ci && ci.lo > 0;
  if (s.n < 30) return [`Only ${s.n} trades: too few to judge. Average ${sgn(s.avg_R)} R.`, "bad"];
  if (beatsR && beatsO && ciPos) return [`Beats both controls and the 90% interval (${sgn(ci.lo)} to ${sgn(ci.hi)} R) is above zero. Worth testing on more, unseen sessions before believing it.`, "good"];
  const why = [!beatsR && "it does not beat random entries", !beatsO && "it does not beat the opposite side", !ciPos && ci && `the 90% interval for its average (${sgn(ci.lo)} to ${sgn(ci.hi)} R) includes zero`].filter(Boolean);
  return [`No edge shown: ${why.join("; ")}.`, "bad"];
}

function renderResults() {
  const r = st.bt, s = r.summary, c = r.controls;
  if (!s.n) { $("#p-results").innerHTML = `<div class="empty">No trades.</div>`; return; }
  const [vt, vc] = verdict(r);
  const ctl = (name, x) => x.n ? `<tr><td>${name}</td><td>${fmtInt(x.n)}</td><td>${x.win.toFixed(1)}%</td><td class="${cls(x.avg_R)}">${sgn(x.avg_R)}</td><td>${x.pf ?? "–"}</td><td class="${cls(x.expectancy_usd)}">${fmtUsd(x.expectancy_usd)}</td><td class="${cls(x.total_usd)}">${fmtUsd(x.total_usd)}</td></tr>` : `<tr><td>${name}</td><td colspan="6">no trades</td></tr>`;
  $("#p-results").innerHTML = `
    <div class="verdict ${vc}">${esc(vt)}</div>
    <div class="stats">
      ${statBox("Trades", fmtInt(s.n))}${statBox("Won", s.win.toFixed(1) + "%")}${statBox("Average R", sgn(s.avg_R), s.avg_R)}
      ${statBox("Profit factor", s.pf ?? "–")}${statBox("Per trade", fmtUsd(s.expectancy_usd), s.expectancy_usd)}${statBox("Total (1 lot)", fmtUsd(s.total_usd), s.total_usd)}
      ${statBox("Max drawdown", fmtUsd(-s.max_dd_usd))}${statBox("90% interval", r.ci ? `${sgn(r.ci.lo, 2)} / ${sgn(r.ci.hi, 2)}` : "–")}
      ${statBox("Avg hold", Math.round(s.avg_hold_s) + " s")}${statBox("Target / stop / time", `${s.targets} / ${s.stops} / ${s.timeouts}`)}
    </div>
    <div class="grid2">
      <div class="card"><h3>Equity, 1 contract, after costs</h3><canvas class="eq" id="eq"></canvas><p class="small">Sessions in order; vertical lines mark a new session.</p></div>
      <div class="card"><h3>Against the controls</h3>
        <table><thead><tr><th></th><th>trades</th><th>won</th><th>avg R</th><th>PF</th><th>per trade</th><th>total</th></tr></thead>
        <tbody>${ctl("Strategy", s)}${ctl("Opposite side", c.opposite)}${ctl("Random seconds", c.random)}</tbody></table>
        <p class="small">Opposite: the other side at the same seconds. Random: the same brackets at 20 random seconds per trade. Interval: bootstrap of the average R over ${r.ci ? r.ci.by + "s" : "–"}.</p>
        <h3 style="margin-top:12px">By session</h3>
        <table><thead><tr><th>session</th><th>trades</th><th>won</th><th>avg R</th><th>total</th></tr></thead><tbody>
        ${r.per_session.map((p) => `<tr class="click" data-sid="${esc(p.session)}"><td>${esc(p.label)}</td><td>${p.n}</td><td>${p.n ? p.win.toFixed(0) + "%" : "–"}</td><td class="${cls(p.avg_R)}">${p.n ? sgn(p.avg_R) : "–"}</td><td class="${cls(p.total_usd)}">${p.n ? fmtUsd(p.total_usd) : "–"}</td></tr>`).join("")}
        </tbody></table></div>
    </div>`;
  $$("#p-results tr[data-sid]").forEach((tr) => (tr.onclick = () => switchSession(tr.dataset.sid)));
  drawEquity();
}

function drawEquity() {
  const c = $("#eq"); if (!c || !st.bt) return;
  const r = c.getBoundingClientRect(), x = c.getContext("2d"), w = r.width, h = r.height;
  c.width = w * dpr; c.height = h * dpr; x.setTransform(dpr, 0, 0, dpr, 0, 0);
  const E = st.bt.equity, vals = [0, ...E.map((e) => e.usd)], lo = Math.min(...vals), hi = Math.max(...vals), L = 56;
  const X = (i) => L + (i / Math.max(1, vals.length - 1)) * (w - L - 8), Y = (v) => 8 + (1 - (v - lo) / Math.max(1, hi - lo)) * (h - 22);
  x.font = "11px " + cssVar("--mono"); x.fillStyle = cssVar("--axis"); x.textBaseline = "middle";
  x.strokeStyle = cssVar("--grid");
  for (const v of [lo, 0, hi]) { x.beginPath(); x.moveTo(L, Y(v)); x.lineTo(w, Y(v)); x.stroke(); x.fillText(fmtUsd(v), 2, Y(v)); }
  x.strokeStyle = cssVar("--line"); let prev = null;
  E.forEach((e, i) => { if (prev !== null && e.session !== prev) { x.beginPath(); x.moveTo(X(i + 0.5), 8); x.lineTo(X(i + 0.5), h - 14); x.stroke(); } prev = e.session; });
  x.strokeStyle = vals[vals.length - 1] >= 0 ? cssVar("--up") : cssVar("--down"); x.lineWidth = 1.8; x.beginPath();
  vals.forEach((v, i) => (i ? x.lineTo(X(i), Y(v)) : x.moveTo(X(i), Y(v)))); x.stroke();
  x.fillStyle = cssVar("--muted"); x.fillText(`${E.length} trades`, L, h - 6);
}

function tradeTable(T, withReplay = true) {
  const tk = st.cfg.tick;
  return `<table><thead><tr><th>#</th><th>session</th><th>entry time</th><th>side</th><th>entry</th><th>exit</th><th>how</th><th>held</th><th>pts</th><th>R</th><th>$</th><th>note</th>${withReplay ? "<th></th>" : ""}</tr></thead><tbody>` +
    T.map((t, i) => {
      const card = st.sessions.find((c) => c.id === t.session);
      return `<tr class="click${st.sel === t.key ? " sel" : ""}" data-k="${t.key}"><td>${i + 1}</td><td>${esc(card ? card.label.slice(0, 15) : t.session)}</td><td class="mono">${card ? clockFor(card, t.e_s) : t.e_s}</td><td class="${t.side === 1 ? "pos" : "neg"}">${t.side === 1 ? "LONG" : "SHORT"}</td><td class="mono">${t.entry.toFixed(2)}</td><td class="mono">${t.exit.toFixed(2)}</td><td>${t.how}</td><td>${t.x_s - t.e_s}s</td><td class="mono ${cls(t.pts)}">${sgn(t.pts, 2)}</td><td class="mono ${cls(t.R)}">${sgn(t.R, 2)}</td><td class="mono ${cls(t.usd)}">${fmtUsd(t.usd)}</td><td class="small">${esc(t.tag || "")}</td>${withReplay ? `<td><button class="btn" data-replay="${t.key}" title="Replay from 2 minutes before the entry">▶ replay</button></td>` : ""}</tr>`;
    }).join("") + "</tbody></table>";
}
function clockFor(card, s) { const t0 = Date.parse(card.start) / 1000, mt = /([+-])(\d\d):(\d\d)$/.exec(card.start), off = mt ? (mt[1] === "-" ? -1 : 1) * (+mt[2] * 60 + +mt[3]) * 60 : 0; const d = new Date((t0 + s + off) * 1000); return d.toISOString().slice(11, 19); }
function renderTrades() {
  const T = st.bt.trades;
  $("#p-trades").innerHTML = T.length ? `<p class="small">Click a trade to show it on the chart, or replay it second by second.</p>` + tradeTable(T) : `<div class="empty">No trades.</div>`;
  bindTradeRows($("#p-trades"), T);
}
function bindTradeRows(root, T) {
  root.querySelectorAll("tr[data-k]").forEach((tr) => (tr.onclick = (e) => { const t = T.find((x) => x.key === tr.dataset.k); if (e.target.dataset.replay) replayTrade(t); else selectTrade(t, true); }));
}
async function switchSession(id) { if (id !== st.sid) { $("#session").value = id; await loadSession(id); } }
async function selectTrade(t, frameIt) {
  await switchSession(t.session);
  st.sel = t.key;
  if (frameIt) { if (nowS() < t.x_s) setNow(st.S.n - 1, false); const pad = Math.max(300, (t.x_s - t.e_s) * 1.5); frame(t.e_s - pad, t.x_s + pad); }
  $$("tr[data-k]").forEach((r) => r.classList.toggle("sel", r.dataset.k === t.key));
  toast(`${t.side === 1 ? "LONG" : "SHORT"} ${t.entry.toFixed(2)} → ${t.exit.toFixed(2)} (${t.how}) ${sgn(t.pts, 2)} pts`);
  markDirty();
}
async function replayTrade(t) {
  await switchSession(t.session);
  st.sel = t.key;
  st.playing = false;
  setNow(Math.max(0, t.e_s - 120), false);
  const w = Math.max(900, (t.x_s - t.e_s) * 2);
  st.view = { a: st.now - 0.6 * w, b: st.now + 0.4 * w }; st.y.auto = true;
  if (st.tf > 15) { st.tf = 15; $$("#tf button").forEach((x) => x.classList.toggle("on", +x.dataset.tf === 15)); }
  togglePlay(); markDirty();
}

// ------------------------------------------------------------------ sweep
function parseVals(txt) { return txt.split(/[\s,;]+/).filter(Boolean).map(Number).filter((v) => Number.isFinite(v)); }
$("#runSw").onclick = async () => {
  const ids = selectedSessions(), k = Math.max(0, Math.min(ids.length - 1, +$("#swTest").value));
  const train = ids.slice(0, ids.length - k), test = ids.slice(ids.length - k);
  const grid = {}, xs = $("#swX").value, ys = $("#swY").value;
  const xv = parseVals($("#swXv").value); if (!xv.length) return toast("Enter X values.");
  grid[xs] = xv;
  if (ys && ys !== xs) { const yv = parseVals($("#swYv").value); if (yv.length) grid[ys] = yv; }
  const btn = $("#runSw"); btn.disabled = true;
  const combos = Object.values(grid).reduce((a, v) => a * v.length, 1);
  $("#swInfo").innerHTML = `<span class="spin"></span> ${combos} combinations × ${train.length} training sessions ...`;
  try {
    const r = await getJSON("/api/sweep", { strategy: $("#strat").value, params: paramValues(), grid, train, test, min_trades: +$("#swMin").value });
    st.sweep = r; $("#swInfo").textContent = `done in ${r.seconds} s`; renderSweep(); tab("sweep");
  } catch (e) { $("#swInfo").textContent = "error: " + e.message; }
  btn.disabled = false;
};
function renderSweep() {
  const r = st.sweep, s = curStrat(), lab = (n) => (s.params.find((p) => p.name === n) || { label: n }).label;
  const [xn, yn] = r.names, xv = r.grid[xn], yv = yn ? r.grid[yn] : [null];
  const cell = (x, y) => r.cells.find((c) => c.values[0] === x && (yn ? c.values[1] === y : true));
  const mx = Math.max(0.05, ...r.cells.filter((c) => c.n).map((c) => Math.abs(c.avg_R)));
  const color = (v) => { const a = Math.min(1, Math.abs(v) / mx); return v >= 0 ? `color-mix(in srgb, var(--up) ${Math.round(25 + 70 * a)}%, var(--panel))` : `color-mix(in srgb, var(--down) ${Math.round(25 + 70 * a)}%, var(--panel))`; };
  let html = `<div class="grid2"><div class="card"><h3>Average R on the ${r.train.length} training sessions</h3><table class="heat"><thead><tr><th>${yn ? esc(lab(yn)) + " ↓ / " : ""}${esc(lab(xn))} →</th>${xv.map((x) => `<th style="text-align:center">${x}</th>`).join("")}</tr></thead><tbody>`;
  for (const y of yv) {
    html += `<tr><th>${y ?? ""}</th>`;
    for (const x of xv) {
      const c = cell(x, y), best = r.best && c && c.values.join() === r.best.values.join();
      html += c && c.n ? `<td class="${best ? "best" : ""}" style="background:${color(c.avg_R)};color:var(--text)" title="${c.n} trades, won ${c.win}%, ${fmtUsd(c.total_usd)}" data-v="${c.values.join(",")}">${sgn(c.avg_R, 2)}<br><span class="small">${c.n}</span></td>` : `<td class="small">–</td>`;
    }
    html += "</tr>";
  }
  html += `</tbody></table><p class="small">Each cell: average R and the number of trades. Click a cell to load its settings. Outlined: the best cell with at least the minimum number of trades.</p></div><div class="card"><h3>Held-out test</h3>`;
  if (!r.best) html += `<p>No combination reached the minimum number of trades on the training sessions.</p>`;
  else if (!r.test.length) html += `<p>No test sessions (set "Test on the last" above 0).</p>`;
  else {
    const bt = r.best_test, b = bt.summary, d = r.default_test, rn = bt.controls.random;
    const row = (name, x) => x && x.n ? `<tr><td>${name}</td><td>${x.n}</td><td>${x.win.toFixed(0)}%</td><td class="${cls(x.avg_R)}">${sgn(x.avg_R)}</td><td class="${cls(x.total_usd)}">${fmtUsd(x.total_usd)}</td></tr>` : `<tr><td>${name}</td><td colspan="4">no trades</td></tr>`;
    html += `<p>Best on training: <b>${r.names.map((n, i) => `${esc(lab(n))} = ${r.best.values[i]}`).join(", ")}</b> (${sgn(r.best.avg_R)} R on ${r.best.n} trades), chosen from ${r.n_tested} combinations.</p>
      <table><thead><tr><th>on the ${r.test.length} test sessions</th><th>trades</th><th>won</th><th>avg R</th><th>total</th></tr></thead><tbody>${row("Best from training", b)}${row("Default settings", d)}${row("Random seconds", rn)}</tbody></table>
      <p class="small">${b.n && rn.n && b.avg_R > rn.avg_R && bt.ci && bt.ci.lo > 0 ? "The chosen settings held up on unseen sessions." : "The training winner did not clearly hold up on unseen sessions. The best of many tries usually looks better than it is: that is why it is checked once on data it was not chosen on."}${bt.ci ? ` 90% interval on test: ${sgn(bt.ci.lo)} to ${sgn(bt.ci.hi)} R.` : ""}</p>`;
  }
  html += `</div></div>`;
  $("#p-sweep").innerHTML = html;
  $$("#p-sweep td[data-v]").forEach((td) => (td.onclick = () => {
    const v = td.dataset.v.split(",").map(Number); r.names.forEach((n, i) => { const inp = $(`#params input[data-p="${n}"]`); if (inp) inp.value = v[i]; });
    tab("strategy"); toast("Settings loaded into the form. Run backtest to see the trades.");
  }));
}

// ------------------------------------------------------------------ bottom panel resize + start
(function resizer() {
  const el = $("#resizer"); let y0 = 0, h0 = 0;
  el.addEventListener("pointerdown", (e) => { y0 = e.clientY; h0 = $("#bottom").getBoundingClientRect().height; el.setPointerCapture(e.pointerId); el.onpointermove = (ev) => { const h = Math.max(120, Math.min(window.innerHeight - 260, h0 - (ev.clientY - y0))); document.documentElement.style.setProperty("--bottom-h", h + "px"); }; });
  el.addEventListener("pointerup", () => { el.onpointermove = null; drawEquity(); });
})();

async function init() {
  try { const t = localStorage.getItem("ofbt-theme"); if (t) document.documentElement.dataset.theme = t; } catch (_) {}
  [st.cfg, st.sessions, st.strategies] = await Promise.all([getJSON("/api/config"), getJSON("/api/sessions"), getJSON("/api/strategies")]);
  $("#costTxt").textContent = st.cfg.cost_pts;
  $("#session").innerHTML = st.sessions.map((c) => `<option value="${esc(c.id)}">${esc(c.label)}${c.private ? " · private" : ""}</option>`).join("");
  $("#session").onchange = (e) => loadSession(e.target.value);
  $("#sessList").innerHTML = st.sessions.map((c) => `<label><input type="checkbox" value="${esc(c.id)}" ${c.private ? "" : "checked"}> ${esc(c.label)} <span class="small">${c.kind === "synthetic" ? "synthetic" : "Databento, private"} · ${fmtInt(c.trades)} trades</span></label>`).join("");
  $("#strat").innerHTML = st.strategies.map((s) => `<option value="${s.name}">${esc(s.title)}</option>`).join("");
  renderParams();
  if (!st.sessions.length) { status("No sessions. Run: python -m ofbt build"); return; }
  // optional link state: ?session=ID&tf=15&at=2290&strategy=vwap_reversion&run=1&tab=results&theme=dark
  const q = new URLSearchParams(location.search);
  if (q.get("theme")) document.documentElement.dataset.theme = q.get("theme");
  const sid = st.sessions.some((c) => c.id === q.get("session")) ? q.get("session") : st.sessions[0].id;
  $("#session").value = sid;
  await loadSession(sid);
  if (q.get("tf")) { const b = $(`#tf button[data-tf="${q.get("tf")}"]`); if (b) b.click(); }
  if (q.get("at")) { startReplay(); setNow(+q.get("at"), false); followView(); }
  if (q.get("strategy") && st.strategies.some((s) => s.name === q.get("strategy"))) { $("#strat").value = q.get("strategy"); renderParams(); }
  requestAnimationFrame(loop);
  if (q.get("run") === "1") await $("#runBt").onclick();
  if (q.get("run") === "sweep") await $("#runSw").onclick();
  if (q.get("tab")) tab(q.get("tab"));
}
window.ofbt = st;   // handy in the browser console
init().catch((e) => status("error: " + esc(e.message)));
