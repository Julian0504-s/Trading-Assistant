"""Daytrading mit weitem Stop-Loss (Cross Margin) – NUR Simulation.

    uv run python scripts/daytrade_widestop.py

Idee: Stop weit weg, damit sich Positionen öfter erholen. Variiert werden Stop
(1,5–12 ATR), Ziel (1,5/3/6 ATR) und ob zum Tagesende geschlossen wird. Basis:
15m und 1h mit Limit-Orders, strenge Trendfilter (4h + 1d), ADX >= 25,
Cross Margin, 10x Hebel, 1 % Risiko pro Trade (Positionsgröße passt sich dem
Stop an), Tagesverlust-Limit aus. Danach für die beste Kombination Hebel × Risiko.

Hebel über 3x ist laut CLAUDE.md verboten – nur über RiskConfig.for_research.
Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from data.funding import load_schedules
from data.store import CandleStore
from indicators import add_all
from risk import RiskConfig
from strategy import ExitConfig, Strategy
from strategy.backtest import Stream, index_signals, run_streams
from strategy.regime import add_htf_trend

from common import DEFAULT_DB_PATH, split_list
from daytrade import MAKER, RULES, UNIVERSE

DAY = 86_400_000
CAP = 10_000.0
MONTHS = (3, 6, 9, 12)


def main() -> int:
    p = argparse.ArgumentParser(description="Daytrading mit weitem Stop (nur Simulation)")
    p.add_argument("--symbols", default=UNIVERSE)
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--out", default="data/research")
    args = p.parse_args()
    symbols = split_list(args.symbols, upper=True)
    now = int(time.time() * 1000) // DAY * DAY

    with CandleStore(args.db) as store:
        higher = {tf: {s: add_all(store.load_df(s, tf)) for s in symbols} for tf in ("4h", "1d")}
        ltf = {tf: add_htf_trend({s: add_all(store.load_df(s, tf)) for s in symbols}, tf, higher)
               for tf in ("15m", "1h")}
    sigs = {tf: index_signals(fr, tf) for tf, fr in ltf.items()}
    funding = load_schedules(args.db, symbols, now - 400 * DAY, now + DAY)

    def run(tf, strat, months, lev=10, risk=0.01):
        rc = RiskConfig.for_research(risk, max_leverage=lev, daily_loss_limit=1.0)
        streams = [Stream(s, tf, ltf[tf][s], strat, sigs[tf].get(s, {})) for s in ltf[tf]]
        return run_streams(streams, start_equity=CAP, risk=rc, funding=funding, maker_fee=MAKER,
                           trade_from=now - int(months * 30.44 * DAY), trade_until=now,
                           margin_mode="cross")

    rows = []
    t0 = time.time()
    for tf, stop, target, flat in itertools.product(("15m", "1h"), (1.5, 3, 5, 8, 12),
                                                    (1.5, 3, 6), (True, False)):
        strat = Strategy(name="ws", label="ws", description="", rules=RULES, min_strength=2,
                         with_trend_only=True, htf_filter=True, adx_min=25, flat_at_day_end=flat,
                         exits=ExitConfig(stop_atr=stop, target_atr=target))
        for months in MONTHS:
            r = run(tf, strat, months)
            tr = r.trades
            wins = [t.pnl for t in tr if t.pnl > 0]
            losses = [t.pnl for t in tr if t.pnl <= 0]
            rows.append({"tf": tf, "stop": stop, "target": target, "flat": flat, "months": months,
                         "ret": (r.end_equity / CAP - 1) * 100, "end": r.end_equity,
                         "dd": r.metrics["Max. Drawdown %"], "trades": len(tr),
                         "hit": 100 * len(wins) / len(tr) if tr else np.nan,
                         "avg_win": float(np.mean(wins)) if wins else 0.0,
                         "avg_loss": float(np.mean(losses)) if losses else 0.0,
                         "fees": r.metrics["Gebühren"],
                         "stopped": sum("Stop" in t.exit_reason for t in tr)})
    print(f"{len(rows)} Simulationen in {time.time() - t0:.0f}s (Cross Margin, 10x, 1 % Risiko, Limit-Orders)\n")
    df = pd.DataFrame(rows)

    for tf in ("15m", "1h"):
        print(f"=== {tf}  (Rendite je Zeitraum · Trefferquote · Ø Gewinn / Ø Verlust im 12-Monats-Lauf)")
        print(f"  {'Stop':>5} {'Ziel':>5} {'Tagesende':>10}" + "".join(f"{f'{m} Mon.':>9}" for m in MONTHS)
              + f"{'Treffer':>9}{'Ø Gewinn':>10}{'Ø Verlust':>11}{'Gebühren':>10}")
        sub = df[df["tf"] == tf]
        for (stop, target, flat), g in sub.groupby(["stop", "target", "flat"], sort=True):
            g = g.set_index("months")
            y = g.loc[12]
            print(f"  {stop:>5g} {target:>5g} {'ja' if flat else 'nein':>10}"
                  + "".join(f"{g.loc[m, 'ret']:>+8.0f}%" for m in MONTHS)
                  + f"{y['hit']:>8.0f}%{y['avg_win']:>+10.0f}{y['avg_loss']:>+11.0f}{y['fees']:>10.0f}")
        print()

    df["score"] = df.groupby(["tf", "stop", "target", "flat"])["ret"].transform("mean")
    pos_all = df.groupby(["tf", "stop", "target", "flat"])["ret"].apply(lambda s: (s > 0).all())
    print(f"Kombinationen, die in ALLEN vier Zeiträumen im Plus sind: {int(pos_all.sum())} von {len(pos_all)}")
    best = df.sort_values("score").iloc[-1]
    print(f"Beste im Schnitt: {best['tf']} · Stop {best['stop']:g} / Ziel {best['target']:g} ATR · "
          f"Tagesende {'ja' if best['flat'] else 'nein'} · Ø {best['score']:+.1f} % über die 4 Zeiträume")

    strat = Strategy(name="best", label="best", description="", rules=RULES, min_strength=2,
                     with_trend_only=True, htf_filter=True, adx_min=25,
                     flat_at_day_end=bool(best["flat"]),
                     exits=ExitConfig(stop_atr=float(best["stop"]), target_atr=float(best["target"])))
    print("\nBeste Kombination mit Hebel × Risiko (Cross Margin):")
    print(f"  {'Hebel':>5} {'Risiko':>6}" + "".join(f"{f'{m} Mon.':>22}" for m in MONTHS))
    grid = []
    for lev, risk in itertools.product((3, 5, 10, 20), (0.01, 0.02, 0.03, 0.05)):
        cells = []
        for m in MONTHS:
            r = run(best["tf"], strat, m, lev, risk)
            eq = r.equity
            broke = eq[eq < CAP * 0.01]
            cell = (f"PLEITE {broke.index[0]:%d.%m}" if len(broke)
                    else f"{(r.end_equity / CAP - 1) * 100:+.0f}% ({r.end_equity:,.0f})")
            cells.append(f"{cell:>22}")
            grid.append({"leverage": lev, "risk": risk, "months": m, "end": r.end_equity,
                         "ruined": bool(len(broke))})
        print(f"  {lev:>4}x {risk * 100:>5.0f}%" + "".join(cells))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "daytrade-widestop.json").write_text(json.dumps(
        {"grid": rows, "best": {k: (v.item() if hasattr(v, "item") else v) for k, v in best.items()},
         "leverage_grid": grid}, ensure_ascii=False, default=lambda o: o.item()))
    print(f"\nAlle Ergebnisse: {out}/daytrade-widestop.json · Nur Simulation, keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
