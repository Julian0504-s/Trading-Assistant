"""Backtest: eine Strategie auf historischen Kerzen mehrerer Symbole (ein Timeframe).

Ablauf pro Kerze t (alle Symbole teilen sich ein Konto):
  1. Offene Aufträge aus Kerze t-1 werden zum ERÖFFNUNGSKURS von t ausgeführt.
  2. Offene Positionen werden gegen Hoch/Tief von t auf Stop/Ziel geprüft,
     danach wird ein Trailing-Stop nachgezogen (gilt ab t+1).
  3. Signale am SCHLUSS von t werden ausgewertet -> Auftrag für Kerze t+1.
So fließt nie Wissen aus der Zukunft in eine Entscheidung ein.

Mit `trade_from`/`trade_until` (ms) lässt sich ein Zeitfenster festlegen, z. B.
um Strategien auf einem Zeitraum zu entwickeln und auf einem anderen zu prüfen.
Die Indikatoren dürfen dabei auf Daten VOR dem Fenster aufbauen (Aufwärmphase).
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from risk import RiskConfig, RiskManager
from signals.models import Signal
from signals.rules import evaluate_candles

from .account import Order, SimAccount, Trade
from .base import Strategy

SignalIndex = dict[str, dict[int, list[Signal]]]


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
    equity: pd.Series            # Kontostand (mark-to-market) je Kerze im Handelsfenster
    metrics: dict = field(default_factory=dict)


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.value // 1_000_000)


def index_signals(frames: dict[str, pd.DataFrame], timeframe: str) -> SignalIndex:
    """Alle Signale vorab berechnen (die Regeln schauen nur zurück), nach Zeit gruppiert.
    Lohnt sich, wenn viele Strategie-Varianten auf denselben Daten laufen."""
    out: SignalIndex = {}
    for sym, df in frames.items():
        by_time: dict[int, list[Signal]] = defaultdict(list)
        for s in evaluate_candles(df, sym, timeframe):
            by_time[s.candle_time].append(s)
        out[sym] = dict(by_time)
    return out


class _Arrays:
    """Spalten eines Symbols als NumPy-Arrays (schneller als Zeilenzugriffe in pandas)."""

    def __init__(self, df: pd.DataFrame):
        self.pos = {_ms(t): i for i, t in enumerate(df.index)}
        col = lambda c: df[c].to_numpy(dtype="float64") if c in df else np.full(len(df), np.nan)  # noqa: E731
        self.o, self.h, self.l, self.c = col("open"), col("high"), col("low"), col("close")
        self.atr, self.ema200, self.adx = col("atr_14"), col("ema_200"), col("adx_14")

    def row(self, i: int) -> dict:
        return {"close": self.c[i], "ema_200": self.ema200[i], "adx_14": self.adx[i]}


def run_backtest(
    frames: dict[str, pd.DataFrame],
    timeframe: str,
    strategy: Strategy,
    *,
    start_equity: float = 1000.0,
    risk: RiskConfig | None = None,
    warmup: int = 200,
    signals: SignalIndex | None = None,
    trade_from: int | None = None,
    trade_until: int | None = None,
) -> BacktestResult:
    """`frames`: pro Symbol ein DataFrame mit Indikatoren (indicators.add_all).
    `warmup`: so viele Kerzen am Anfang nie handeln (EMA 200 braucht Vorlauf).
    `signals`: vorab berechnete Signale (index_signals), sonst werden sie hier berechnet.
    `trade_from`/`trade_until`: nur in diesem Zeitfenster (ms) handeln und bewerten."""
    acc = SimAccount(start_equity, mode="backtest", risk=RiskManager(risk or RiskConfig()))
    sig_at = signals if signals is not None else index_signals(frames, timeframe)
    arr = {sym: _Arrays(df) for sym, df in frames.items()}

    timeline = sorted(set().union(*(arr[s].pos for s in arr)))
    if len(timeline) > warmup:
        first = timeline[warmup]
        trade_from = max(first, trade_from) if trade_from is not None else first
    else:
        trade_from = None
    trade_until = trade_until if trade_until is not None else (timeline[-1] if timeline else 0)
    exits = strategy.exits

    pending: dict[str, tuple[Signal, float]] = {}   # Symbol -> (Signal, ATR)
    last_close: dict[str, float] = {}
    eq_t: list[int] = []
    eq_v: list[float] = []
    end_t = trade_from or 0

    for t in timeline:
        if trade_from is None or t > trade_until:
            if trade_from is not None and t > trade_until:
                break
            continue
        in_window = t >= trade_from
        for sym, a in arr.items():
            i = a.pos.get(t)
            if i is None:
                continue
            o, h, low, c = a.o[i], a.h[i], a.l[i], a.c[i]

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
                             target=target, reason=sig.reason, prices=last_close,
                             trail_atr=exits.trail_atr)

            # 2. Stop/Ziel innerhalb der Kerze, danach Trailing-Stop nachziehen
            pos = acc.positions.get((sym, timeframe))
            if pos is not None:
                acc.check_exit(pos, o, h, low, t, a.atr[i])
            last_close[sym] = c

            # 3. Signale am Kerzenschluss -> Auftrag für die nächste Kerze
            if in_window and t < trade_until:
                sigs = sig_at.get(sym, {}).get(t)
                if sigs:
                    chosen = strategy.pick(sigs, a.row(i))
                    atr = a.atr[i]
                    if chosen is not None and not math.isnan(atr) and atr > 0:
                        pos = acc.positions.get((sym, timeframe))
                        if pos is None or pos.direction != chosen.direction:
                            pending[sym] = (chosen, float(atr))
        if in_window:
            eq_t.append(t)
            eq_v.append(acc.equity(last_close))
            end_t = t

    # Offene Positionen zum letzten Schlusskurs schließen
    for pos in list(acc.positions.values()):
        acc.close(pos, last_close[pos.symbol], end_t, "Backtest-Ende")
    if eq_v:
        eq_v[-1] = acc.cash

    equity = pd.Series(eq_v, index=pd.to_datetime(eq_t, unit="ms", utc=True), dtype="float64")
    result = BacktestResult(
        strategy=strategy.name, timeframe=timeframe, symbols=list(frames),
        start=trade_from or 0, end=end_t, start_equity=start_equity, end_equity=acc.cash,
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
    dd = (eq / eq.cummax() - 1).min() * 100 if len(eq) else 0.0
    days = (r.end - r.start) / 86_400_000 if r.end > r.start else 0
    ret = r.end_equity / r.start_equity - 1
    cagr = ((1 + ret) ** (365 / days) - 1) * 100 if days > 0 and ret > -1 else math.nan
    weekly = eq.resample("W").last().pct_change().dropna() * 100 if len(eq) > 1 else pd.Series()
    return {
        "Trades": len(trades),
        "Trefferquote %": 100 * len(wins) / len(trades) if trades else math.nan,
        "Rendite %": ret * 100,
        "Rendite p.a. %": cagr,
        "Endkapital": r.end_equity,
        "Max. Drawdown %": dd,
        "Profitfaktor": gross_win / gross_loss if gross_loss > 0 else math.inf if gross_win else math.nan,
        "Ø R pro Trade": sum(t.r_multiple for t in trades) / len(trades) if trades else math.nan,
        "Gebühren": sum(t.fees for t in trades),
        "Tage": days,
        "Wochen im Plus %": 100 * (weekly > 0).mean() if len(weekly) else math.nan,
        "Beste Woche %": weekly.max() if len(weekly) else math.nan,
        "Schlechteste Woche %": weekly.min() if len(weekly) else math.nan,
    }
