"""Write one self-contained HTML report (no server, no internet): run card, results with controls, a price / order-flow
chart with every trade, and a replay that shows the rebuilt order book (ladder) and the tape second by second."""
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np

from . import config as C

NY = ZoneInfo("America/New_York")


def _ny(t):
    return datetime.fromtimestamp(int(t), timezone.utc).astimezone(NY).strftime("%Y-%m-%d %H:%M:%S")


def write(path, B, F, sigs, res, run_info):
    t = B["t"]
    buy = np.zeros(len(t)); sell = np.zeros(len(t))
    sec = B["tr_ns"] // 1_000_000_000 - int(t[0]); ok = (sec >= 0) & (sec < len(t))
    np.add.at(buy, sec[ok & (B["tr_sd"] == 1)], B["tr_sz"][ok & (B["tr_sd"] == 1)])
    np.add.at(sell, sec[ok & (B["tr_sd"] == -1)], B["tr_sz"][ok & (B["tr_sd"] == -1)])
    data = dict(
        t0=int(t[0]), tick=C.TICK,
        bb=B["bb"].tolist(), ba=B["ba"].tolist(),
        bl=B["bid_lad"].tolist(), al=B["ask_lad"].tolist(),
        buy=buy.astype(int).tolist(), sell=sell.astype(int).tolist(),
        delta=np.round(F["delta"], 1).tolist(), thr=[None if not np.isfinite(x) else round(float(x), 1) for x in F["thr"]],
        imb=np.round(F["imb"], 3).tolist(),
        trades=[dict(x, t_entry_ny=_ny(x["t_entry"]), t_exit_ny=_ny(x["t_exit"])) for x in res["rule"]],
        stats=res["stats"], run=run_info,
        cfg=dict(FLOW_WINDOW_S=C.FLOW_WINDOW_S, FLOW_LOOKBACK_S=C.FLOW_LOOKBACK_S, FLOW_PCTL=C.FLOW_PCTL, BOOK_LEVELS=C.BOOK_LEVELS,
                 IMB_MIN=C.IMB_MIN, WARMUP_S=C.WARMUP_S, COOLDOWN_S=C.COOLDOWN_S, STOP_PTS=C.STOP_PTS, TARGET_PTS=C.TARGET_PTS,
                 MAX_HOLD_S=C.MAX_HOLD_S, COST_PTS=C.COST_PTS, POINT_USD=C.POINT_USD, RANDOM_CONTROL_N=C.RANDOM_CONTROL_N),
    )
    html = TEMPLATE.replace("/*DATA*/null", json.dumps(data, separators=(",", ":")))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    return path


TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Order Flow Backtest</title>
<style>
:root{--bg:#f6f7f9;--panel:#fff;--ink:#1b1f24;--muted:#5d6670;--line:#dfe3e8;--bid:#1f7a4d;--ask:#b4392f;--accent:#2f5fb3;--warn:#8a5a00;--warnbg:#fff4d6;--band:rgba(47,95,179,.10)}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#111418;--panel:#191d22;--ink:#e6e9ed;--muted:#9aa4ae;--line:#2b3138;--bid:#46c38a;--ask:#f0776b;--accent:#7aa5f0;--warn:#f2c46b;--warnbg:#2c2410;--band:rgba(122,165,240,.13)}}
:root[data-theme=dark]{--bg:#111418;--panel:#191d22;--ink:#e6e9ed;--muted:#9aa4ae;--line:#2b3138;--bid:#46c38a;--ask:#f0776b;--accent:#7aa5f0;--warn:#f2c46b;--warnbg:#2c2410;--band:rgba(122,165,240,.13)}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:20px 16px 48px}h1{font-size:22px;margin:0 0 4px}h2{font-size:15px;margin:0 0 10px}
.sub{color:var(--muted);margin:0 0 14px}.banner{background:var(--warnbg);color:var(--warn);border:1px solid var(--warn);border-radius:8px;padding:8px 12px;margin:0 0 16px;font-weight:600}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:14px;margin-bottom:14px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px;min-width:0}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 12px;margin:0}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}
ol{margin:0;padding-left:20px}li{margin:2px 0}code{font-size:12.5px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}th,td{padding:5px 8px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}th:first-child,td:first-child{text-align:left}
th{color:var(--muted);font-weight:600}.scroll{overflow-x:auto}tbody tr.pick{cursor:pointer}tbody tr.pick:hover{background:var(--band)}tbody tr.sel{background:var(--band)}
.pos{color:var(--bid)}.neg{color:var(--ask)}
.chartwrap{position:relative}canvas{display:block;width:100%;background:var(--panel)}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}button{font:inherit;padding:5px 12px;border:1px solid var(--line);border-radius:6px;background:var(--panel);color:var(--ink);cursor:pointer}button:hover{border-color:var(--accent)}
input[type=range]{flex:1;min-width:160px}.mono{font-family:ui-monospace,Consolas,monospace}
.replay{display:grid;grid-template-columns:minmax(220px,300px) 1fr;gap:14px}@media (max-width:720px){.replay{grid-template-columns:1fr}}
.lad td{padding:2px 6px}.lad .px{text-align:center;font-family:ui-monospace,Consolas,monospace}.lad .b{background:linear-gradient(to left,var(--bid) var(--w),transparent var(--w));background-size:100% 70%;background-repeat:no-repeat;background-position:center}
.lad .a{background:linear-gradient(to right,var(--ask) var(--w),transparent var(--w));background-size:100% 70%;background-repeat:no-repeat;background-position:center}
.lad td.b,.lad td.a{color:var(--ink);text-align:center;min-width:70px}.legend{color:var(--muted);font-size:12.5px}
</style></head><body><main>
<h1>Order-flow backtest on Databento MBO</h1>
<p class="sub">Full order book rebuilt from market-by-order messages → per-second book and tape features → a rule → realistic fills → results with controls.</p>
<div class="banner" id="banner"></div>
<div class="grid">
 <section class="card"><h2>Run card</h2><dl id="runcard"></dl></section>
 <section class="card"><h2>Demo rule (generic, public)</h2><ol id="rules"></ol></section>
 <section class="card"><h2>Fill model</h2><ol id="fills"></ol></section>
