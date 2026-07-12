#!/usr/bin/env python3
"""Reconstruction de chandelles OHLCV à pas LIBRE depuis les trades — AUTONOME (stdlib seule).

Lit la base produite par `binance_history.py` (table `trades(trade_id PK, ts, price,
size, side)`) et reconstruit des chandelles :
    open / high / low / close / volume + volume agresseur "buy" + nombre de trades,
à **n'importe quel pas de temps** — `--tf` accepte `<n>s`, `<n>m`, `<n>H`, `<n>D` (et `D`
seul) : `30s`, `1m`, `5m`, `15m`, `1H`, `4H`, `D`… Bornes alignées sur l'UTC (minuit UTC
pour D). Le projet vise le scalping/HFT : défaut `1m 1H D` (le 1m est la vue de travail,
1H/D le contexte).

POURQUOI une seule passe ?
  Les `trade_id` (aggTrades) sont croissants dans le temps : parcourir la table dans
  l'ordre du `trade_id` (= le rowid) donne les trades DÉJÀ triés chronologiquement, sans
  tri coûteux ni index `ts`. On agrège en streaming au **PGCD des pas demandés** (ex. 1m
  pour `1m 1H D`), puis on **rollup** chaque pas demandé : tout pas étant un multiple du
  PGCD, chaque borne large coïncide avec une borne fine → l'agrégat est exact.

SORTIE :
  - par défaut : un tableau texte des N dernières chandelles de chaque pas de temps ;
  - `--csv DOSSIER` : écrit aussi la série complète en CSV (un fichier par pas de temps) ;
  - `--chart FICHIER.html` : écrit un **graphique chandelier interactif AUTONOME** (canvas,
    aucune dépendance / aucun CDN — ouvrable hors-ligne) avec volume, crosshair, zoom (molette)
    et pan (glisser), et un sélecteur de pas de temps. `--open` l'ouvre dans le navigateur.

EXEMPLES :
  python candles.py                          # 20 dernières 1m/1H/D de la base par défaut
  python candles.py --tf 1m --tail 50        # 50 dernières chandelles 1 minute
  python candles.py --tf 15s 1m 5m           # résolutions scalping (n'importe quel pas)
  python candles.py --csv ./candles          # persiste tout en CSV
  python candles.py --chart btc.html --open  # graphique chandelier interactif (ouvre le navigateur)
  python candles.py --limit 2000000          # aperçu rapide (2 M premiers trades)
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import os
import pathlib
import re
import sqlite3
import sys
import time
import webbrowser

# Pas de temps -> largeur en millisecondes (alignés sur l'epoch = minuit UTC).
SEC_MS = 1_000
MIN_MS = 60 * SEC_MS
HOUR_MS = 60 * MIN_MS
DAY_MS = 24 * HOUR_MS
UNIT_MS = {"s": SEC_MS, "m": MIN_MS, "h": HOUR_MS, "d": DAY_MS}
TF_DEFAULT = ["1m", "1H", "D"]        # scalping : le 1m est la vue de travail, 1H/D le contexte


def parse_tf(spec: str) -> tuple[str, int]:
    """`30s` / `1m` / `4H` / `D` -> (nom canonique, largeur en ms). Pas libre, unité s/m/H/D."""
    m = re.fullmatch(r"(\d*)([smhdSMHD])", spec.strip())
    n = int(m.group(1) or 1) if m else 0
    if not m or n <= 0:
        raise argparse.ArgumentTypeError(
            f"pas de temps invalide : « {spec} » (attendu <n>s|m|H|D, ex. 30s, 1m, 4H, D)")
    unit = m.group(2).lower()
    canon = "D" if (unit == "d" and n == 1) else f"{n}{'H' if unit == 'h' else 'D' if unit == 'd' else unit}"
    return canon, n * UNIT_MS[unit]

# Index d'une chandelle (tuple compact, pas d'objet par chandelle).
START, OPEN, HIGH, LOW, CLOSE, VOL, BUYVOL, N = range(8)


# --------------------------------------------------------------------------- #
# Construction des chandelles au pas de base en streaming (une passe sur la table)
# --------------------------------------------------------------------------- #
def build_base(conn: sqlite3.Connection, width_ms: int,
               limit: int = 0, progress: bool = True) -> list[tuple]:
    """Parcourt les trades dans l'ordre du trade_id et émet les chandelles au pas donné."""
    sql = "SELECT ts, price, size, side='buy' FROM trades ORDER BY trade_id"
    if limit > 0:
        sql += f" LIMIT {int(limit)}"
    cur = conn.execute(sql)

    out: list[tuple] = []
    cur_b = -1
    o = hi = lo = cl = 0.0
    vol = bvol = 0.0
    n = 0
    seen = 0
    t0 = time.monotonic()

    for ts, price, size, isbuy in cur:
        b = ts // width_ms * width_ms
        if b != cur_b:
            if cur_b >= 0:
                out.append((cur_b, o, hi, lo, cl, vol, bvol, n))
            cur_b = b
            o = hi = lo = cl = price
            vol = size
            bvol = size if isbuy else 0.0
            n = 1
        else:
            if price > hi:
                hi = price
            elif price < lo:
                lo = price
            cl = price
            vol += size
            if isbuy:
                bvol += size
            n += 1
        if progress:
            seen += 1
            if seen % 20_000_000 == 0:
                el = time.monotonic() - t0
                print(f"  … {seen // 1_000_000} M trades scannés "
                      f"({int(seen / el):,}/s)".replace(",", " "), file=sys.stderr)

    if cur_b >= 0:
        out.append((cur_b, o, hi, lo, cl, vol, bvol, n))
    return out


