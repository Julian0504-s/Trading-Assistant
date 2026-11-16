"""Backtest: Strategien × Timeframes auf den gespeicherten Kerzen der Watchlist.

Aufruf:
    uv run python scripts/fetch_candles.py --history     # einmalig: lange Historie laden
    uv run python scripts/backtest.py                    # alle Strategien, alle Timeframes
    uv run python scripts/backtest.py --strategies trend --timeframes 15m,1h

Jede Kombination aus Strategie und Timeframe ist ein eigenes simuliertes Konto
(Standard 1.000 USDT) über alle Symbole der Watchlist. Ergebnisse landen in
data/backtests/<Zeitstempel>/ (Übersicht, Trades, Order-Log als CSV).

Vergangene Ergebnisse sind keine Garantie für die Zukunft. Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from data.store import CandleStore
from indicators import add_all
from risk import RiskConfig
from strategy import STRATEGIES, export_orders_csv, run_backtest

from common import (
    DEFAULT_DB_PATH,
    DEFAULT_TIMEFRAMES,
    DEFAULT_WATCHLIST,
    check_timeframes,
    load_dotenv,
    split_list,
)


def _fmt(v: float, spec: str) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, float) and math.isinf(v):
        return "∞"
    return format(v, spec)


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Strategien auf historischen Kerzen testen")
    p.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    p.add_argument("--timeframes", default=os.getenv("TIMEFRAMES", DEFAULT_TIMEFRAMES))
    p.add_argument("--strategies", default=",".join(STRATEGIES),
                   help=f"Komma-getrennt aus: {', '.join(STRATEGIES)}")
    p.add_argument("--capital", type=float, default=1000.0, help="Startkapital in USDT")
    p.add_argument("--risk", type=float, default=0.01, help="Risiko pro Trade (max. 0.01)")
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--out", default="data/backtests")
    args = p.parse_args()

    symbols = split_list(args.symbols, upper=True)
    timeframes = check_timeframes(split_list(args.timeframes))
    names = split_list(args.strategies)
    unknown = [n for n in names if n not in STRATEGIES]
    if unknown:
        p.error(f"Unbekannte Strategien {unknown}")
    risk = RiskConfig(risk_per_trade=args.risk)

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out_dir = Path(args.out) / stamp
    summary = []

    print(f"Backtest · {', '.join(symbols)} · Startkapital {args.capital:,.0f} USDT · "
          f"Risiko {args.risk:.1%} pro Trade · Hebel max. {risk.max_leverage:g}x\n")
    header = (f"{'Strategie':<16}{'TF':>4}{'Zeitraum':>11}{'Trades':>8}{'Treffer':>9}"
              f"{'Rendite':>10}{'Max.DD':>9}{'PF':>6}{'Ø R':>7}{'Gebühren':>10}")
    print(header)
    print("-" * len(header))

    with CandleStore(args.db) as store:
        for tf in timeframes:
            frames = {}
            for sym in symbols:
                df = store.load_df(sym, tf)
                if len(df) > 300:
                    frames[sym] = add_all(df)
            if not frames:
                print(f"{'':<16}{tf:>4}  zu wenig Kerzen – erst fetch_candles.py --history")
                continue
            for name in names:
                res = run_backtest(frames, tf, STRATEGIES[name], start_equity=args.capital,
                                   risk=risk)
                m = res.metrics
                period = f"{m['Tage']:.0f} Tage"
                print(f"{STRATEGIES[name].label:<16}{tf:>4}{period:>11}{m['Trades']:>8}"
                      f"{_fmt(m['Trefferquote %'], '8.0f')}%{_fmt(m['Rendite %'], '+9.1f')}%"
                      f"{_fmt(m['Max. Drawdown %'], '8.1f')}%{_fmt(m['Profitfaktor'], '6.2f')}"
                      f"{_fmt(m['Ø R pro Trade'], '+7.2f')}{_fmt(m['Gebühren'], '10.2f')}")
                run_dir = out_dir / f"{name}_{tf}"
                export_orders_csv(res.orders, run_dir / "orders.csv")
                res.equity.rename("Kontostand").to_csv(run_dir / "equity.csv",
                                                       index_label="Zeit (UTC)")
                summary.append({
                    "strategy": name, "label": STRATEGIES[name].label, "timeframe": tf,
                    "symbols": list(frames), "start": res.start, "end": res.end,
                    "metrics": {k: (None if isinstance(v, float) and not math.isfinite(v) else v)
                                for k, v in m.items()},
                })
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    latest = Path(args.out) / "latest.txt"
    latest.write_text(stamp)
    print(f"\nErgebnisse: {out_dir}/  (Order-Log je Lauf als orders.csv)")
    print("PF = Profitfaktor (Gewinne/Verluste), Ø R = Ergebnis pro Trade in Vielfachen des Risikos.")
    print("Vergangene Ergebnisse sind keine Garantie für die Zukunft. Keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