</div>
<section class="card" style="margin-bottom:14px"><h2>Results (after costs)</h2><div class="scroll"><table id="stats"></table></div>
<p class="legend" id="statnote"></p></section>
<section class="card" style="margin-bottom:14px"><h2>Price, aggressor flow and every trade</h2>
<div class="legend">Top: best bid / best ask (band), entries ▲▼, exits ●, stop and target lines. Middle: 30-second aggressor delta with its threshold (shaded). Bottom: book imbalance on the best 5 levels. Drag on the chart to zoom, double-click to reset, click a trade below to jump to it.</div>
<div class="chartwrap"><canvas id="cv" height="460"></canvas></div></section>
<section class="card" style="margin-bottom:14px"><h2>Replay: the rebuilt book, second by second</h2>
<div class="bar"><button id="play">▶ Play</button><button id="slow" title="replay speed">10×</button><input type="range" id="slider" min="0" value="0"><span class="mono" id="clock"></span></div>
<div class="replay"><div class="scroll"><table class="lad" id="ladder"></table></div><div id="state"></div></div></section>
<section class="card"><h2>Trades</h2><div class="scroll"><table id="trades"></table></div></section>
</main>
<script>
const D=/*DATA*/null;
const $=id=>document.getElementById(id), N=D.bb.length, TK=D.tick;
const css=v=>getComputedStyle(document.documentElement).getPropertyValue(v).trim();
const fmtT=s=>new Date((D.t0+s)*1000).toLocaleTimeString('en-US',{timeZone:'America/New_York',hour12:false});
const R=D.run, S=D.stats, c=D.cfg;
$('banner').textContent=R.synthetic?'SYNTHETIC DATA. This public demo runs on generated order-book messages shaped like Databento GLBX.MDP3 MBO. The numbers show how the app works and say nothing about real markets.':'Real Databento data: local run, do not share.';
const rc=[['Contract',R.contract],['Dataset / schema',R.dataset+' / '+R.schema],['Symbol (stype_in)',R.symbols+' ('+R.stype_in+')'],['Time range',R.range],['MBO messages read',R.records.toLocaleString()],['Seconds sampled',N.toLocaleString()],['Trades on the tape',R.prints.toLocaleString()],['Signals',D.trades.length],['Source file',R.source],['Built',R.built]];
$('runcard').innerHTML=rc.map(([k,v])=>`<dt>${k}</dt><dd>${v}</dd>`).join('');
$('rules').innerHTML=[`delta30 = aggressive buy − sell volume over the last ${c.FLOW_WINDOW_S} s (tape aggressor side)`,`threshold = ${c.FLOW_PCTL}th percentile of |delta30| over the previous ${c.FLOW_LOOKBACK_S/60} min (past only)`,`imbalance = (bid − ask size) / total on the best ${c.BOOK_LEVELS} levels`,`LONG when delta30 ≥ threshold and imbalance ≥ +${c.IMB_MIN}; SHORT mirrored`,`no signal in the first ${c.WARMUP_S/60} min; ${c.COOLDOWN_S/60} min between signals`].map(x=>`<li>${x}</li>`).join('');
$('fills').innerHTML=[`act in the second after the signal: buy at the ask, sell at the bid`,`stop ${c.STOP_PTS} pts, filled 1 tick worse; stop first if both hit in one second`,`target ${c.TARGET_PTS} pts, needs a trade 1 tick through`,`time exit after ${c.MAX_HOLD_S/60} min`,`cost ${c.COST_PTS} pts a round turn; 1 R = ${c.STOP_PTS} pts = $${c.STOP_PTS*c.POINT_USD} on one NQ`].map(x=>`<li>${x}</li>`).join('');
const cls=v=>v>0?'pos':(v<0?'neg':'');
const rows=[['The rule',S.rule],['Opposite side, same seconds',S.opposite],[`Random seconds (${c.RANDOM_CONTROL_N} per signal)`,S.random]];
$('stats').innerHTML='<thead><tr><th>Row</th><th>Trades</th><th>Won</th><th>Avg pts</th><th>Avg R</th><th>Total pts</th><th>Total $ (1 NQ)</th><th>Max drawdown pts</th><th>Stop / target / time</th></tr></thead><tbody>'+rows.map(([k,s])=>s.n?`<tr><td>${k}</td><td>${s.n}</td><td>${s.win}%</td><td class="${cls(s.avg_pts)}">${s.avg_pts}</td><td class="${cls(s.avg_R)}">${s.avg_R}</td><td class="${cls(s.total_pts)}">${s.total_pts}</td><td class="${cls(s.total_usd)}">${s.total_usd.toLocaleString()}</td><td>${s.max_dd_pts}</td><td>${s.stops} / ${s.targets} / ${s.timeouts}</td></tr>`:`<tr><td>${k}</td><td colspan="8">no trades</td></tr>`).join('')+'</tbody>';
$('statnote').textContent='A rule is only interesting if it beats both controls by a clear margin over many trades and many days. One synthetic session proves nothing either way; the controls are here to show how a result should be judged.';
// trades table
$('trades').innerHTML='<thead><tr><th>#</th><th>Side</th><th>Entry (NY)</th><th>Entry</th><th>Stop</th><th>Target</th><th>Exit (NY)</th><th>Exit</th><th>How</th><th>Pts</th><th>R</th></tr></thead><tbody>'+D.trades.map((x,i)=>`<tr class="pick" data-i="${i}"><td>${i+1}</td><td class="${x.side>0?'pos':'neg'}">${x.side>0?'LONG':'SHORT'}</td><td>${x.t_entry_ny.slice(11)}</td><td>${x.entry.toFixed(2)}</td><td>${x.stop.toFixed(2)}</td><td>${x.target.toFixed(2)}</td><td>${x.t_exit_ny.slice(11)}</td><td>${x.exit.toFixed(2)}</td><td>${x.how}</td><td class="${cls(x.pts)}">${x.pts.toFixed(2)}</td><td class="${cls(x.R)}">${x.R.toFixed(2)}</td></tr>`).join('')+'</tbody>';
// chart
const cv=$('cv'), g=cv.getContext('2d'); let v0=0,v1=N-1,cursor=0,drag=null,selT=-1;
function size(){const r=cv.getBoundingClientRect(),dp=devicePixelRatio||1;cv.width=r.width*dp;cv.height=460*dp;g.setTransform(dp,0,0,dp,0,0);draw();}
function draw(){const W=cv.width/(devicePixelRatio||1),H=460,L=58,Rm=10,P1=[8,270],P2=[290,370],P3=[390,450];
 g.clearRect(0,0,W,H);g.font='11px system-ui';const ink=css('--ink'),mu=css('--muted'),ln=css('--line'),bid=css('--bid'),ask=css('--ask'),ac=css('--accent'),band=css('--band');
 const X=s=>L+(s-v0)/(v1-v0)*(W-L-Rm);let lo=1e18,hi=-1e18;const step=Math.max(1,Math.floor((v1-v0)/1500));
 for(let s=v0;s<=v1;s++){if(D.bb[s]>0){lo=Math.min(lo,D.bb[s]);hi=Math.max(hi,D.ba[s]);}}
 D.trades.forEach(t=>{if(t.e_s<=v1&&t.x_s>=v0){lo=Math.min(lo,Math.min(t.stop,t.target)/TK);hi=Math.max(hi,Math.max(t.stop,t.target)/TK);}});
 const pad=(hi-lo)*.06+2;lo-=pad;hi+=pad;const Y=p=>P1[0]+(hi-p)/(hi-lo)*(P1[1]-P1[0]);
 g.strokeStyle=ln;g.fillStyle=mu;g.lineWidth=1;
 for(let k=0;k<=5;k++){const p=lo+(hi-lo)*k/5,y=Y(p);g.beginPath();g.moveTo(L,y);g.lineTo(W-Rm,y);g.stroke();g.fillText((p*TK).toFixed(2),4,y+4);}
 const nt=Math.max(2,Math.floor((W-L)/110));for(let k=0;k<=nt;k++){const s=Math.round(v0+(v1-v0)*k/nt);g.fillText(fmtT(s),X(s)-22,H-2);}
 g.fillStyle=band;g.beginPath();let f=true;for(let s=v0;s<=v1;s+=step){if(D.ba[s]<=0)continue;const x=X(s),y=Y(D.ba[s]);f?g.moveTo(x,y):g.lineTo(x,y);f=false;}
 for(let s=v1;s>=v0;s-=step){if(D.bb[s]<=0)continue;g.lineTo(X(s),Y(D.bb[s]));}g.closePath();g.fill();
 [[D.ba,ask],[D.bb,bid]].forEach(([a,col])=>{g.strokeStyle=col;g.beginPath();let f=true;for(let s=v0;s<=v1;s+=step){if(a[s]<=0)continue;const x=X(s),y=Y(a[s]);f?g.moveTo(x,y):g.lineTo(x,y);f=false;}g.stroke();});
 D.trades.forEach((t,i)=>{if(t.x_s<v0||t.e_s>v1)return;const x0=X(t.e_s),x1=X(t.x_s),col=t.side>0?bid:ask;g.setLineDash([4,3]);g.lineWidth=i===selT?2:1;
  g.strokeStyle=ask;g.beginPath();g.moveTo(x0,Y(t.stop/TK));g.lineTo(x1,Y(t.stop/TK));g.stroke();g.strokeStyle=bid;g.beginPath();g.moveTo(x0,Y(t.target/TK));g.lineTo(x1,Y(t.target/TK));g.stroke();g.setLineDash([]);
  g.strokeStyle=ink;g.beginPath();g.moveTo(x0,Y(t.entry/TK));g.lineTo(x1,Y(t.exit/TK));g.stroke();
  g.fillStyle=col;g.beginPath();const y=Y(t.entry/TK);if(t.side>0){g.moveTo(x0,y-2);g.lineTo(x0-6,y+9);g.lineTo(x0+6,y+9);}else{g.moveTo(x0,y+2);g.lineTo(x0-6,y-9);g.lineTo(x0+6,y-9);}g.fill();
  g.fillStyle=t.pts>0?bid:ask;g.beginPath();g.arc(x1,Y(t.exit/TK),4,0,7);g.fill();g.lineWidth=1;});
 // delta panel
 let dm=1;for(let s=v0;s<=v1;s++){dm=Math.max(dm,Math.abs(D.delta[s]),D.thr[s]||0);}const Yd=v=>(P2[0]+P2[1])/2-v/dm*(P2[1]-P2[0])/2;
 g.fillStyle=band;g.beginPath();f=true;for(let s=v0;s<=v1;s+=step){if(D.thr[s]==null)continue;const x=X(s);f?g.moveTo(x,Yd(D.thr[s])):g.lineTo(x,Yd(D.thr[s]));f=false;}
 for(let s=v1;s>=v0;s-=step){if(D.thr[s]==null)continue;g.lineTo(X(s),Yd(-D.thr[s]));}g.closePath();g.fill();
 g.strokeStyle=ac;g.beginPath();f=true;for(let s=v0;s<=v1;s+=step){const x=X(s),y=Yd(D.delta[s]);f?g.moveTo(x,y):g.lineTo(x,y);f=false;}g.stroke();
 g.strokeStyle=ln;g.beginPath();g.moveTo(L,Yd(0));g.lineTo(W-Rm,Yd(0));g.stroke();g.fillStyle=mu;g.fillText('delta30',4,P2[0]+10);g.fillText('±'+Math.round(dm),4,P2[0]+24);
 // imbalance panel
 const Yi=v=>(P3[0]+P3[1])/2-v*(P3[1]-P3[0])/2;g.strokeStyle=ln;[c.IMB_MIN,-c.IMB_MIN,0].forEach(v=>{g.beginPath();g.moveTo(L,Yi(v));g.lineTo(W-Rm,Yi(v));g.stroke();});
 g.strokeStyle=mu;g.beginPath();f=true;for(let s=v0;s<=v1;s+=step){const x=X(s),y=Yi(D.imb[s]);f?g.moveTo(x,y):g.lineTo(x,y);f=false;}g.stroke();g.fillStyle=mu;g.fillText('imbalance',4,P3[0]+10);
 if(cursor>=v0&&cursor<=v1){g.strokeStyle=ink;g.setLineDash([2,3]);g.beginPath();g.moveTo(X(cursor),P1[0]);g.lineTo(X(cursor),P3[1]);g.stroke();g.setLineDash([]);}
 if(drag&&drag.x1!=null){g.fillStyle=band;g.fillRect(Math.min(drag.x0,drag.x1),0,Math.abs(drag.x1-drag.x0),P3[1]);}
 cv._X=X;cv._L=L;cv._W=W;}