def rollup(base: list[tuple], width_ms: int) -> list[tuple]:
    """Agrège des chandelles fines en chandelles plus larges (largeur multiple du pas fin)."""
    out: list[tuple] = []
    cur_b = -1
    o = hi = lo = cl = vol = bvol = 0.0
    n = 0
    for c in base:
        b = c[START] // width_ms * width_ms
        if b != cur_b:
            if cur_b >= 0:
                out.append((cur_b, o, hi, lo, cl, vol, bvol, n))
            cur_b = b
            o, hi, lo, cl = c[OPEN], c[HIGH], c[LOW], c[CLOSE]
            vol, bvol, n = c[VOL], c[BUYVOL], c[N]
        else:
            if c[HIGH] > hi:
                hi = c[HIGH]
            if c[LOW] < lo:
                lo = c[LOW]
            cl = c[CLOSE]
            vol += c[VOL]
            bvol += c[BUYVOL]
            n += c[N]
    if cur_b >= 0:
        out.append((cur_b, o, hi, lo, cl, vol, bvol, n))
    return out


# --------------------------------------------------------------------------- #
# Affichage / écriture
# --------------------------------------------------------------------------- #
def fdate(ms: int, width_ms: int) -> str:
    """Date UTC adaptée au pas : jour seul (>= 1D), +heures:minutes, +secondes (< 1m)."""
    d = dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc)
    if width_ms >= DAY_MS:
        return d.strftime("%Y-%m-%d")
    if width_ms < MIN_MS:
        return d.strftime("%Y-%m-%d %H:%M:%S")
    return d.strftime("%Y-%m-%d %H:%M")


def num(x: float, dec: int) -> str:
    return f"{x:,.{dec}f}".replace(",", " ")


def print_table(tf: str, width_ms: int, candles: list[tuple],
                tail: int, symbol: str, market: str) -> None:
    span = (f"{fdate(candles[0][START], width_ms)} → {fdate(candles[-1][START], width_ms)}"
            if candles else "—")
    print(f"\n== {symbol} [{market}] — chandelles {tf} : "
          f"{len(candles)} au total ({span} UTC) ==")
    if not candles:
        print("  (aucune)")
        return
    dh = 10 if width_ms >= DAY_MS else 19 if width_ms < MIN_MS else 16
    print(f"{'date (UTC)':<{dh}}  {'open':>11} {'high':>11} {'low':>11} {'close':>11} "
          f"{'volume':>14} {'buy%':>6} {'trades':>10}")
    for c in candles[-tail:]:
        buy_pct = (100 * c[BUYVOL] / c[VOL]) if c[VOL] else 0.0
        print(f"{fdate(c[START], width_ms):<{dh}}  "
              f"{num(c[OPEN], 2):>11} {num(c[HIGH], 2):>11} {num(c[LOW], 2):>11} "
              f"{num(c[CLOSE], 2):>11} {num(c[VOL], 3):>14} {buy_pct:>5.1f}% "
              f"{num(c[N], 0):>10}")


