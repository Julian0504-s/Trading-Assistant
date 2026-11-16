"""Backtest: eine Strategie auf historischen Kerzen mehrerer Symbole (ein Timeframe).

Ablauf pro Kerze t (alle Symbole teilen sich ein Konto):
  1. Offene Aufträge aus Kerze t-1 werden zum ERÖFFNUNGSKURS von t ausgeführt.
  2. Offene Positionen werden gegen Hoch/Tief von t auf Stop/Ziel geprüft.
  3. Signale am SCHLUSS von t werden ausgewertet -> Auftrag für Kerze t+1.
So fließt nie Wissen aus der Zukunft in eine Entscheidung ein.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from risk import RiskConfig, RiskManager
from signals.models import Signal
from signals.rules import evaluate_candles

from .account import Order, SimAccount, Trade
from .base import Strategy


@dataclass
class BacktestResult:
    strategy: str
    timeframe: str
    symbols: list[str]
    start: int
    end: int
    start_equity: float
    end_equity: float
    trades: list[Trade]
    orders: list[Order]
    equity: pd.Series            # Kontostand (mark-to-market) je Kerze
    metrics: dict = field(default_factory=dict)


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.value // 1_000_000)


def run_backtest(
    frames: dict[str, pd.DataFrame],
    timeframe: str,
    strategy: Strategy,
    *,
    start_equity: float = 1000.0,
    risk: RiskConfig | None = None,
    warmup: int = 200,
) -> BacktestResult:
    """`frames`: pro Symbol ein DataFrame mit Indikatoren (indicators.add_all).
    `warmup`: so viele Kerzen am Anfang nicht handeln (EMA 200 braucht Vorlauf)."""
    acc = SimAccount(start_equity, mode="backtest", risk=RiskManager(risk or RiskConfig()))

    # Signale vorab berechnen (die Regeln schauen nur zurück) und nach Zeit gruppieren
    sig_at: dict[str, dict[int, list[Signal]]] = {}
    for sym, df in frames.items():
        by_time: dict[int, list[Signal]] = defaultdict(list)
        for s in evaluate_candles(df, sym, timeframe):
            by_time[s.candle_time].append(s)
        sig_at[sym] = by_time

    timeline = sorted(set().union(*(df.index for df in frames.values())))
    trade_from = timeline[warmup] if len(timeline) > warmup else None
    pending: dict[str, tuple[Signal, float]] = {}  # Symbol -> (Signal, ATR)
    last_close: dict[str, float] = {}
    eq_points: list[tuple[pd.Timestamp, float]] = []

    for ts in timeline:
        t = _ms(ts)
        for sym, df in frames.items():
            if ts not in df.index:
                continue
            row = df.loc[ts]
            o, h, low, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])

            # 1. Auftrag aus der Vorkerze zum Eröffnungskurs ausführen
            order = pending.pop(sym, None)
            if order is not None:
                sig, atr = order
                pos = acc.positions.get((sym, timeframe))
                if pos is not None and pos.direction != sig.direction:
                    acc.close(pos, o, t, f"Gegensignal: {sig.reason}")
                if (sym, timeframe) not in acc.positions:
                    stop, target = strategy.stop_and_target(sig.direction, o, atr)
                    acc.open(symbol=sym, timeframe=timeframe, strategy=strategy.name,
                             direction=sig.direction, price=o, time=t, stop=stop,
                             target=target, reason=sig.reason, prices=last_close)

            # 2. Stop/Ziel innerhalb der Kerze
            pos = acc.positions.get((sym, timeframe))
            if pos is not None:
                acc.check_exit(pos, o, h, low, t)
            last_close[sym] = c

            # 3. Signale am Kerzenschluss -> Auftrag für die nächste Kerze
            if trade_from is not None and ts >= trade_from:
                chosen = strategy.pick(sig_at[sym].get(t, []), row)
                atr = row.get("atr_14")
                if chosen is not None and atr is not None and not math.isnan(atr) and atr > 0:
                    pos = acc.positions.get((sym, timeframe))
                    if pos is None or pos.direction != chosen.direction:
                        pending[sym] = (chosen, float(atr))
        eq_points.append((ts, acc.equity(last_close)))

    # Offene Positionen zum letzten Schlusskurs schließen
    end_t = _ms(timeline[-1]) if timeline else 0
    for pos in list(acc.positions.values()):
        acc.close(pos, last_close[pos.symbol], end_t, "Backtest-Ende")
    if eq_points:
        eq_points[-1] = (eq_points[-1][0], acc.cash)

    equity = pd.Series([v for _, v in eq_points], index=[k for k, _ in eq_points], dtype="float64")
    result = BacktestResult(
        strategy=strategy.name, timeframe=timeframe, symbols=list(frames),
        start=_ms(trade_from) if trade_from is not None else 0, end=end_t,
        start_equity=start_equity, end_equity=acc.cash,
        trades=acc.trades, orders=acc.orders, equity=equity,
    )
    result.metrics = compute_metrics(result)
    return result


def compute_metrics(r: BacktestResult) -> dict:
    trades = r.trades
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    gross_win = sum(t.pnl for t in wins)
    gross_loss = -sum(t.pnl for t in losses)
    eq = r.equity
    if len(eq):
        dd = (eq / eq.cummax() - 1).min() * 100
    else:
        dd = 0.0
    days = (r.end - r.start) / 86_400_000 if r.end > r.start else 0
    return {
        "Trades": len(trades),
        "Trefferquote %": 100 * len(wins) / len(trades) if trades else math.nan,
        "Rendite %": (r.end_equity / r.start_equity - 1) * 100,
        "Endkapital": r.end_equity,
        "Max. Drawdown %": dd,
        "Profitfaktor": gross_win / gross_loss if gross_loss > 0 else math.inf if gross_win else math.nan,
        "Ø R pro Trade": sum(t.r_multiple for t in trades) / len(trades) if trades else math.nan,
        "Gebühren": sum(t.fees for t in trades),
        "Tage": days,
    }