const sOf=x=>{const W=cv._W,L=cv._L;return Math.round(v0+(x-L)/(W-L-10)*(v1-v0));};
cv.addEventListener('mousedown',e=>{const r=cv.getBoundingClientRect();drag={x0:e.clientX-r.left,x1:null};});
cv.addEventListener('mousemove',e=>{if(!drag)return;const r=cv.getBoundingClientRect();drag.x1=e.clientX-r.left;draw();});
window.addEventListener('mouseup',e=>{if(!drag)return;const d=drag;drag=null;if(d.x1==null||Math.abs(d.x1-d.x0)<6){if(d.x1==null){setCursor(Math.max(0,Math.min(N-1,sOf(d.x0))));}draw();return;}
 let a=sOf(Math.min(d.x0,d.x1)),b=sOf(Math.max(d.x0,d.x1));a=Math.max(0,a);b=Math.min(N-1,b);if(b-a>20){v0=a;v1=b;}draw();});
cv.addEventListener('dblclick',()=>{v0=0;v1=N-1;selT=-1;draw();});
document.querySelectorAll('#trades tr.pick').forEach(tr=>tr.addEventListener('click',()=>{const i=+tr.dataset.i,t=D.trades[i];selT=i;
 document.querySelectorAll('#trades tr').forEach(r=>r.classList.remove('sel'));tr.classList.add('sel');v0=Math.max(0,t.e_s-600);v1=Math.min(N-1,t.x_s+300);setCursor(t.sig_s);$('cv').scrollIntoView({behavior:'smooth',block:'center'});}));