def write_csv(path: str, candles: list[tuple]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "date_utc", "open", "high", "low", "close",
                    "volume", "buy_volume", "sell_volume", "trades"])
        for c in candles:
            w.writerow([c[START], fdate(c[START], SEC_MS), c[OPEN], c[HIGH], c[LOW],
                        c[CLOSE], c[VOL], c[BUYVOL], c[VOL] - c[BUYVOL], c[N]])


# --------------------------------------------------------------------------- #
# Graphique chandelier HTML autonome (canvas, zéro dépendance / zéro CDN)
# --------------------------------------------------------------------------- #
def write_chart(path: str, candles_by_tf: dict[str, list[tuple]],
                tfs: list[str], symbol: str, market: str) -> None:
    """Écrit un graphique chandelier interactif dans un .html autonome (ouvrable hors-ligne).

    Les chandelles sont embarquées en JSON ; un petit moteur de rendu canvas (vanilla JS,
    aucune lib externe) dessine OHLC + volume, avec crosshair, zoom molette, pan glisser et
    un sélecteur de pas de temps. Cohérent avec la philosophie « stdlib pure » du projet."""
    # [start_ms, open, high, low, close, volume, buy_volume, trades] — arrondis pour alléger.
    data = {
        tf: [[c[START], round(c[OPEN], 2), round(c[HIGH], 2), round(c[LOW], 2),
              round(c[CLOSE], 2), round(c[VOL], 4), round(c[BUYVOL], 4), c[N]]
             for c in candles_by_tf[tf]]
        for tf in tfs
    }
    default_tf = tfs[-1]  # le pas le plus fin (les listes vont du plus large au plus fin)
    html = (CHART_HTML
            .replace("__TITLE__", f"{symbol} [{market}]")
            .replace("__DATA__", json.dumps(data, separators=(",", ":")))
            .replace("__TFS__", json.dumps(tfs))
            .replace("__DEFAULT__", json.dumps(default_tf)))
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)


