"""Balayage de paramètres piloté par le VRAI moteur LEAN (grid search).

Ce script réplique exactement la mécanique de l'optimiseur natif de LEAN
(`Optimizer.Launcher/ConsoleLeanOptimizer.cs`) : pour chaque combinaison de
paramètres, il lance un processus LEAN enfant avec

    --parameters period_fast:50,period_slow:200
    --results-destination-folder <dossier isolé>
    --close-automatically true

puis lit le `*-summary.json` produit. Chaque point de la grille est donc un
backtest LEAN complet et exact (pas un proxy vectorisé) — au prix d'être plus
lent, ce que la parallélisation compense.

  Sortie : backtests/optimize/results/<strategie>_grid.csv (une ligne/combo,
           toutes les métriques), + le meilleur combo imprimé selon la cible.
  Ensuite : heatmap.py lit ce CSV pour tracer les heatmaps 2D et 3D.

Exemples
    python backtests/optimize/run_grid.py                 # grille SMA par défaut
    python backtests/optimize/run_grid.py --workers 6
    python backtests/optimize/run_grid.py --fast 20,50 --slow 100,200   # petite grille
    python backtests/optimize/run_grid.py --force         # ignore le cache, tout relancer

Rien n'est figé : bornes de dates lues dans le CSV côté algo, métriques lues
dans les JSON produits par le moteur.
"""
from __future__ import annotations

import argparse
import csv
import glob
import itertools
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# ── Emplacements (tout en absolu : le Launcher doit tourner depuis son bin/Release
#    à cause des chemins relatifs de config.json)
ROOT = Path(__file__).resolve().parents[3]                 # .../Backtesting
SCRIPTS_DIR = Path(__file__).resolve().parent               # formation/01-lean/scripts
LAUNCHER_DIR = ROOT / "backtests" / "lean" / "Launcher" / "bin" / "Release"
LAUNCHER_DLL = "QuantConnect.Lean.Launcher.dll"
RUNS_DIR = SCRIPTS_DIR / "runs"
RESULTS_DIR = SCRIPTS_DIR / "results"


def detect_python_env() -> tuple[str, str]:
    """Trouve python311.dll + son PYTHONHOME (pont pythonnet de LEAN, cf. leçon 01).

    Ordre : $LEAN_PY_ENV, puis $CONDA_PREFIX, puis les emplacements conda usuels.
    Renvoie (PYTHONNET_PYDLL, PYTHONHOME). Modifiable par variable d'environnement.
    """
    candidates = []
    if os.environ.get("LEAN_PY_ENV"):
        candidates.append(Path(os.environ["LEAN_PY_ENV"]))
    if os.environ.get("CONDA_PREFIX"):
        candidates.append(Path(os.environ["CONDA_PREFIX"]))
    home = Path.home()
    for base in ("anaconda3", "miniconda3", "miniforge3"):
        candidates.append(home / base / "envs" / "backtesting")
    for env in candidates:
        dll = env / "python311.dll"
        if dll.exists():
            return str(dll), str(env)
    raise SystemExit(
        "python311.dll introuvable. Pose la variable LEAN_PY_ENV sur le dossier de "
        "ton env conda 'backtesting' (celui qui contient python311.dll)."
    )


# ── Métriques extraites du `-summary.json`. col_csv -> (clé JSON, type)
METRICS = [
    ("net_profit", "Net Profit", "pct"),
    ("car", "Compounding Annual Return", "pct"),
    ("drawdown", "Drawdown", "pct"),
    ("sharpe", "Sharpe Ratio", "num"),
    ("sortino", "Sortino Ratio", "num"),
    ("psr", "Probabilistic Sharpe Ratio", "pct"),
    ("win_rate", "Win Rate", "pct"),
    ("loss_rate", "Loss Rate", "pct"),
    ("profit_loss_ratio", "Profit-Loss Ratio", "num"),
    ("total_orders", "Total Orders", "int"),
    ("total_fees", "Total Fees", "money"),
]


