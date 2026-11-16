"""Ideen-Vergleich über aufeinanderfolgende Halbjahre (Walk-forward-Prüfung).

    uv run python scripts/walkforward.py

Jede Idee ist vorab festgelegt (keine Parameter, die an die Daten angepasst
werden) und wird mit der Basis-Strategie (trend_trail auf 1d) verglichen –
in jedem der letzten 4 Halbjahre mit frischem Konto (1.000 USDT) und über
den Gesamtzeitraum.

Regel (vorab festgelegt): Eine Idee gilt als Verbesserung, wenn sie in
mindestens 3 von 4 Halbjahren mehr Rendite bringt als die Basis UND über den
Gesamtzeitraum ein mindestens gleich gutes Verhältnis Jahresrendite / max.
Drawdown hat.

Bericht: data/research/walkforward-<Zeitstempel>.json
Vergangene Ergebnisse sind keine Garantie für die Zukunft. Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from data.store import CandleStore
from indicators import add_all
from strategy import STRATEGIES, Strategy
from strategy.backtest import Stream, index_signals, run_streams
from strategy.regime import add_btc_regime

from common import DEFAULT_DB_PATH, load_dotenv, split_list

UNIVERSE = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT"
DAY = 86_400_000
HALF = 182 * DAY

BASE = STRATEGIES["trend_trail"]

# Idee -> Liste von (Timeframe, Strategie) im selben Konto
IDEAS: dict[str, list[tuple[str, Strategy]]] = {
    "Basis: Trendfolge mit Trailing-Stop (1d)": [("1d", BASE)],
    "Nur Long": [("1d", replace(BASE, name="long_only", long_only=True))],
    "BTC-Filter": [("1d", replace(BASE, name="btc_filter", btc_filter=True))],
    "Nur Long + BTC-Filter": [("1d", replace(BASE, name="long_btc", long_only=True,
                                             btc_filter=True))],
    "Nachkauf (max. 2×, alle 2 ATR)": [("1d", replace(BASE, name="pyramid", pyramid_max=2,
                                                      pyramid_step_atr=2.0))],
    "1d + 4h in einem Konto": [("1d", BASE), ("4h", BASE)],
    "Zum Vergleich: bisherige Trendfolge (1d)": [("1d", STRATEGIES["trend"])],
}
BASE_NAME = next(iter(IDEAS))


def mar(m: dict) -> float:
    cagr, dd = m["Rendite p.a. %"], m["Max. Drawdown %"]
    if cagr is None or (isinstance(cagr, float) and math.isnan(cagr)) or not dd:
        return -math.inf
    return cagr / abs(dd)


def clean(m: dict) -> dict:
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in m.items()}


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Ideen über Halbjahre vergleichen")
    p.add_argument("--symbols", default=UNIVERSE)
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--out", default="data/research")
    args = p.parse_args()
    symbols = split_list(args.symbols, upper=True)

    now = int(time.time() * 1000) // DAY * DAY
    periods = [(now - (4 - k) * HALF, now - (3 - k) * HALF) for k in range(4)]
    fmt = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).strftime("%m/%Y")  # noqa: E731

    tfs = sorted({tf for streams in IDEAS.values() for tf, _ in streams})
    frames, sigs = {}, {}
    with CandleStore(args.db) as store:
        for tf in tfs:
            fr = {s: add_all(df) for s in symbols if len(df := store.load_df(s, tf)) > 400}
            frames[tf] = add_btc_regime(fr)
            sigs[tf] = index_signals(frames[tf], tf)
    # Gesamtzeitraum: ab dem Zeitpunkt, an dem alle Timeframes handeln können
    full_from = max(min(df.index[200] for df in frames[tf].values()) for tf in tfs)
    full_from = int(full_from.value // 1_000_000)

    def run(name: str, start: int, end: int) -> dict:
        streams = [Stream(sym, tf, frames[tf][sym], strat, sigs[tf].get(sym, {}))
                   for tf, strat in IDEAS[name] for sym in frames[tf]]
        r = run_streams(streams, trade_from=start, trade_until=end, name=name)
        step = max(1, len(r.equity) // 400)
        return {"m": r.metrics,
                "equity": {"x": [t.isoformat() for t in r.equity.index[::step]],
                           "y": [round(float(v), 2) for v in r.equity.values[::step]]}}

    print(f"{len(symbols)} Coins · Halbjahre: " + ", ".join(f"{fmt(a)}–{fmt(b)}" for a, b in periods)
          + f" · Gesamt ab {fmt(full_from)}\n")
    results = {}
    for name in IDEAS:
        res = {"halves": [run(name, a, b) for a, b in periods], "full": run(name, full_from, now)}
        results[name] = res

    base = results[BASE_NAME]
    hdr = (f"{'Idee':<44}" + "".join(f"{fmt(a) + '–' + fmt(b):>17}" for a, b in periods)
           + f"{'Gesamt':>10}{'p.a.':>8}{'MaxDD':>8}{'Trades':>8}  Urteil")
    print(hdr)
    print("-" * len(hdr))
    report = {"periods": periods, "full_from": full_from, "symbols": symbols, "ideas": []}
    for name, res in results.items():
        wins = sum(h["m"]["Rendite %"] > b["m"]["Rendite %"]
                   for h, b in zip(res["halves"], base["halves"]))
        better = name != BASE_NAME and wins >= 3 and mar(res["full"]["m"]) >= mar(base["full"]["m"])
        if name == BASE_NAME:
            verdict = "Basis"
        elif name.startswith("Zum Vergleich"):
            verdict = f"{wins}/4 Halbjahre besser"
        else:
            verdict = f"{'VERBESSERUNG' if better else 'keine Verbesserung'} ({wins}/4)"
        fm = res["full"]["m"]
        print(f"{name:<44}" + "".join(f"{h['m']['Rendite %']:>+16.1f}%" for h in res["halves"])
              + f"{fm['Rendite %']:>+9.0f}%{fm['Rendite p.a. %']:>+7.1f}%{fm['Max. Drawdown %']:>7.1f}%"
              f"{fm['Trades']:>8}  {verdict}")
        report["ideas"].append({
            "name": name, "verdict": verdict, "better": better, "wins": wins,
            "halves": [clean(h["m"]) for h in res["halves"]], "full": clean(fm),
            "equity": res["full"]["equity"],
        })

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"walkforward-{stamp}.json").write_text(json.dumps(report, indent=1, ensure_ascii=False,
                                                                      default=lambda o: o.item()))
    (out / "walkforward-latest.txt").write_text(stamp)
    print(f"\nBericht: {out}/walkforward-{stamp}.json")
    print("Jedes Halbjahr mit frischem Konto (1.000 USDT). Keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
