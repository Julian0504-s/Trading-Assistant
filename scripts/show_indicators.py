"""Zeigt die aktuellen Indikatorwerte für Watchlist × Timeframes aus der SQLite-DB.

Vorher Kerzen laden:  uv run python scripts/fetch_candles.py
Dann:                 uv run python scripts/show_indicators.py
                      uv run python scripts/show_indicators.py --symbols BTCUSDT --timeframes 1h,4h

Nur zur Kontrolle im Terminal – Hinweise, keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import math
import os
import sys

from data.store import CandleStore
from indicators import add_all

from common import (
    DEFAULT_DB_PATH,
    DEFAULT_TIMEFRAMES,
    DEFAULT_WATCHLIST,
    load_dotenv,
)


def _num(x: float, fmt: str) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else format(x, fmt)


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Aktuelle Indikatoren anzeigen")
    p.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    p.add_argument("--timeframes", default=os.getenv("TIMEFRAMES", DEFAULT_TIMEFRAMES))
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    args = p.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]

    header = (f"{'Symbol':<9}{'TF':>4} {'Kerze (UTC)':<17}{'Close':>11} {'Trend':<6}"
              f"{'EMA20/50':>9}{'RSI':>6}{'MACD-H':>10}{'ATR%':>6}{'BB %B':>7}{'Vol×':>6}")
    print(header)
    print("-" * len(header))
    with CandleStore(args.db) as store:
        for sym in symbols:
            for tf in timeframes:
                df = store.load_df(sym, tf)
                if df.empty:
                    print(f"{sym:<9}{tf:>4}  keine Daten – erst fetch_candles.py ausführen")
                    continue
                last = add_all(df).iloc[-1]
                ema200 = last["ema_200"]
                trend = "-" if math.isnan(ema200) else ("über" if last["close"] > ema200 else "unter")
                cross = "-"
                if not (math.isnan(last["ema_20"]) or math.isnan(last["ema_50"])):
                    cross = "20>50" if last["ema_20"] > last["ema_50"] else "20<50"
                atr_pct = last["atr_14"] / last["close"] * 100
                spike = " !" if last["volume_spike"] else ""
                print(
                    f"{sym:<9}{tf:>4} {last.name:%Y-%m-%d %H:%M} {last['close']:>11.6g} {trend:<6}"
                    f"{cross:>9}{_num(last['rsi_14'], '6.1f')}{_num(last['macd_hist'], '10.4g')}"
                    f"{_num(atr_pct, '6.2f')}{_num(last['bb_pct_b'], '7.2f')}"
                    f"{_num(last['volume_ratio'], '6.1f')}{spike}"
                )
    print("\nTrend = Close über/unter EMA 200 · Vol× = Volumen / Ø der letzten 20 Kerzen "
          "(! = Spike ≥ 2×)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