CHART_HTML = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__ — chandelles</title>
<style>
  :root { color-scheme: dark; }
  html,body { margin:0; height:100%; background:#0d1117; color:#c9d1d9;
              font:13px/1.4 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
  #wrap { display:flex; flex-direction:column; height:100%; }
  header { padding:10px 14px; display:flex; align-items:center; gap:14px; flex-wrap:wrap;
           border-bottom:1px solid #21262d; }
  header h1 { font-size:15px; margin:0; font-weight:600; }
  .tfbtns { display:flex; gap:4px; }
  .tfbtns button { background:#161b22; color:#c9d1d9; border:1px solid #30363d;
                   padding:4px 12px; border-radius:6px; cursor:pointer; font:inherit; }
  .tfbtns button.active { background:#1f6feb; border-color:#1f6feb; color:#fff; }
  .meta { color:#8b949e; font-size:12px; }
  #chart { flex:1; position:relative; }
  canvas { display:block; width:100%; height:100%; cursor:crosshair; }
  #tip { position:absolute; pointer-events:none; background:#161b22ee; border:1px solid #30363d;
         border-radius:6px; padding:8px 10px; font-size:12px; white-space:nowrap; display:none;
         box-shadow:0 4px 16px #0008; z-index:5; }
  #tip b { color:#fff; } #tip .up { color:#26a69a; } #tip .dn { color:#ef5350; }
  #hint { color:#6e7681; font-size:11px; }
</style>
</head>
<body>
<div id="wrap">
  <header>
    <h1>__TITLE__</h1>
    <div class="tfbtns" id="tfbtns"></div>
    <span class="meta" id="meta"></span>
    <span id="hint">molette = zoom · glisser = défiler · double-clic = réinitialiser</span>
  </header>
  <div id="chart"><canvas id="cv"></canvas><div id="tip"></div></div>
</div>
<script>
const DATA = __DATA__, TFS = __TFS__;
let TF = __DEFAULT__;
const cv = document.getElementById('cv'), ctx = cv.getContext('2d');
const tip = document.getElementById('tip'), meta = document.getElementById('meta');
const PADL = 8, PADR = 64, PADT = 12, PADB = 24, VOLH = 0.18; // ratio hauteur volume
let series = DATA[TF], view = {i0: 0, n: 0}, hover = -1, dpr = 1;

function fmtDate(ms, mode) { // 0 = jour seul, 1 = +HH:MM, 2 = +HH:MM:SS
  const d = new Date(ms);
  const p = x => String(x).padStart(2,'0');
  let s = d.getUTCFullYear()+'-'+p(d.getUTCMonth()+1)+'-'+p(d.getUTCDate());
  if (mode >= 1) s += ' '+p(d.getUTCHours())+':'+p(d.getUTCMinutes());
  if (mode >= 2) s += ':'+p(d.getUTCSeconds());
  return s;
}
const tmode = () => /D$/.test(TF) ? 0 : (/s$/.test(TF) ? 2 : 1); // selon le pas courant
function fmtNum(x, dec){ return x.toLocaleString('fr-FR',{minimumFractionDigits:dec,maximumFractionDigits:dec}); }

function resetView(){
  const n = series.length;
  view.n = Math.min(n, 200);          // ~200 dernières chandelles par défaut
  view.i0 = Math.max(0, n - view.n);
}
function clampView(){
  view.n = Math.max(5, Math.min(series.length, Math.round(view.n)));
  view.i0 = Math.max(0, Math.min(series.length - view.n, Math.round(view.i0)));
}

function resize(){
  dpr = window.devicePixelRatio || 1;
  const r = cv.getBoundingClientRect();
  cv.width = Math.max(1, r.width*dpr); cv.height = Math.max(1, r.height*dpr);
  draw();
}

function draw(){
  const W = cv.width, H = cv.height;
  ctx.clearRect(0,0,W,H);
  if (!series.length) return;
  clampView();
  const i0 = view.i0, n = view.n, slice = series.slice(i0, i0+n);

  const plotL = PADL*dpr, plotR = W - PADR*dpr, plotW = plotR - plotL;
  const plotT = PADT*dpr, plotB = H - PADB*dpr, plotH = plotB - plotT;
  const priceH = plotH*(1-VOLH), volTop = plotT + priceH + 6*dpr, volH = plotB - volTop;

  let lo = Infinity, hi = -Infinity, vmax = 0;
  for (const c of slice){ if (c[3]<lo) lo=c[3]; if (c[2]>hi) hi=c[2]; if (c[5]>vmax) vmax=c[5]; }
  if (lo===hi){ lo*=0.999; hi*=1.001; }
  const pad = (hi-lo)*0.06; lo-=pad; hi+=pad;
  const y = p => plotT + (hi-p)/(hi-lo)*priceH;
  const cw = plotW/n, bw = Math.max(1*dpr, cw*0.62);

  // grille + axe prix (droite)
  ctx.strokeStyle = '#21262d'; ctx.fillStyle = '#8b949e';
  ctx.lineWidth = 1*dpr; ctx.font = (11*dpr)+'px sans-serif';
  ctx.textAlign='left'; ctx.textBaseline='middle';
  const STEPS = 6;
  for (let k=0;k<=STEPS;k++){
    const p = lo + (hi-lo)*k/STEPS, yy = y(p);
    ctx.beginPath(); ctx.moveTo(plotL,yy); ctx.lineTo(plotR,yy); ctx.stroke();
    ctx.fillText(fmtNum(p,2), plotR+6*dpr, yy);
  }
  // axe temps (bas)
  ctx.textAlign='center'; ctx.textBaseline='top';
  const tlabels = Math.min(8, n);
  for (let k=0;k<tlabels;k++){
    const idx = Math.floor(k*(n-1)/Math.max(1,tlabels-1));
    const x = plotL + (idx+0.5)*cw;
    ctx.strokeStyle='#161b22'; ctx.beginPath(); ctx.moveTo(x,plotT); ctx.lineTo(x,plotB); ctx.stroke();
    ctx.fillStyle='#8b949e'; ctx.fillText(fmtDate(slice[idx][0], tmode()), x, plotB+4*dpr);
  }

  // chandelles + volume
  for (let k=0;k<n;k++){
    const c = slice[k], x = plotL + (k+0.5)*cw;
    const up = c[4] >= c[1], col = up ? '#26a69a' : '#ef5350';
    // volume
    const vh = vmax>0 ? c[5]/vmax*volH : 0;
    ctx.fillStyle = up ? '#26a69a55' : '#ef535055';
    ctx.fillRect(x-bw/2, plotB-vh, bw, vh);
    // mèche
    ctx.strokeStyle = col; ctx.lineWidth = Math.max(1, dpr*0.8);
    ctx.beginPath(); ctx.moveTo(x, y(c[2])); ctx.lineTo(x, y(c[3])); ctx.stroke();
    // corps
    const yo = y(c[1]), yc = y(c[4]);
    ctx.fillStyle = col;
    ctx.fillRect(x-bw/2, Math.min(yo,yc), bw, Math.max(1*dpr, Math.abs(yc-yo)));
  }

  // crosshair + tooltip
  if (hover>=0 && hover<n){
    const c = slice[hover], x = plotL + (hover+0.5)*cw;
    ctx.strokeStyle='#6e7681'; ctx.setLineDash([4*dpr,4*dpr]); ctx.lineWidth=1*dpr;
    ctx.beginPath(); ctx.moveTo(x,plotT); ctx.lineTo(x,plotB); ctx.stroke(); ctx.setLineDash([]);
    const up = c[4]>=c[1], cls = up?'up':'dn', buyPct = c[5]>0 ? 100*c[6]/c[5] : 0;
    tip.innerHTML =
      '<b>'+fmtDate(c[0], 2)+' UTC</b><br>'+
      'O '+fmtNum(c[1],2)+'  H '+fmtNum(c[2],2)+'<br>'+
      'L '+fmtNum(c[3],2)+'  C <span class="'+cls+'">'+fmtNum(c[4],2)+'</span><br>'+
      'Vol '+fmtNum(c[5],3)+'  ·  achat '+buyPct.toFixed(1)+'%<br>'+
      'trades '+fmtNum(c[7],0);
    tip.style.display='block';
    const r = cv.getBoundingClientRect();
    let tx = x/dpr + 14, ty = 14;
    if (tx + tip.offsetWidth > r.width) tx = x/dpr - tip.offsetWidth - 14;
    tip.style.left = tx+'px'; tip.style.top = ty+'px';
  } else tip.style.display='none';

  const s0 = slice[0][0], s1 = slice[n-1][0];
  meta.textContent = series.length+' chandelles '+TF+' · vue '+fmtDate(s0,tmode())+
                     ' → '+fmtDate(s1,tmode())+' UTC';
}

// interactions
function xToIndex(clientX){
  const r = cv.getBoundingClientRect();
  const plotL = PADL, plotR = r.width - PADR, cw = (plotR-plotL)/view.n;
  return Math.floor((clientX - r.left - plotL)/cw);
}
cv.addEventListener('mousemove', e=>{ hover = xToIndex(e.clientX); draw(); });
cv.addEventListener('mouseleave', ()=>{ hover=-1; draw(); });
cv.addEventListener('wheel', e=>{
  e.preventDefault();
  const f = e.deltaY>0 ? 1.15 : 1/1.15;
  const anchor = Math.max(0, Math.min(view.n-1, xToIndex(e.clientX)));
  const newN = Math.max(5, Math.min(series.length, Math.round(view.n*f)));
  view.i0 = view.i0 + anchor - Math.round(anchor*newN/view.n);
  view.n = newN; clampView(); draw();
}, {passive:false});
let drag=null;
cv.addEventListener('mousedown', e=>{ drag={x:e.clientX, i0:view.i0}; cv.style.cursor='grabbing'; });
window.addEventListener('mouseup', ()=>{ drag=null; cv.style.cursor='crosshair'; });
window.addEventListener('mousemove', e=>{
  if(!drag) return;
  const r = cv.getBoundingClientRect();
  const cw = (r.width-PADL-PADR)/view.n;
  view.i0 = drag.i0 - Math.round((e.clientX-drag.x)/cw);
  clampView(); draw();
});
cv.addEventListener('dblclick', ()=>{ resetView(); draw(); });

// boutons pas de temps
const tfbtns = document.getElementById('tfbtns');
TFS.forEach(tf=>{
  const b = document.createElement('button'); b.textContent = tf;
  if (tf===TF) b.classList.add('active');
  b.onclick = ()=>{ TF=tf; series=DATA[tf]; hover=-1; resetView();
                    [...tfbtns.children].forEach(x=>x.classList.toggle('active', x.textContent===tf));
                    draw(); };
  tfbtns.appendChild(b);
});

window.addEventListener('resize', resize);
resetView(); resize();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- #
def main() -> int:
    p = argparse.ArgumentParser(
        description="Reconstruit des chandelles OHLCV à pas libre depuis les trades (sqlite).")
    p.add_argument("--db",
                   default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "BTCUSDT-binance-perp-api.db"),
                   help="base sqlite des trades (défaut : data\\BTCUSDT-binance-perp-api.db)")
    p.add_argument("--tf", nargs="+", type=parse_tf, default=[parse_tf(t) for t in TF_DEFAULT],
                   metavar="PAS", help="pas de temps libres : <n>s|m|H|D, ex. 30s 1m 5m 1H D "
                                       f"(défaut : {' '.join(TF_DEFAULT)})")
    p.add_argument("--tail", type=int, default=20, help="nb de chandelles affichées par pas")
    p.add_argument("--csv", default=None, metavar="DOSSIER",
                   help="écrire la série complète en CSV (un fichier par pas de temps)")
    p.add_argument("--chart", default=None, metavar="FICHIER.html",
                   help="écrire un graphique chandelier interactif autonome (canvas, zéro dépendance)")
    p.add_argument("--open", action="store_true",
                   help="ouvrir le graphique (--chart) dans le navigateur après écriture")
    p.add_argument("--limit", type=int, default=0,
                   help="ne scanner que les N premiers trades (aperçu rapide ; 0 = tout)")
    args = p.parse_args()

    # Pas demandés, dédoublonnés, du plus large au plus fin ; base de streaming = leur PGCD
    # (tout pas demandé en est un multiple -> chaque rollup est exact).
    tf_ms = dict(sorted(set(args.tf), key=lambda t: -t[1]))
    tf_order = list(tf_ms)
    base_ms = math.gcd(*tf_ms.values())

    if not os.path.exists(args.db):
        print(f"ERREUR : base introuvable : {args.db}", file=sys.stderr)
        return 1

    # Lecture SEULE (mode=ro) : ne crée pas de -wal, ne verrouille pas la base en écriture.
    uri = pathlib.Path(args.db).resolve().as_uri() + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        symbol = market = "?"
        try:
            meta = dict(conn.execute("SELECT k, v FROM _meta").fetchall())
            symbol, market = meta.get("symbol", "?"), meta.get("market", "?")
        except sqlite3.Error:
            pass

        print(f"Lecture des trades depuis {args.db}"
              + (f" (limite {args.limit:,} trades)".replace(",", " ") if args.limit else "")
              + " — une passe, peut prendre 1–2 min sur l'historique complet…")
        t0 = time.monotonic()
        base = build_base(conn, base_ms, limit=args.limit)
    finally:
        conn.close()

    if not base:
        print("Aucun trade -> aucune chandelle.")
        return 0

    candles = {tf: base if ms == base_ms else rollup(base, ms) for tf, ms in tf_ms.items()}
    total = sum(c[N] for c in base)
    print(f"{total:,}".replace(",", " ")
          + f" trades agrégés en {time.monotonic() - t0:.0f}s "
          + " / ".join(f"{len(candles[tf])} {tf}" for tf in tf_order) + ".")

    if args.csv:
        os.makedirs(args.csv, exist_ok=True)
        for tf in tf_order:
            path = os.path.join(args.csv, f"{symbol}-{market}-{tf}.csv")
            write_csv(path, candles[tf])
            print(f"  CSV : {path}  ({len(candles[tf])} chandelles)")

    if args.chart:
        d = os.path.dirname(os.path.abspath(args.chart))
        if d:
            os.makedirs(d, exist_ok=True)
        write_chart(args.chart, candles, tf_order, symbol, market)
        print(f"  Graphique : {args.chart}  ({', '.join(f'{len(candles[tf])} {tf}' for tf in tf_order)})")
        if args.open:
            webbrowser.open(pathlib.Path(args.chart).resolve().as_uri())

    for tf in tf_order:
        print_table(tf, tf_ms[tf], candles[tf], args.tail, symbol, market)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