// replay
const sl=$('slider');sl.max=N-1;let timer=null,speed=1;
function setCursor(s){cursor=s;sl.value=s;$('clock').textContent=fmtT(s)+' New York  ·  second '+s+' of '+(N-1);ladder(s);draw();}
function ladder(s){const bb=D.bb[s],ba=D.ba[s],bl=D.bl[s],al=D.al[s];const mx=Math.max(1,...bl,...al);let h='<thead><tr><th style="text-align:center">Bid size</th><th style="text-align:center">Price</th><th style="text-align:center">Ask size</th></tr></thead><tbody>';
 for(let k=al.length-1;k>=0;k--)h+=`<tr><td></td><td class="px">${((ba+k)*TK).toFixed(2)}</td><td class="a" style="--w:${al[k]/mx*100}%">${al[k]||''}</td></tr>`;
 for(let k=ba-bb-1;k>=1&&k<6;k--)h+=`<tr><td></td><td class="px" style="color:var(--muted)">${((bb+k)*TK).toFixed(2)}</td><td></td></tr>`;
 for(let k=0;k<bl.length;k++)h+=`<tr><td class="b" style="--w:${bl[k]/mx*100}%">${bl[k]||''}</td><td class="px">${((bb-k)*TK).toFixed(2)}</td><td></td></tr>`;
 $('ladder').innerHTML=h+'</tbody>';
 let b30=0,s30=0;for(let q=Math.max(0,s-29);q<=s;q++){b30+=D.buy[q];s30+=D.sell[q];}
 const thr=D.thr[s],live=thr!=null&&((D.delta[s]>=thr&&D.imb[s]>=c.IMB_MIN)||(D.delta[s]<=-thr&&D.imb[s]<=-c.IMB_MIN));
 const open=D.trades.find(t=>t.e_s<=s&&t.x_s>=s);
 $('state').innerHTML=`<dl><dt>Best bid / ask</dt><dd class="mono">${(bb*TK).toFixed(2)} / ${(ba*TK).toFixed(2)} (spread ${ba-bb} tick${ba-bb>1?'s':''})</dd>
 <dt>Tape, this second</dt><dd class="mono"><span class="pos">${D.buy[s]} bought</span> · <span class="neg">${D.sell[s]} sold</span> (aggressor)</dd>
 <dt>Tape, last 30 s</dt><dd class="mono"><span class="pos">${b30}</span> − <span class="neg">${s30}</span> = delta30 ${D.delta[s]}</dd>
 <dt>Threshold now</dt><dd class="mono">${thr==null?'warming up (needs 10 min of history)':'±'+thr}</dd>
 <dt>Imbalance, best 5</dt><dd class="mono">${D.imb[s].toFixed(2)}</dd>
 <dt>Rule</dt><dd>${live?'<b>conditions met this second</b>':'not met'}</dd>
 <dt>Position</dt><dd>${open?(open.side>0?'LONG ':'SHORT ')+'from '+open.entry.toFixed(2)+', stop '+open.stop.toFixed(2)+', target '+open.target.toFixed(2):'flat'}</dd></dl>`;}
sl.addEventListener('input',()=>setCursor(+sl.value));
$('play').addEventListener('click',()=>{if(timer){clearInterval(timer);timer=null;$('play').textContent='▶ Play';return;}$('play').textContent='❚❚ Pause';
 timer=setInterval(()=>{if(cursor>=N-1){clearInterval(timer);timer=null;$('play').textContent='▶ Play';return;}const s=cursor+speed;if(s>v1){const w=v1-v0;v0=Math.min(N-1-w,s-Math.floor(w/5));v1=v0+w;}setCursor(Math.min(N-1,s));},100);});
$('slow').addEventListener('click',()=>{speed=speed===1?5:(speed===5?20:1);$('slow').textContent=(speed*10)+'×';});
window.addEventListener('resize',size);size();
const first=D.trades.length?D.trades[0].sig_s:Math.min(N-1,c.WARMUP_S);setCursor(first);
</script></body></html>
"""
