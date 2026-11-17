"""Daytrading mit Hebel × Risiko pro Trade über 3, 6, 9 und 12 Monate – NUR Simulation.

    uv run python scripts/daytrade_leverage.py

Strategien: die vier besten Daytrading-Varianten aus scripts/daytrade.py (15m und 1h,
mittlere Signalstärke, Stop 1,5 / Ziel 3 ATR, Market- oder Limit-Orders, strenge
Trendfilter 4h + 1d, ADX >= 25, Schluss zum Tagesende).

Hebel 3x bis 20x, Risiko 1–5 % pro Trade, Tagesverlust-Limit AUS. Liquidation wird
simuliert: isolated (Standard) = Marge je Position = Volumen / Hebel; --margin cross =
ganzes Konto haftet, Liquidation aller Positionen bei Unterschreiten der Erhaltungsmarge
(0,5 %). Fällt das Konto
unter 1 % des Startkapitals, gilt es als pleite.

Diese Einstellungen sind laut CLAUDE.md verboten (max. 3x, max. 1 %) und laufen nur über
RiskConfig.for_research – Paper Trading und Live lehnen sie ab. Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import time
from pathlib import Path

import pandas as pd

from data.funding import load_schedules
from data.store import CandleStore
from indicators import add_all
from risk import RiskConfig
from strategy.backtest import Stream, index_signals, run_streams
from strategy.regime import add_htf_trend

from common import DEFAULT_DB_PATH, split_list
from daytrade import UNIVERSE, variants

DAY = 86_400_000
CAP = 10_000.0
LEVERAGES = (3, 5, 8, 10, 15, 20)
RISKS = (0.01, 0.02, 0.03, 0.05)
MONTHS = (3, 6, 9, 12)
PICK = ("15m · ab mittel · Stop 1.5/Ziel 3 ATR", "1h · ab mittel · Stop 1.5/Ziel 3 ATR")


def main() -> int:
    p = argparse.ArgumentParser(description="Daytrading: Hebel × Risiko (nur Simulation)")
    p.add_argument("--symbols", default=UNIVERSE)
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--out", default="data/research")
    p.add_argument("--margin", choices=("isolated", "cross"), default="isolated",
                   help="isolated: je Position eigene Marge; cross: ganzes Konto haftet")
    args = p.parse_args()
    symbols = split_list(args.symbols, upper=True)
    now = int(time.time() * 1000) // DAY * DAY

    with CandleStore(args.db) as store:
        higher = {tf: {s: add_all(store.load_df(s, tf)) for s in symbols} for tf in ("4h", "1d")}
        ltf = {}
        for tf in ("15m", "1h"):
            fr = {s: add_all(store.load_df(s, tf)) for s in symbols}
            ltf[tf] = add_htf_trend(fr, tf, higher)
    sigs = {tf: index_signals(fr, tf) for tf, fr in ltf.items()}
    funding = load_schedules(args.db, symbols, now - 400 * DAY, now + DAY)

    strategies = [(tf, s, maker) for tf, s, maker in variants()
                  if any(s.label.startswith(x) for x in PICK)]
    results = []
    t0 = time.time()
    for (tf, strat, maker), months, lev, risk in itertools.product(strategies, MONTHS, LEVERAGES, RISKS):
        rc = RiskConfig.for_research(risk, max_leverage=lev, daily_loss_limit=1.0)
        start = now - int(months * 30.44 * DAY)
        streams = [Stream(s, tf, ltf[tf][s], strat, sigs[tf].get(s, {})) for s in ltf[tf]]
        r = run_streams(streams, start_equity=CAP, risk=rc, trade_from=start, trade_until=now,
                        funding=funding, maker_fee=maker, margin_mode=args.margin)
        eq = r.equity
        ruined = eq[eq < CAP * 0.01]
        liqs = sum(t.exit_reason.startswith("Liquidation") for t in r.trades)
        results.append({
            "strategy": strat.label, "months": months, "leverage": lev, "risk": risk,
            "end": r.end_equity, "ret": (r.end_equity / CAP - 1) * 100,
            "dd": r.metrics["Max. Drawdown %"], "low": float(eq.min()) if len(eq) else CAP,
            "high": float(eq.max()) if len(eq) else CAP, "trades": r.metrics["Trades"],
            "liquidations": liqs,
            "ruined": f"{ruined.index[0]:%d.%m.%Y}" if len(ruined) else None,
        })
    print(f"{len(results)} Simulationen ({args.margin} margin) in {time.time() - t0:.0f}s\n")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"daytrade-leverage-{args.margin}.json").write_text(json.dumps(results, ensure_ascii=False,
                                                           default=lambda o: o.item()))

    df = pd.DataFrame(results)
    for label in df["strategy"].unique():
        sub = df[df["strategy"] == label]
        print(f"=== {label}")
        print(f"  {'Hebel':>5} {'Risiko':>6} " + "".join(f"{f'{m} Mon.':>22}" for m in MONTHS))
        for lev, risk in itertools.product(LEVERAGES, RISKS):
            cells = []
            for m in MONTHS:
                x = sub[(sub["months"] == m) & (sub["leverage"] == lev) & (sub["risk"] == risk)].iloc[0]
                cell = (f"PLEITE {x['ruined'][:5]}" if isinstance(x["ruined"], str) else
                        f"{x['ret']:+7.0f}% ({x['end']:,.0f})")
                cells.append(f"{cell:>22}")
            print(f"  {lev:>4}x {risk * 100:>5.0f}% " + "".join(cells))
        print()

    pos = df[df["ret"] > 0]
    print(f"Im Plus: {len(pos)} von {len(df)} Kombinationen · pleite: {df['ruined'].map(lambda v: isinstance(v, str)).sum()} · "
          f"mit mind. einer Liquidation: {(df['liquidations'] > 0).sum()}")
    for m in MONTHS:
        best = df[df["months"] == m].sort_values("ret").iloc[-1]
        print(f"  Beste Kombination {m:>2} Monate: {best['strategy']} · {best['leverage']}x · "
              f"{best['risk'] * 100:.0f}% -> {best['ret']:+.0f} % ({best['end']:,.0f} USDT)")
    print(f"\nAlle Ergebnisse: {out}/daytrade-leverage-{args.margin}.json · Nur Simulation, keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
