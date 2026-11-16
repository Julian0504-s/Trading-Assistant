"""Daytrading-Test: strenge Setups auf 15m/1h, Schluss zum Tagesende.

    uv run python scripts/daytrade.py

Alle Varianten nutzen strenge Filter: Signal in Richtung des Trends auf dem
eigenen Timeframe UND auf 4h UND 1d (jeweils EMA 200), ADX >= 25, alle
Positionen werden um 00:00 UTC geschlossen. Risiko wie immer: max. 1 % pro
Trade, Hebel max. 3x.

Variiert werden: Timeframe (15m, 1h), Mindeststärke (mittel, stark),
Stop/Ziel (1,5/3 oder 1/2 ATR) und Gebühren (Taker 0,06 % oder Limit-Orders
0,02 % für Einstieg und Ziel; Stops und Tagesende immer Taker + Slippage).

Ablauf (vorab festgelegt): Auswahl auf 11/2024–11/2025 (genug Trades,
Profitfaktor >= 1,1, positiv; bestes Verhältnis Jahresrendite / Drawdown),
dann einmalige Prüfung auf dem letzten Jahr – im Vergleich zur 1d-Strategie
(trend_trail) im selben Zeitraum.
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
from strategy.backtest import Stream, index_signals, run_streams
from strategy.regime import add_htf_trend

from common import DEFAULT_DB_PATH, load_dotenv, split_list

UNIVERSE = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT"
DAY = 86_400_000
RULES = frozenset({"ema_cross", "ema200_trend", "bb_breakout", "donchian_breakout"})
TAKER, MAKER = 0.0006, 0.0002


def variants() -> list[tuple[str, Strategy, float | None]]:
    out = []
    for tf, strength, (st, tg), maker in itertools.product(
            ("15m", "1h"), (2, 3), ((1.5, 3.0), (1.0, 2.0)), (False, True)):
        label = (f"{tf} · ab {'stark' if strength == 3 else 'mittel'} · Stop {st:g}/Ziel {tg:g} ATR"
                 f" · {'Limit-Orders' if maker else 'Market-Orders'}")
        strat = Strategy(name=f"day_{tf}_{strength}_{st:g}_{'m' if maker else 't'}", label=label,
                         description=label, rules=RULES, min_strength=strength,
                         with_trend_only=True, htf_filter=True, adx_min=25,
                         flat_at_day_end=True,
                         exits=ExitConfig(stop_atr=st, target_atr=tg))
        out.append((tf, strat, MAKER if maker else None))
    return out


def mar(m: dict) -> float:
    c, d = m["Rendite p.a. %"], m["Max. Drawdown %"]
    return -math.inf if c is None or math.isnan(c) or not d else c / abs(d)


def eligible(m: dict) -> bool:
    return m["Trades"] >= 100 and (m["Profitfaktor"] or 0) >= 1.1 and m["Rendite %"] > 0


def line(label: str, m: dict) -> str:
    def f(v, spec):
        if v is None or (isinstance(v, float) and not math.isfinite(v)):
            return format(float("nan"), spec).replace("nan", "  –")
        return format(v, spec)
    return (f"  {label:<58}{m['Trades']:>6}{f(m['Trefferquote %'], '6.0f')}%"
            f"{f(m['Rendite %'], '+9.1f')}%{f(m['Max. Drawdown %'], '8.1f')}%"
            f"{f(m['Profitfaktor'], '6.2f')}{f(m['Gebühren'], '9.0f')}{f(m['Wochen im Plus %'], '6.0f')}%")


HEADER = (f"  {'Variante':<58}{'Trades':>6}{'Treff':>7}{'Rendite':>10}{'MaxDD':>9}{'PF':>6}"
          f"{'Gebühr':>9}{'W+':>7}")


def clean(m: dict) -> dict:
    return {k: (None if isinstance(v, float) and not math.isfinite(v) else v) for k, v in m.items()}


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Daytrading-Test")
    p.add_argument("--symbols", default=UNIVERSE)
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--out", default="data/research")
    args = p.parse_args()
    symbols = split_list(args.symbols, upper=True)

    now = int(time.time() * 1000) // DAY * DAY
    test_from = now - 365 * DAY
    dev_from = test_from - 365 * DAY
    fmt = lambda ms: datetime.fromtimestamp(ms / 1000, UTC).strftime("%d.%m.%Y")  # noqa: E731

    with CandleStore(args.db) as store:
        higher = {tf: {s: add_all(store.load_df(s, tf)) for s in symbols} for tf in ("4h", "1d")}
        ltf = {}
        for tf in ("15m", "1h"):
            fr = {s: add_all(df) for s in symbols if len(df := store.load_df(s, tf)) > 400}
            ltf[tf] = add_htf_trend(fr, tf, higher)
    sigs = {tf: index_signals(fr, tf) for tf, fr in ltf.items()}
    sigs_1d = index_signals(higher["1d"], "1d")
    funding = load_schedules(args.db, symbols, dev_from - 30 * DAY, now + DAY)
    coverage = {tf: min(df.index[0] for df in fr.values()) for tf, fr in ltf.items()}
    print("Daten ab: " + ", ".join(f"{tf} {ts:%d.%m.%Y}" for tf, ts in coverage.items()))
    print(f"Auswahl {fmt(dev_from)}–{fmt(test_from)} · Prüfung {fmt(test_from)}–heute · "
          f"{len(symbols)} Coins · Funding eingerechnet\n")

    def run(tf, strat, maker, start, end):
        streams = [Stream(s, tf, ltf[tf][s], strat, sigs[tf].get(s, {})) for s in ltf[tf]]
        return run_streams(streams, trade_from=start, trade_until=end, funding=funding,
                           maker_fee=maker, name=strat.name)

    def run_base(start, end):
        base = STRATEGIES["trend_trail"]
        streams = [Stream(s, "1d", higher["1d"][s], base, sigs_1d.get(s, {})) for s in symbols]
        return run_streams(streams, trade_from=start, trade_until=end, funding=funding)

    dev = []
    for tf, strat, maker in variants():
        r = run(tf, strat, maker, dev_from, test_from - 1)
        dev.append((tf, strat, maker, r.metrics))
    ranked = sorted(dev, key=lambda x: mar(x[3]), reverse=True)
    ok = [x for x in ranked if eligible(x[3])]
    print(f"AUSWAHL – zugelassen: {len(ok)} von {len(dev)}. Alle Varianten:")
    print(HEADER)
    for tf, strat, maker, m in ranked:
        print(line(("✓ " if eligible(m) else "  ") + strat.label, m))
    base_dev = run_base(dev_from, test_from - 1).metrics
    print(line("  Zum Vergleich 1d trend_trail", base_dev))

    print(f"\nPRÜFUNG {fmt(test_from)}–heute (nie gesehen):")
    print(HEADER)
    report = {"dev_from": dev_from, "test_from": test_from,
              "dev": [{"label": s.label, "metrics": clean(m)} for _, s, _, m in ranked],
              "base_dev": clean(base_dev)}
    base_test = run_base(test_from, now)
    if ok:
        tf, strat, maker, _ = ok[0]
        te = run(tf, strat, maker, test_from, now)
        print(line("Gewinner Auswahl: " + strat.label, te.metrics))
        report["winner"] = {"label": strat.label, "test": clean(te.metrics)}
        better = (te.metrics["Rendite %"] > base_test.metrics["Rendite %"]
                  and mar(te.metrics) >= mar(base_test.metrics))
    else:
        print("  Keine Variante hat die Auswahl bestanden – nichts zu prüfen.")
        report["winner"] = None
        better = False
    print(line("1d trend_trail (Vergleich)", base_test.metrics))
    report["base_test"] = clean(base_test.metrics)
    report["better"] = better
    print(f"\nUrteil: {'Daytrading BESSER als die 1d-Strategie' if better else 'Daytrading NICHT besser als die 1d-Strategie'}")

    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"daytrade-{stamp}.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False, default=lambda o: o.item()))
    (out / "daytrade-latest.txt").write_text(stamp)
    print(f"Bericht: {out}/daytrade-{stamp}.json · Keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
