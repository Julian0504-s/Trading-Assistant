"""Verbesserungs-Check auf langen Daten (Binance-Archiv ab 2020), jeder Punkt getrennt.

    uv run python scripts/improvements.py --part 1   # Risiko pro Trade (nur Backtest!)
    uv run python scripts/improvements.py --part 2   # Ideen auf nie gesehenen Daten 2020–2022
    uv run python scripts/improvements.py --part 3   # mehr Coins

Basis immer: trend_trail auf 1d, 10 Coins, 1 % Risiko pro Trade, 10.000 USDT.

Teil 2 – Regel vorab festgelegt: Eine Idee gilt als Verbesserung, wenn sie im
Zeitraum 07/2020–11/2022 (den keine der Ideen je gesehen hat) mehr Rendite UND
ein mindestens gleich gutes Verhältnis Jahresrendite/Drawdown hat als die Basis,
und über den Gesamtzeitraum ebenfalls kein schlechteres Verhältnis.

Teil 1 nutzt RiskConfig.for_research – das ist NUR ein Gedankenexperiment; Paper
Trading und Live lehnen mehr als 1 % Risiko pro Trade ab.
Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import replace

import numpy as np
import pandas as pd

from data.funding import FundingStore, funding_schedule
from data.store import CandleStore
from indicators import add_all
from risk import RiskConfig
from strategy import STRATEGIES
from strategy.backtest import Stream, index_signals, run_streams
from strategy.regime import add_btc_regime

from common import split_list

BASE10 = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT"
EXTRA10 = "NEARUSDT,SUIUSDT,UNIUSDT,INJUSDT,AAVEUSDT,BCHUSDT,1000PEPEUSDT,XMRUSDT,WLDUSDT,TAOUSDT"
DAY = 86_400_000
CAP = 10_000.0
OOS_END = "2022-11-03"     # ab hier beginnen die Bitunix-Daten, auf denen entwickelt wurde
BASE = STRATEGIES["trend_trail"]


def ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz="UTC").value // 1_000_000)


class Data:
    """Kerzen, Signale und Funding je Timeframe aus einer oder mehreren Datenbanken."""

    def __init__(self, dbs: list[str], symbols: list[str], tfs: tuple[str, ...]):
        self.frames, self.sigs, self.funding = {}, {}, {}
        for tf in tfs:
            fr = {}
            for db in dbs:
                with CandleStore(db) as cs:
                    for s in symbols:
                        if s not in fr and len(df := cs.load_df(s, tf)) > 250:
                            fr[s] = add_all(df)
            self.frames[tf] = add_btc_regime(fr)
            self.sigs[tf] = index_signals(self.frames[tf], tf)
        end = max(int(df.index[-1].value // 1_000_000) for df in self.frames["1d"].values())
        self.end = end
        for db in dbs:
            with FundingStore(db) as fs:
                for s in self.frames["1d"]:
                    sch = funding_schedule(fs, s, 0, end + DAY)
                    if s not in self.funding or (~sch["estimated"]).sum() > (~self.funding[s]["estimated"]).sum():
                        self.funding[s] = sch

    def run(self, plan: list[tuple[str, object]], risk=None, start=None, end=None, symbols=None):
        streams = [Stream(s, tf, self.frames[tf][s], strat, self.sigs[tf].get(s, {}))
                   for tf, strat in plan for s in self.frames[tf]
                   if symbols is None or s in symbols]
        return run_streams(streams, start_equity=CAP, risk=risk, trade_from=start,
                           trade_until=end or self.end, funding=self.funding)


def mar(m: dict) -> float:
    c, d = m["Rendite p.a. %"], m["Max. Drawdown %"]
    return -math.inf if c is None or (isinstance(c, float) and math.isnan(c)) or not d else c / abs(d)


def summary(r) -> dict:
    m, eq = r.metrics, r.equity
    d = eq.resample("D").last().ffill()
    two = (d.shift(-61) / d - 1).dropna() * 100
    yearly = eq.resample("YE").last()
    yr = pd.concat([pd.Series([CAP]), yearly]).pct_change().dropna() * 100
    return {"end": r.end_equity, "pa": m["Rendite p.a. %"], "dd": m["Max. Drawdown %"],
            "dd_usdt": float((eq - eq.cummax()).min()), "low": float(eq.min()),
            "worst_2m": float(two.min()) if len(two) else math.nan,
            "worst_year": float(yr.min()), "trades": m["Trades"], "mar": mar(m)}


def row(label: str, s: dict) -> str:
    return (f"  {label:<38}{s['end']:>12,.0f}{s['pa']:>+8.1f}%{s['dd']:>8.1f}%{s['dd_usdt']:>11,.0f}"
            f"{s['low']:>10,.0f}{s['worst_2m']:>+9.1f}%{s['worst_year']:>+9.1f}%{s['trades']:>7}{s['mar']:>7.2f}")


HEAD = (f"  {'Variante':<38}{'Endkapital':>12}{'p.a.':>9}{'MaxDD':>9}{'MaxDD USDT':>11}"
        f"{'Tiefst':>10}{'schl.2M':>10}{'schl.Jahr':>10}{'Trades':>7}{'R/DD':>7}")


def part1(data: Data) -> None:
    print("PUNKT 1 – Risiko pro Trade (nur Gedankenexperiment im Backtest, Hebel max. 3x)\n")
    print(HEAD)
    for risk in (0.005, 0.01, 0.015, 0.02, 0.03):
        rc = RiskConfig(risk_per_trade=risk) if risk <= 0.01 else RiskConfig.for_research(risk)
        r = data.run([("1d", BASE)], risk=rc)
        exd = r.exposure.resample("D").max()
        label = f"{risk * 100:g} % Risiko{' (erlaubt)' if risk <= 0.01 else ''}"
        print(row(label, summary(r)) + f"   Volumen max. {exd.max():.1f}x")
    print("\n  R/DD = Jahresrendite / max. Drawdown. Tagesverlust-Limit 3 % gilt weiter.")


def part2(data: Data) -> None:
    print("PUNKT 2 – Ideen auf nie gesehenen Daten 07/2020–11/2022 (Regel vorab festgelegt)\n")
    ideas = {
        "Basis": [("1d", BASE)],
        "Nur Long": [("1d", replace(BASE, name="lo", long_only=True))],
        "BTC-Filter": [("1d", replace(BASE, name="btc", btc_filter=True))],
        "Nachkauf (max. 2×, alle 2 ATR)": [("1d", replace(BASE, name="pyr", pyramid_max=2,
                                                          pyramid_step_atr=2.0))],
        "1d + 4h in einem Konto": [("1d", BASE), ("4h", BASE)],
    }
    oos_end = ms(OOS_END)
    halves = [("2020-07-20", "2021-01-01"), ("2021-01-01", "2021-07-01"),
              ("2021-07-01", "2022-01-01"), ("2022-01-01", "2022-07-01"),
              ("2022-07-01", OOS_END)]
    res = {}
    for name, plan in ideas.items():
        if any(tf not in data.frames for tf, _ in plan):
            print(f"  {name}: Daten fehlen – übersprungen")
            continue
        oos = summary(data.run(plan, end=oos_end))
        full = summary(data.run(plan))
        hs = [data.run(plan, start=ms(a), end=ms(b)).metrics["Rendite %"] for a, b in halves]
        res[name] = (oos, full, hs)
    base_oos, base_full, base_h = res["Basis"]
    print(f"  {'Idee':<32}" + "".join(f"{a[2:7] + '–' + b[2:7]:>16}" for a, b in halves)
          + f"{'07/20–11/22':>13}{'MaxDD':>8}{'R/DD':>6}  {'gesamt p.a.':>11}{'MaxDD':>8}{'R/DD':>6}  Urteil")
    for name, (oos, full, hs) in res.items():
        if name == "Basis":
            verdict = "Basis"
        else:
            ok = (oos["end"] > base_oos["end"] and oos["mar"] >= base_oos["mar"]
                  and full["mar"] >= base_full["mar"])
            wins = sum(h > b for h, b in zip(hs, base_h))
            verdict = f"{'VERBESSERUNG' if ok else 'keine Verbesserung'} ({wins}/5 Halbjahre besser)"
        print(f"  {name:<32}" + "".join(f"{h:>+15.1f}%" for h in hs)
              + f"{(oos['end'] / CAP - 1) * 100:>+12.1f}%{oos['dd']:>7.1f}%{oos['mar']:>6.2f}"
              f"  {full['pa']:>+10.1f}%{full['dd']:>7.1f}%{full['mar']:>6.2f}  {verdict}")


def part3(data: Data) -> None:
    print("PUNKT 3 – mehr Coins (10 bisherige + 10 weitere)\n")
    base = BASE10.split(",")
    extra = [s for s in EXTRA10.split(",") if s in data.frames["1d"]]
    print(f"  weitere Coins mit Daten: {', '.join(s.replace('USDT', '') for s in extra)}")
    for s in extra:
        df = data.frames["1d"][s]
        print(f"    {s:<14} ab {df.index[0]:%m/%Y}" + (f" bis {df.index[-1]:%m/%Y}" if df.index[-1] < data.frames['1d']['BTCUSDT'].index[-1] - pd.Timedelta(days=5) else ""))
    print()
    print(HEAD)
    for label, syms, start in (("10 Coins – gesamt", base, None), ("20 Coins – gesamt", base + extra, None),
                               ("10 Coins – letzte 365 Tage", base, data.end - 365 * DAY),
                               ("20 Coins – letzte 365 Tage", base + extra, data.end - 365 * DAY)):
        r = data.run([("1d", BASE)], start=start, symbols=set(syms))
        exd = r.exposure.resample("D").max()
        print(row(label, summary(r)) + f"   Volumen Ø {exd.mean():.2f}x / max. {exd.max():.1f}x")
    print("\n  Achtung: Die zusätzlichen Coins sind heute liquide – Coins, die es nicht mehr gibt,")
    print("  fehlen in der Auswahl (Überlebenden-Effekt). Das schönt den Vergleich etwas.")


def main() -> int:
    p = argparse.ArgumentParser(description="Verbesserungs-Check (lange Daten)")
    p.add_argument("--part", type=int, choices=(1, 2, 3), required=True)
    args = p.parse_args()
    if args.part == 1:
        part1(Data(["data/binance.db"], split_list(BASE10), ("1d",)))
    elif args.part == 2:
        part2(Data(["data/binance.db"], split_list(BASE10), ("1d", "4h")))
    else:
        part3(Data(["data/binance.db", "data/binance_extra.db"],
                   split_list(BASE10) + split_list(EXTRA10), ("1d",)))
    print("\nVergangene Ergebnisse sind keine Garantie. Keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
