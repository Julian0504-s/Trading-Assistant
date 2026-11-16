"""Langzeit-Backtest mit Binance-Archivdaten (ab 2020) – inklusive Bärenmarkt 2022.

    uv run python scripts/fetch_binance_history.py    # einmalig
    uv run python scripts/long_backtest.py

1. Plausibilitäts-Check: gleiche Strategie im gemeinsamen Zeitraum auf Bitunix-
   und Binance-Daten – nur wenn die Ergebnisse ähnlich sind, taugt Binance als
   Ersatz für die Zeit vor 04/2022.
2. Voller Zeitraum mit Startkapital (Standard 10.000 USDT): Kalenderjahre,
   tiefster Kontostand, größter Rückgang, längste Durststrecke, 2-Monats-Spannen.

Ergebnis zusätzlich in data/backtests/long/summary.json.
Vergangene Ergebnisse sind keine Garantie für die Zukunft. Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from data.funding import FundingStore, funding_schedule
from data.store import CandleStore
from indicators import add_all
from strategy import STRATEGIES
from strategy.backtest import Stream, index_signals, run_streams

from common import split_list

UNIVERSE = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT"
DAY = 86_400_000


def ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz="UTC").value // 1_000_000)


def load(db: str, symbols: list[str]):
    with CandleStore(db) as cs:
        frames = {s: add_all(df) for s in symbols if len(df := cs.load_df(s, "1d")) > 250}
    end = max(int(df.index[-1].value // 1_000_000) for df in frames.values())
    with FundingStore(db) as fs:
        funding = {s: funding_schedule(fs, s, 0, end + DAY) for s in frames}
    return frames, index_signals(frames, "1d"), funding


def run(data, capital, start=None, end=None, strategy="trend_trail"):
    frames, sigs, funding = data
    streams = [Stream(s, "1d", frames[s], STRATEGIES[strategy], sigs.get(s, {})) for s in frames]
    return run_streams(streams, start_equity=capital, trade_from=start, trade_until=end,
                       funding=funding)


def main() -> int:
    p = argparse.ArgumentParser(description="Langzeit-Backtest (Binance-Archiv)")
    p.add_argument("--symbols", default=UNIVERSE)
    p.add_argument("--capital", type=float, default=10_000)
    p.add_argument("--strategy", default="trend_trail")
    p.add_argument("--binance-db", default="data/binance.db")
    p.add_argument("--bitunix-db", default="data/market.db")
    args = p.parse_args()
    symbols = split_list(args.symbols, upper=True)
    cap = args.capital

    bn = load(args.binance_db, symbols)
    bx = load(args.bitunix_db, symbols)
    end = min(max(int(df.index[-1].value // 1_000_000) for df in d[0].values()) for d in (bn, bx))

    print("1) PLAUSIBILITÄTS-CHECK – gleicher Zeitraum, gleiche Strategie:")
    common_from = ms("2022-11-04")
    for name, d in (("Bitunix", bx), ("Binance", bn)):
        m = run(d, cap, common_from, end, args.strategy).metrics
        print(f"   {name:<8} {m['Rendite %']:+7.1f} %  max. Rückgang {m['Max. Drawdown %']:6.1f} %"
              f"  Trades {m['Trades']}")

    r = run(bn, cap, None, end, args.strategy)
    m, eq = r.metrics, r.equity
    print(f"\n2) VOLLER ZEITRAUM {eq.index[0]:%d.%m.%Y} – {eq.index[-1]:%d.%m.%Y} "
          f"(Binance-Daten, {cap:,.0f} USDT Start)")
    print(f"   Endkapital {r.end_equity:,.0f} USDT ({m['Rendite %']:+.0f} %, "
          f"{m['Rendite p.a. %']:+.1f} % pro Jahr)")
    peak = eq.cummax()
    dd = eq - peak
    lo = eq.idxmin()
    print(f"   Tiefster Kontostand: {eq.min():,.0f} USDT am {lo:%d.%m.%Y}")
    print(f"   Größter Rückgang: {m['Max. Drawdown %']:.1f} % = {dd.min():,.0f} USDT "
          f"(Tiefpunkt {dd.idxmin():%d.%m.%Y})")
    at_high = eq >= peak
    last_high = eq.index.to_series().where(at_high).ffill()
    flat = (eq.index.to_series() - last_high).dt.days
    print(f"   Längste Zeit ohne neuen Höchststand: {flat.max()} Tage (bis {flat.idxmax():%d.%m.%Y})")
    pnl = [t.pnl for t in sorted(r.trades, key=lambda t: t.exit_time)]
    streak = longest = 0
    for x in pnl:
        streak = streak + 1 if x <= 0 else 0
        longest = max(longest, streak)
    print(f"   {m['Trades']} Trades, Treffer {m['Trefferquote %']:.0f} %, längste Verlustserie "
          f"{longest} · Gebühren {m['Gebühren']:,.0f} · Funding {m['Funding']:,.0f} USDT")
    exd = r.exposure.resample("D").max()
    print(f"   Positionsvolumen/Konto: Median {exd.median():.2f}x, Maximum {exd.max():.2f}x")

    print("\n   Je Kalenderjahr (frisches Konto zu Jahresbeginn):")
    years = []
    first_year = eq.index[0].year
    for y in range(first_year, eq.index[-1].year + 1):
        a, b = max(ms(f"{y}-01-01"), int(eq.index[0].value // 1_000_000)), min(ms(f"{y + 1}-01-01") - 1, end)
        yr = run(bn, cap, a, b, args.strategy)
        ym = yr.metrics
        label = f"{y}" + (" (ab Juli)" if y == first_year else "") + (" (bis Okt.)" if y == eq.index[-1].year else "")
        print(f"   {label:<16} {yr.end_equity - cap:>+10,.0f} USDT ({ym['Rendite %']:+6.1f} %)  "
              f"größter Rückgang {ym['Max. Drawdown %']:6.1f} %  Trades {ym['Trades']}")
        years.append({"label": label, "pnl": yr.end_equity - cap, "ret": ym["Rendite %"],
                      "dd": ym["Max. Drawdown %"], "trades": ym["Trades"]})

    print("\n   Krisen im Detail (Konto zu Beginn des Ereignisses = 100 %):")
    crises = [("Mai 2021 (Crash nach Rekordhoch)", "2021-05-01", "2021-07-31"),
              ("LUNA-Kollaps", "2022-05-01", "2022-06-30"),
              ("FTX-Pleite", "2022-11-01", "2022-12-31"),
              ("Bärenmarkt 2022 gesamt", "2022-01-01", "2022-12-31")]
    crisis_out = []
    for label, a, b in crises:
        seg = eq[(eq.index >= pd.Timestamp(a, tz="UTC")) & (eq.index <= pd.Timestamp(b, tz="UTC"))]
        if len(seg) < 2:
            continue
        chg = (seg.iloc[-1] / seg.iloc[0] - 1) * 100
        worst = (seg / seg.cummax() - 1).min() * 100
        print(f"   {label:<34} Veränderung {chg:+6.1f} %  zwischenzeitlich {worst:6.1f} %")
        crisis_out.append({"label": label, "chg": chg, "worst": worst})

    d = eq.resample("D").last().ffill()
    two = (d.shift(-61) / d - 1).dropna() * 100
    print(f"\n   2 Monate: typisch {np.median(two):+.1f} %, im Plus {100 * (two > 0).mean():.0f} %, "
          f"schlechtester {two.min():+.1f} %, bester {two.max():+.1f} %")

    out = Path("data/backtests/long")
    out.mkdir(parents=True, exist_ok=True)
    step = max(1, len(eq) // 600)
    clean = {k: (None if isinstance(v, float) and not math.isfinite(v) else float(v)) for k, v in m.items()}
    (out / "summary.json").write_text(json.dumps({
        "metrics": clean, "years": years, "crises": crisis_out, "low": float(eq.min()),
        "low_date": f"{lo:%d.%m.%Y}", "max_dd_usdt": float(dd.min()), "flat_days": int(flat.max()),
        "longest_losing": longest, "start": f"{eq.index[0]:%d.%m.%Y}", "end": f"{eq.index[-1]:%d.%m.%Y}",
        "two_months": {"median": float(np.median(two)), "pos": float(100 * (two > 0).mean()),
                       "min": float(two.min()), "max": float(two.max())},
        "equity": {"x": [t.isoformat() for t in eq.index[::step]],
                   "y": [round(float(v), 2) for v in eq.values[::step]]},
    }, ensure_ascii=False, default=lambda o: o.item()))
    print("\nVergangene Ergebnisse sind keine Garantie. Keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
