"""Die gemerkten Bot-Varianten auf Gold und EUR/USD (Binance-Archiv als Kursquelle).

    uv run python scripts/fetch_binance_history.py --market spot --symbols PAXGUSDT,EURUSDT \
        --timeframe 1d --start 2020-01 --db data/markets.db      # und dasselbe mit --timeframe 4h
    uv run python scripts/markets_test.py

Kursquellen: PAXG (1 Token = 1 Unze Gold) und EUR/USDT, jeweils Binance-Spot.
Silber ist nicht testbar (Daten erst ab 2026).

Kosten:
  Gold    – wie auf Bitunix: 0,06 % Gebühr + 0,02 % Slippage pro Seite, Funding
            +0,012 % je 8 h (aus der Bitunix-Gold-Historie, Long zahlt).
  EUR/USD – wie bei einem Forex-Broker: 0,01 % + 0,005 % Slippage pro Seite,
            Haltegebühr ca. 0,0033 % je 8 h (Long zahlt; hängt vom Broker ab).
Je 10.000 USDT Start. Nur Simulation, keine Anlageempfehlung.
"""

from __future__ import annotations

import sys

import pandas as pd

from data.funding import FUNDING_INTERVAL_MS
from data.store import CandleStore
from indicators import add_all
from risk import RiskConfig
from strategy import STRATEGIES
from strategy.backtest import Stream, index_signals, run_streams

CAP = 10_000.0
TT = STRATEGIES["trend_trail"]
VARIANTS = [
    ("trend_trail@1d (1 %)", [("1d", TT)], 0.01),
    ("trend_trail@1d+4h~0.5 (0,5 %)", [("1d", TT), ("4h", TT)], 0.005),
    ("trend_trail@1d+4h (1 %)", [("1d", TT), ("4h", TT)], 0.01),
    ("trend_trail@4h (1 %)", [("4h", TT)], 0.01),
]
MARKETS = {
    "Gold (PAXG)": {"symbol": "PAXGUSDT", "fee": 0.0006, "slip": 0.0002, "carry": 0.00012},
    "EUR/USD": {"symbol": "EURUSDT", "fee": 0.0001, "slip": 0.00005, "carry": 0.000033},
}


def carry_schedule(start: int, end: int, rate: float) -> pd.DataFrame:
    t0 = -(-start // FUNDING_INTERVAL_MS) * FUNDING_INTERVAL_MS
    times = list(range(t0, end + 1, FUNDING_INTERVAL_MS))
    return pd.DataFrame({"time": times, "rate": rate, "mark": float("nan"), "estimated": True})


def stats(eq: pd.Series) -> dict:
    d = eq.resample("D").last().ffill()
    dd = d / d.cummax() - 1
    years = (d.index[-1] - d.index[0]).days / 365
    end = float(d.iloc[-1])
    return {"pa": ((end / CAP) ** (1 / years) - 1) * 100, "end": end, "dd": dd.min() * 100,
            "low": float(d.min()), "low_d": f"{d.idxmin():%m/%Y}", "high": float(d.max()),
            "worst_entry": CAP * (1 + dd.min()), "start": f"{d.index[0]:%m/%Y}"}


def main() -> int:
    with CandleStore("data/markets.db") as cs:
        frames = {name: {tf: add_all(cs.load_df(m["symbol"], tf)) for tf in ("1d", "4h")}
                  for name, m in MARKETS.items()}
    print(f"{'Markt / Variante':<38}{'ab':>8}{'pro Jahr':>10}{'Endstand':>11}{'Gewinn':>10}"
          f"{'Max.DD':>9}{'Tiefst':>9}{'schl. Einstieg':>16}{'Trades/J':>10}")
    for name, m in MARKETS.items():
        sym = m["symbol"]
        sigs = {tf: index_signals({sym: frames[name][tf]}, tf) for tf in ("1d", "4h")}
        start = int(frames[name]["1d"].index[200].value // 1_000_000)
        end = int(frames[name]["1d"].index[-1].value // 1_000_000)
        carry = {sym: carry_schedule(start, end + 86_400_000, m["carry"])}
        print(f"\n{name}")
        for label, plan, risk in VARIANTS:
            streams = [Stream(sym, tf, frames[name][tf], strat, sigs[tf].get(sym, {}))
                       for tf, strat in plan]
            r = run_streams(streams, start_equity=CAP, risk=RiskConfig(risk_per_trade=risk),
                            trade_from=start, trade_until=end, funding=carry,
                            fee_rate=m["fee"], slippage=m["slip"])
            st = stats(r.equity)
            years = (r.end - r.start) / (365 * 86_400_000)
            print(f"  {label:<36}{st['start']:>8}{st['pa']:>+9.1f}%{st['end']:>11,.0f}"
                  f"{st['end'] - CAP:>+10,.0f}{st['dd']:>8.1f}%{st['low']:>9,.0f}"
                  f"{st['worst_entry']:>16,.0f}{r.metrics['Trades'] / years:>10.0f}")
        px = frames[name]["1d"]["close"]
        bh = px[px.index >= pd.Timestamp(start, unit="ms", tz="UTC")] / px[px.index >= pd.Timestamp(start, unit="ms", tz="UTC")].iloc[0] * CAP
        st = stats(bh)
        print(f"  {'Zum Vergleich: kaufen und halten':<36}{st['start']:>8}{st['pa']:>+9.1f}%{st['end']:>11,.0f}"
              f"{st['end'] - CAP:>+10,.0f}{st['dd']:>8.1f}%{st['low']:>9,.0f}{st['worst_entry']:>16,.0f}{'–':>10}")
    print("\nNur Simulation, keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
