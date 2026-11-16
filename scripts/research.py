"""Strategie-Forschung mit sauberer Trennung von Entwicklung und Prüfung.

    uv run python scripts/research.py

1. ENTWICKLUNG: Alle Varianten (Einstiegsregeln × ADX-Filter × Ausstiege) laufen
   auf den Daten VOR dem Prüfzeitraum.
2. AUSWAHL (vorab festgelegt, nicht nachträglich angepasst):
   zugelassen ist eine Variante mit genug Trades, Profitfaktor >= 1,1 und
   positiver Rendite; pro Timeframe gewinnt die mit dem besten Verhältnis
   Jahresrendite / max. Drawdown.
3. PRÜFUNG: Nur die Gewinner (plus die bisherige Strategie als Vergleich)
   laufen einmal auf dem Prüfzeitraum (letzte 365 Tage), den sie nie gesehen haben.

Ergebnis: data/research/<Zeitstempel>/report.json und Ausgabe im Terminal.
Vergangene Ergebnisse sind keine Garantie für die Zukunft. Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from data.funding import load_schedules
from data.store import CandleStore
from indicators import add_all
from strategy import STRATEGIES, ExitConfig, Strategy
from strategy.backtest import index_signals, run_backtest

from common import DEFAULT_DB_PATH, load_dotenv, split_list

UNIVERSE = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT"
DAY = 86_400_000
MIN_TRADES = {"1h": 100, "4h": 60, "1d": 25}

ENTRIES = {
    "Trend-Signale": dict(rules=frozenset({"ema_cross", "ema200_trend", "bb_breakout"}),
                          min_strength=2),
    "Donchian": dict(rules=frozenset({"donchian_breakout"}), min_strength=1),
    "Trend + Donchian": dict(rules=frozenset({"ema_cross", "ema200_trend", "bb_breakout",
                                              "donchian_breakout"}), min_strength=2),
}
ADX = [0, 20, 25]
EXITS = [
    ExitConfig(stop_atr=1.5, target_atr=3.0),
    ExitConfig(stop_atr=2.0, target_atr=4.0),
    ExitConfig(stop_atr=2.0, target_atr=6.0),
    ExitConfig(stop_atr=2.0, target_atr=None, trail_atr=3.0),
    ExitConfig(stop_atr=3.0, target_atr=None, trail_atr=4.0),
]


def variants() -> list[Strategy]:
    out = []
    for (ename, e), adx, ex in itertools.product(ENTRIES.items(), ADX, EXITS):
        label = f"{ename} · {'ADX ≥ ' + str(adx) if adx else 'ohne ADX'} · {ex.label}"
        out.append(Strategy(name=f"v{len(out) + 1}", label=label, description=label,
                            with_trend_only=True, adx_min=adx, exits=ex, **e))
    return out


def score(m: dict) -> float:
    """Jahresrendite / |max. Drawdown| – höher ist besser."""
    cagr, dd = m["Rendite p.a. %"], m["Max. Drawdown %"]
    if cagr is None or math.isnan(cagr) or not dd:
        return -math.inf
    return cagr / abs(dd)


def eligible(m: dict, tf: str) -> bool:
    return (m["Trades"] >= MIN_TRADES.get(tf, 30) and (m["Profitfaktor"] or 0) >= 1.1
            and m["Rendite %"] > 0)


def clean(m: dict) -> dict:
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in m.items()}


def line(label: str, m: dict) -> str:
    def f(v, spec):
        return "–" if v is None or (isinstance(v, float) and not math.isfinite(v)) else format(v, spec)
    return (f"  {label:<62}{m['Trades']:>6}{f(m['Trefferquote %'], '7.0f')}%"
            f"{f(m['Rendite %'], '+9.1f')}%{f(m['Rendite p.a. %'], '+8.1f')}%"
            f"{f(m['Max. Drawdown %'], '8.1f')}%{f(m['Profitfaktor'], '6.2f')}"
            f"{f(m['Wochen im Plus %'], '6.0f')}%{f(m['Beste Woche %'], '+7.1f')}%"
            f"{f(m['Schlechteste Woche %'], '+7.1f')}%")


HEADER = (f"  {'Variante':<62}{'Trades':>6}{'Treff':>8}{'Rendite':>10}{'p.a.':>9}{'MaxDD':>9}"
          f"{'PF':>6}{'W+':>7}{'beste':>8}{'schl.':>8}")


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Strategie-Forschung (Entwicklung/Prüfung getrennt)")
    p.add_argument("--symbols", default=UNIVERSE)
    p.add_argument("--timeframes", default="1h,4h,1d")
    p.add_argument("--test-days", type=int, default=365)
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--out", default="data/research")
    args = p.parse_args()

    symbols = split_list(args.symbols, upper=True)
    tfs = split_list(args.timeframes)
    now = int(time.time() * 1000)
    test_from = (now - args.test_days * DAY) // DAY * DAY
    vs = variants()
    report = {"test_from": test_from, "symbols": symbols, "timeframes": {}}
    fmt_d = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).strftime("%d.%m.%Y")  # noqa: E731

    print(f"{len(vs)} Varianten × {len(tfs)} Timeframes · {len(symbols)} Coins · "
          f"Prüfzeitraum ab {fmt_d(test_from)} (wird erst am Ende gerechnet)\n")

    with CandleStore(args.db) as store:
        for tf in tfs:
            frames = {s: add_all(df) for s in symbols if len(df := store.load_df(s, tf)) > 400}
            if not frames:
                print(f"{tf}: keine Daten")
                continue
            sigs = index_signals(frames, tf)
            start = min(df.index[0] for df in frames.values())
            funding = load_schedules(args.db, list(frames), int(start.value // 1_000_000), now + DAY)
            print(f"=== {tf} · Entwicklung {start:%d.%m.%Y} – {fmt_d(test_from - 1)} · "
                  f"{len(frames)} Coins")
            t0 = time.time()
            train = []
            for v in vs:
                r = run_backtest(frames, tf, v, signals=sigs, trade_until=test_from - 1,
                                 funding=funding)
                train.append((v, r.metrics))
            ranked = sorted(train, key=lambda x: score(x[1]), reverse=True)
            ok = [x for x in ranked if eligible(x[1], tf)]
            print(f"  ({time.time() - t0:.0f}s) zugelassen: {len(ok)} von {len(vs)} · Top 5:")
            print(HEADER)
            for v, m in ranked[:5]:
                print(line(("✓ " if eligible(m, tf) else "  ") + v.label, m))

            finalists = []
            if ok:
                finalists.append(("Gewinner Entwicklung", ok[0][0]))
            finalists.append(("Bisher (Trendfolge)", STRATEGIES["trend"]))
            print(f"\n  PRÜFUNG {fmt_d(test_from)} – heute (nie gesehene Daten):")
            print(HEADER)
            tf_report = {"train": [{"label": v.label, "metrics": clean(m)} for v, m in ranked],
                         "test": []}
            for role, v in finalists:
                tr = next((m for vv, m in train if vv is v), None)
                if tr is None:
                    tr = run_backtest(frames, tf, v, signals=sigs, trade_until=test_from - 1,
                                      funding=funding).metrics
                te = run_backtest(frames, tf, v, signals=sigs, trade_from=test_from, funding=funding)
                print(line(f"{role}: {v.label}", te.metrics))
                step = max(1, len(te.equity) // 400)
                tf_report["test"].append({
                    "role": role, "label": v.label, "train": clean(tr), "test": clean(te.metrics),
                    "equity": {"x": [t.isoformat() for t in te.equity.index[::step]],
                               "y": [round(float(x), 2) for x in te.equity.values[::step]]},
                })
            report["timeframes"][tf] = tf_report
            print()

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(args.out) / stamp
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
    (Path(args.out) / "latest.txt").write_text(stamp)
    print(f"Bericht: {out}/report.json")
    print("W+ = Anteil Wochen im Plus. Vergangene Ergebnisse sind keine Garantie. "
          "Keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