def _num(raw) -> float:
    """'-4.277%' -> -4.277 ; '$826.64' -> 826.64 ; '1,234' -> 1234.0 ; '' -> nan."""
    s = str(raw).strip().replace("%", "").replace("$", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def parse_summary(summary_path: Path) -> dict:
    stats = json.load(open(summary_path, encoding="utf-8"))["statistics"]
    out = {}
    for col, key, kind in METRICS:
        if key not in stats:
            out[col] = float("nan")
            continue
        val = _num(stats[key])
        out[col] = int(val) if (kind == "int" and val == val) else val
    return out


class Strategy:
    """Décrit une stratégie balayable : sa classe LEAN, son fichier, sa grille."""

    def __init__(self, name, class_name, algo_file, param_grid, valid=None, default=None):
        self.name = name                      # ex. "sma"
        self.class_name = class_name          # ex. "SmaOptimizable"
        self.algo_file = algo_file            # chemin absolu du .py
        self.param_grid = param_grid          # dict ordonné {nom: [valeurs]}
        self.valid = valid or (lambda **kw: True)
        self.default = default                # combo de référence (réglage de la formation)

    def combos(self):
        names = list(self.param_grid)
        for values in itertools.product(*(self.param_grid[n] for n in names)):
            combo = dict(zip(names, values))
            if self.valid(**combo):
                yield combo


def combo_id(combo: dict) -> str:
    """{'period_fast':50,'period_slow':200} -> 'period_fast-050_period_slow-200'."""
    return "_".join(f"{k}-{v:03d}" if isinstance(v, int) else f"{k}-{v}"
                    for k, v in combo.items())


def run_one(strat: Strategy, combo: dict, env: dict, force: bool) -> dict:
    """Lance UN backtest LEAN pour une combinaison ; renvoie params + métriques."""
    out_dir = RUNS_DIR / strat.name / combo_id(combo)
    out_dir.mkdir(parents=True, exist_ok=True)

    existing = glob.glob(str(out_dir / "*-summary.json"))
    if existing and not force:
        row = dict(combo); row.update(parse_summary(Path(existing[0])))
        row["status"] = "cache"
        return row

    params = ",".join(f"{k}:{v}" for k, v in combo.items())
    cmd = [
        "dotnet", LAUNCHER_DLL,
        "--algorithm-language", "Python",
        "--algorithm-type-name", strat.class_name,
        "--algorithm-location", str(strat.algo_file),
        "--parameters", params,
        "--results-destination-folder", str(out_dir),
        "--close-automatically", "true",
    ]
    with open(out_dir / "run.log", "w", encoding="utf-8") as log:
        # NB : on N'utilise PAS le code de sortie (le ResultsAnalyzer de LEAN lève une
        # exception post-backtest sur notre donnée custom -> exit 82, mais les fichiers
        # de résultats sont déjà écrits). Le vrai juge = présence du -summary.json.
        subprocess.run(cmd, cwd=LAUNCHER_DIR, env=env,
                       stdout=log, stderr=subprocess.STDOUT)

    found = glob.glob(str(out_dir / "*-summary.json"))
    row = dict(combo)
    if found:
        row.update(parse_summary(Path(found[0])))
        row["status"] = "ok"
    else:
        row.update({col: float("nan") for col, *_ in METRICS})
        row["status"] = "FAILED"
    return row


def sweep(strat: Strategy, workers: int, force: bool, target: str):
    env = os.environ.copy()
    dll, home = detect_python_env()
    env["PYTHONNET_PYDLL"], env["PYTHONHOME"] = dll, home
    print(f"[env] PYTHONNET_PYDLL = {dll}")

    combos = list(strat.combos())
    print(f"[grille] {strat.name} : {len(combos)} combinaisons valides, "
          f"{workers} en parallèle\n")

    rows, t0 = [], time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(run_one, strat, c, env, force): c for c in combos}
        for i, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            rows.append(row)
            tag = {"ok": "  ", "cache": "≈ ", "FAILED": "✗ "}[row["status"]]
            print(f"  {tag}[{i:>3}/{len(combos)}] {combo_id(futures[fut]):<34} "
                  f"{target}={row.get(target, float('nan')):+.3f}  "
                  f"net={row.get('net_profit', float('nan')):+.2f}%")

    # tri stable par les paramètres pour un CSV lisible
    param_names = list(strat.param_grid)
    rows.sort(key=lambda r: tuple(r[n] for n in param_names))
    cols = param_names + [c for c, *_ in METRICS] + ["status"]
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = RESULTS_DIR / f"{strat.name}_grid.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)

    ok = [r for r in rows if r["status"] != "FAILED" and r.get(target) == r.get(target)]
    best = max(ok, key=lambda r: r[target]) if ok else None
    dt = time.time() - t0
    print(f"\n[fini] {len(rows)} runs en {dt:.0f} s -> {csv_path}")
    if best:
        bp = ", ".join(f"{n}={best[n]}" for n in param_names)
        # trades affiché : un « meilleur » à 0 trade = combo dégénéré (reste à plat)
        print(f"[meilleur | {target}] ({bp}) : {target}={best[target]:+.3f}, "
              f"net={best['net_profit']:+.2f}%, dd={best['drawdown']:.1f}%, "
              f"sharpe={best['sharpe']:+.3f}, trades={best['total_orders']}")
    failed = [r for r in rows if r["status"] == "FAILED"]
    if failed:
        print(f"[!] {len(failed)} combinaison(s) sans résultat "
              f"(voir runs/{strat.name}/<combo>/run.log)")
    return csv_path


