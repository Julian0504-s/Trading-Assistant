"""Forex-Bot-Test: an Forex angepasste Strategien auf 10 Paaren (ECB-Tageskurse ab 1999).

    uv run python scripts/fetch_forex.py      # einmalig
    uv run python scripts/forex_test.py

Strategien (Parameter aus der Literatur, NICHT an die Daten angepasst):
  A) trend_trail (Krypto-Strategie)           – Vergleich
  B) Turtle 55: Ausbruch 55 Tage, Stop 2 ATR, Trailing 3 ATR
  C) Turtle 55 nur in Richtung EMA 200
  D) EMA 50/200 "immer im Markt" (Wechsel beim Kreuz, Notfall-Stop 10 ATR)
  E) Zeitreihen-Momentum: monatlich Long, wenn 12-Monats-Rendite > 0, sonst Short;
     Gewichtung nach 60-Tage-Schwankung, Ziel 10 % Jahresschwankung je Portfolio

Entwicklung 1999–2015, Prüfung 2016–heute, je 10.000 Start. Kosten pro Seite 0,01 % +
0,005 % Slippage, Haltegebühr 0,005 % pro Tag und Position (beide Seiten). Hebel 3x und 10x,
Risiko 1 % pro Trade (A–D). Nur einmal täglich ein Kurs (ECB): Stops greifen zum Tageskurs.
Nur Simulation, keine Anlageempfehlung.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from data.ecb import PAIRS
from data.funding import FUNDING_INTERVAL_MS
from data.store import CandleStore
from indicators import add_all, donchian
from risk import RiskConfig
from signals.models import Signal
from signals.rules import cross_above, cross_below
from strategy import ExitConfig, Strategy
from strategy.backtest import Stream, index_signals, run_streams

CAP = 10_000.0
FEE, SLIP = 0.0001, 0.00005
HOLD_PER_DAY = 0.00005
PERIODS = {"Entwicklung 1999–2015": ("1999-01-01", "2015-12-31"),
           "Prüfung 2016–heute": ("2016-01-01", "2100-01-01"),
           "Gesamt": ("1999-01-01", "2100-01-01")}


def ms(ts: str) -> int:
    return int(pd.Timestamp(ts, tz="UTC").value // 1_000_000)


def custom_signals(df: pd.DataFrame, sym: str) -> dict[str, dict[int, list[Signal]]]:
    """Turtle-55-Ausbruch und EMA-50/200-Kreuz als Signale (je Kerzenschluss)."""
    t = lambda ts: int(ts.value // 1_000_000)  # noqa: E731
    out: dict[str, dict[int, list[Signal]]] = {"turtle": {}, "ma": {}}
    up = cross_above(df["close"], df["dc_high_55"])
    dn = cross_below(df["close"], df["dc_low_55"])
    for ts in df.index[up | dn]:
        d = "long" if up.loc[ts] else "short"
        out["turtle"].setdefault(t(ts), []).append(Signal(
            sym, "1d", "turtle55", d, 2, f"Ausbruch {'über das 55-Tage-Hoch' if d == 'long' else 'unter das 55-Tage-Tief'}",
            float(df.loc[ts, "close"]), t(ts)))
    up = cross_above(df["ema_50"], df["ema_200"])
    dn = cross_below(df["ema_50"], df["ema_200"])
    for ts in df.index[up | dn]:
        d = "long" if up.loc[ts] else "short"
        out["ma"].setdefault(t(ts), []).append(Signal(
            sym, "1d", "ma50_200", d, 2, f"EMA 50 kreuzt EMA 200 nach {'oben' if d == 'long' else 'unten'}",
            float(df.loc[ts, "close"]), t(ts)))
    return out


def summarize(eq: pd.Series, trades: int | None) -> dict:
    d = eq.resample("D").last().ffill()
    years = max((d.index[-1] - d.index[0]).days / 365, 1e-9)
    end = float(d.iloc[-1])
    dd = d / d.cummax() - 1
    yearly = d.resample("YE").last()
    yr = pd.concat([pd.Series([d.iloc[0]]), yearly]).pct_change().dropna() * 100
    return {"pa": ((end / d.iloc[0]) ** (1 / years) - 1) * 100, "end": end * CAP / float(d.iloc[0]),
            "dd": dd.min() * 100, "low": float(d.min()) * CAP / float(d.iloc[0]),
            "worst_year": float(yr.min()) if len(yr) else math.nan,
            "pos_years": f"{int((yr > 0).sum())}/{len(yr)}",
            "trades_pa": (trades / years) if trades is not None else None}


def tsmom(closes: pd.DataFrame, start: str, end: str, max_lev: float) -> tuple[pd.Series, int]:
    """Zeitreihen-Momentum (vektorisiert): monatliche Gewichte, tägliche Abrechnung."""
    rets = closes.pct_change()
    vol = rets.rolling(60).std() * np.sqrt(252)
    mom = closes / closes.shift(252) - 1
    month_end = closes.groupby(closes.index.tz_localize(None).to_period("M")).tail(1).index
    n = closes.shape[1]
    target = 0.10 / math.sqrt(n)  # je Paar; bei unkorrelierten Paaren ~10 % gesamt
    w = pd.DataFrame(np.nan, index=closes.index, columns=closes.columns)
    w.loc[month_end] = (np.sign(mom) * target / vol).loc[month_end]
    gross = w.abs().sum(axis=1)
    w = w.div(np.maximum(gross / max_lev, 1.0), axis=0)          # Hebel-Obergrenze
    w = w.ffill().fillna(0.0)
    w = w.shift(1).fillna(0.0)                                   # erst am Folgetag handeln
    window = (w.index >= pd.Timestamp(start, tz="UTC")) & (w.index <= pd.Timestamp(end, tz="UTC"))
    w, rets = w[window], rets[window].fillna(0.0)
    turnover = w.diff().abs().sum(axis=1).fillna(w.abs().sum(axis=1))
    daily = (w * rets).sum(axis=1) - turnover * (FEE + SLIP) - w.abs().sum(axis=1) * HOLD_PER_DAY
    eq = CAP * (1 + daily).cumprod()
    trades = int((w.diff().abs() > 1e-9).sum().sum())
    return eq, trades


def main() -> int:
    with CandleStore("data/forex.db") as cs:
        frames = {}
        for sym in PAIRS:
            df = add_all(cs.load_df(sym, "1d"))
            frames[sym] = df.join(donchian(df, 55))
    sigs_std = index_signals(frames, "1d")
    custom = {s: custom_signals(df, s) for s, df in frames.items()}
    end_all = max(int(df.index[-1].value // 1_000_000) for df in frames.values())
    hold = {s: pd.DataFrame({"time": range(-(-ms("1999-01-01") // FUNDING_INTERVAL_MS) * FUNDING_INTERVAL_MS,
                                           end_all + 86_400_000, FUNDING_INTERVAL_MS),
                             "rate": 0.0, "mark": float("nan"), "estimated": True})
            for s in frames}

    base = Strategy("x", "x", "x", frozenset(), min_strength=1)
    strategies = {
        "A) trend_trail (Krypto-Strategie)": ("std", replace(base, rules=frozenset({"ema_cross", "ema200_trend", "bb_breakout"}),
                                                            min_strength=2, with_trend_only=True,
                                                            exits=ExitConfig(stop_atr=3, target_atr=None, trail_atr=4))),
        "B) Turtle 55": ("turtle", replace(base, rules=frozenset({"turtle55"}),
                                          exits=ExitConfig(stop_atr=2, target_atr=None, trail_atr=3))),
        "C) Turtle 55 + EMA-200-Filter": ("turtle", replace(base, rules=frozenset({"turtle55"}), with_trend_only=True,
                                                           exits=ExitConfig(stop_atr=2, target_atr=None, trail_atr=3))),
        "D) EMA 50/200 immer im Markt": ("ma", replace(base, rules=frozenset({"ma50_200"}),
                                                       exits=ExitConfig(stop_atr=10, target_atr=None))),
    }
    closes = pd.DataFrame({s: df["close"] for s, df in frames.items()})
    report = []
    for lev in (3, 10):
        rc = RiskConfig(max_leverage=lev) if lev <= 3 else RiskConfig.for_research(0.01, max_leverage=lev)
        print(f"\n===== Hebel max. {lev}x {'(erlaubt)' if lev <= 3 else '(nur Forschung – CLAUDE.md erlaubt 3x)'}")
        print(f"  {'Strategie':<36}{'Zeitraum':<24}{'pro Jahr':>9}{'Endstand':>10}{'MaxDD':>8}"
              f"{'Tiefst':>9}{'schl.Jahr':>10}{'Jahre +':>9}{'Trades/J':>9}")
        for name, (kind, strat) in strategies.items():
            for pname, (a, b) in PERIODS.items():
                streams = [Stream(s, "1d", frames[s], strat,
                                  sigs_std.get(s, {}) if kind == "std" else custom[s][kind])
                           for s in frames]
                r = run_streams(streams, start_equity=CAP, risk=rc, trade_from=ms(a),
                                trade_until=min(ms(b), end_all), funding=hold,
                                fee_rate=FEE, slippage=SLIP, holding_cost=HOLD_PER_DAY / 3)
                st = summarize(r.equity, r.metrics["Trades"])
                report.append({"lev": lev, "strategy": name, "period": pname, **st})
                print(f"  {name:<36}{pname:<24}{st['pa']:>+8.1f}%{st['end']:>10,.0f}{st['dd']:>7.1f}%"
                      f"{st['low']:>9,.0f}{st['worst_year']:>+9.1f}%{st['pos_years']:>9}{st['trades_pa']:>9.0f}")
        name = "E) Zeitreihen-Momentum (monatlich)"
        for pname, (a, b) in PERIODS.items():
            eq, n_tr = tsmom(closes, a, b, lev)
            st = summarize(eq, n_tr)
            report.append({"lev": lev, "strategy": name, "period": pname, **st})
            print(f"  {name:<36}{pname:<24}{st['pa']:>+8.1f}%{st['end']:>10,.0f}{st['dd']:>7.1f}%"
                  f"{st['low']:>9,.0f}{st['worst_year']:>+9.1f}%{st['pos_years']:>9}{st['trades_pa']:>9.0f}")
    out = Path("data/research")
    out.mkdir(parents=True, exist_ok=True)
    (out / "forex.json").write_text(json.dumps(report, ensure_ascii=False, default=float))
    print("\nEndstand je Zeitraum mit 10.000 Start · Trades/J bei E = Gewichtsänderungen. "
          "Nur Simulation, keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