# ── Catalogue des stratégies balayables. Chacune : 2 paramètres balayés (heatmap 2D),
#    défauts = réglage de la formation (contrôle de conformité au chiffre près).
STRATEGIES = {
    "sma": Strategy(
        name="sma",
        class_name="SmaOptimizable",
        algo_file=SCRIPTS_DIR / "algos" / "sma_optimizable.py",
        param_grid={"period_fast": [10, 20, 30, 40, 50, 60, 80, 100],
                    "period_slow": [50, 75, 100, 150, 200, 250, 300]},
        default={"period_fast": 50, "period_slow": 200},
        valid=lambda period_fast, period_slow: period_fast < period_slow,
    ),
    "macd": Strategy(
        name="macd",
        class_name="MacdOptimizable",
        algo_file=SCRIPTS_DIR / "algos" / "macd_optimizable.py",
        param_grid={"ema_fast": [6, 8, 12, 16, 20, 26],
                    "ema_slow": [20, 26, 35, 50, 75, 100]},   # signal fixé à 9
        default={"ema_fast": 12, "ema_slow": 26},
        valid=lambda ema_fast, ema_slow: ema_fast < ema_slow,
    ),
    "bollinger": Strategy(
        name="bollinger",
        class_name="BollingerOptimizable",
        algo_file=SCRIPTS_DIR / "algos" / "bollinger_optimizable.py",
        param_grid={"period_bb": [10, 14, 20, 30, 40, 50],
                    "k_std": [1.5, 2.0, 2.5, 3.0]},
        default={"period_bb": 20, "k_std": 2.0},
    ),
    "rsi": Strategy(
        name="rsi",
        class_name="RsiOptimizable",
        algo_file=SCRIPTS_DIR / "algos" / "rsi_optimizable.py",
        # seuil = survente ; surachat = 100 - seuil (symétrique, géré dans l'algo)
        param_grid={"rsi_period": [7, 10, 14, 21, 30],
                    "seuil": [10, 15, 20, 25, 30, 35, 40]},
        default={"rsi_period": 14, "seuil": 30},
        valid=lambda rsi_period, seuil: seuil < 50,
    ),
}


def _parse_val(s: str):
    """'12' -> 12 (int) ; '2.5' -> 2.5 (float). Préserve le type pour --parameters."""
    s = s.strip()
    try:
        return int(s)
    except ValueError:
        return float(s)


def main():
    ap = argparse.ArgumentParser(description="Balayage de paramètres via le vrai moteur LEAN.")
    ap.add_argument("strategy", nargs="?", default="sma", choices=list(STRATEGIES))
    ap.add_argument("--workers", type=int, default=4, help="backtests LEAN en parallèle")
    ap.add_argument("--force", action="store_true", help="ignore le cache, relance tout")
    ap.add_argument("--target", default="sharpe", help="métrique à maximiser pour 'le meilleur'")
    ap.add_argument("--set", action="append", default=[], metavar="PARAM=V1,V2,...",
                    help="surcharge la liste d'un paramètre (répétable), ex. --set ema_fast=12,16")
    args = ap.parse_args()

    strat = STRATEGIES[args.strategy]
    for spec in args.set:
        key, _, vals = spec.partition("=")
        key = key.strip()
        if key not in strat.param_grid:
            ap.error(f"paramètre inconnu '{key}' pour {strat.name} "
                     f"(attendus : {list(strat.param_grid)})")
        strat.param_grid[key] = [_parse_val(v) for v in vals.split(",")]

    sweep(strat, workers=args.workers, force=args.force, target=args.target)


if __name__ == "__main__":
    main()
