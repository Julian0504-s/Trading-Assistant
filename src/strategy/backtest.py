"""Backtest: Strategien auf historischen Kerzen, ein gemeinsames Konto.

Ereignisbasiert – jede Kerze erzeugt zwei Ereignisse:
  ERÖFFNUNG (open_time):   Aufträge aus der Vorkerze werden zum Eröffnungskurs
                           ausgeführt (Einstieg, Gegensignal, Nachkauf).
  SCHLUSS (open_time+TF):  Stop/Ziel werden gegen Hoch/Tief geprüft, ein
                           Trailing-Stop nachgezogen, dann die Signale am
                           Kerzenschluss ausgewertet -> Auftrag für die nächste Kerze.
Kontostand und Positionsgrößen kennen nur Schlusskurse, die zum jeweiligen
Zeitpunkt feststehen – auch wenn mehrere Timeframes (z. B. 1d und 4h) in einem
Konto laufen. So fließt nie Wissen aus der Zukunft in eine Entscheidung ein.

Mit `trade_from`/`trade_until` (ms, Kerzen-Eröffnung) lässt sich ein Zeitfenster
festlegen. Die Indikatoren dürfen auf Daten VOR dem Fenster aufbauen.
"""

from __future__ import annotations

import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bitunix.client import INTERVAL_MS
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
    equity: pd.Series            # Kontostand (zu bekannten Schlusskursen) im Handelsfenster
    funding: float = 0.0         # gezahltes Funding (+) bzw. erhaltenes (−) in USDT
    exposure: pd.Series | None = None  # offenes Positionsvolumen / Kontostand je Zeitpunkt
    long_exposure: pd.Series | None = None  # nur Long-Volumen / Kontostand
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


@dataclass
class Stream:
    """Ein Symbol auf einem Timeframe mit seiner Strategie."""

    symbol: str
    timeframe: str
    df: pd.DataFrame
    strategy: Strategy
    signals: dict[int, list[Signal]]


class _Arrays:
    """Spalten als NumPy-Arrays (schneller als Zeilenzugriffe in pandas)."""

    def __init__(self, df: pd.DataFrame):
        self.t = np.array([_ms(t) for t in df.index], dtype="int64")
        col = lambda c: df[c].to_numpy(dtype="float64") if c in df else np.full(len(df), np.nan)  # noqa: E731
        self.o, self.h, self.l, self.c = col("open"), col("high"), col("low"), col("close")
        self.atr, self.ema200, self.adx = col("atr_14"), col("ema_200"), col("adx_14")
        self.btc_up = (df["btc_up"].to_numpy(dtype="float64") if "btc_up" in df
                       else np.full(len(df), np.nan))
        self.htf_up = df["htf_up"].to_numpy(dtype=bool) if "htf_up" in df else None
        self.htf_down = df["htf_down"].to_numpy(dtype=bool) if "htf_down" in df else None

    def row(self, i: int) -> dict:
        b = self.btc_up[i]
        return {"close": self.c[i], "ema_200": self.ema200[i], "adx_14": self.adx[i],
                "btc_up": None if math.isnan(b) else bool(b),
                "htf_up": None if self.htf_up is None else bool(self.htf_up[i]),
                "htf_down": None if self.htf_down is None else bool(self.htf_down[i])}


def run_streams(
    streams: list[Stream],
    *,
    start_equity: float = 1000.0,
    risk: RiskConfig | None = None,
    warmup: int = 200,
    trade_from: int | None = None,
    trade_until: int | None = None,
    name: str = "",
    timeframe_label: str = "",
    funding: dict[str, pd.DataFrame] | None = None,
    fee_rate: float | None = None,
    maker_fee: float | None = None,
) -> BacktestResult:
    """Mehrere Streams (Symbol × Timeframe × Strategie) in einem gemeinsamen Konto.
    `funding`: je Symbol eine Tabelle (time, rate, mark) – siehe data.funding.funding_schedule.
    Ohne `funding` wird kein Funding abgerechnet."""
    acc = SimAccount(start_equity, mode="backtest", risk=RiskManager(risk or RiskConfig()),
                     maker_fee=maker_fee)
    if fee_rate is not None:
        acc.fee_rate = fee_rate
    arrs = [_Arrays(s.df) for s in streams]
    steps = [INTERVAL_MS[s.timeframe] for s in streams]

    # Handelsbeginn: frühestens nach `warmup` Kerzen des jeweiligen Streams
    starts = [a.t[warmup] if len(a.t) > warmup else None for a in arrs]
    valid = [x for x in starts if x is not None]
    if not valid:
        trade_from = None
    else:
        earliest = min(valid)
        trade_from = max(earliest, trade_from) if trade_from is not None else earliest
    if trade_until is None:
        trade_until = max((int(a.t[-1]) for a in arrs if len(a.t)), default=0)

    # Ereignisse: (Zeit, Art, Stream, Index). Art 0 = Kerzenschluss, 1 = Funding,
    # 2 = Eröffnung – zum selben Zeitpunkt in dieser Reihenfolge: Eine Position zahlt
    # Funding, wenn sie zur Abrechnung offen ist; neu eröffnete zahlen erst ab der nächsten.
    events: list[tuple[int, int, int, int]] = []
    for k, a in enumerate(arrs):
        if trade_from is None:
            break
        first = max(int(np.searchsorted(a.t, trade_from)), warmup)
        last = int(np.searchsorted(a.t, trade_until, side="right"))
        for i in range(first, last):
            events.append((int(a.t[i]), 2, k, i))
            events.append((int(a.t[i]) + steps[k], 0, k, i))
    fund_syms: list[tuple[str, np.ndarray, np.ndarray, np.ndarray]] = []
    if funding and trade_from is not None:
        for sym in sorted({s.symbol for s in streams}):
            sch = funding.get(sym)
            if sch is None or sch.empty:
                continue
            sch = sch[(sch["time"] > trade_from) & (sch["time"] <= trade_until + max(steps))]
            j = len(fund_syms)
            fund_syms.append((sym, sch["time"].to_numpy(), sch["rate"].to_numpy(),
                              sch["mark"].to_numpy(dtype="float64")))
            events.extend((int(t), 1, j, n) for n, t in enumerate(fund_syms[j][1]))
    heapq.heapify(events)

    pending: dict[int, list[tuple[str, Signal | None, float]]] = defaultdict(list)
    last_close: dict[str, float] = {}
    eq_t: list[int] = []
    eq_v: list[float] = []
    ex_v: list[float] = []
    lx_v: list[float] = []

    def snapshot(t: int) -> None:
        e = acc.equity(last_close)
        eq_t.append(t)
        eq_v.append(e)
        price = lambda p: last_close.get(p.symbol, p.entry_price)  # noqa: E731
        ex_v.append(sum(p.qty * price(p) for p in acc.positions.values()) / e if e > 0 else 0.0)
        lx_v.append(sum(p.qty * price(p) for p in acc.positions.values()
                        if p.direction == "long") / e if e > 0 else 0.0)
    end_t = trade_from or 0

    while events:
        t, kind, k, i = heapq.heappop(events)
        if kind == 1:  # ---- FUNDING
            sym, _, rates, marks = fund_syms[k]
            if any(p.symbol == sym for p in acc.positions.values()):
                mark = marks[i] if not math.isnan(marks[i]) else last_close.get(sym)
                if mark:
                    acc.apply_funding(sym, float(rates[i]), float(mark), t)
            continue
        s, a, strat = streams[k], arrs[k], streams[k].strategy
        key = (s.symbol, s.timeframe)
        if kind == 2:  # ---- ERÖFFNUNG
            for action, sig, atr in pending.pop(k, []):
                o = a.o[i]
                pos = acc.positions.get(key)
                if action == "add":
                    if pos is not None:
                        acc.add_to(pos, o, t, "Nachkauf: Trend läuft weiter", prices=last_close)
                    continue
                if pos is not None and pos.direction != sig.direction:
                    acc.close(pos, o, t, f"Gegensignal: {sig.reason}")
                if key not in acc.positions:
                    stop, target = strat.stop_and_target(sig.direction, o, atr)
                    acc.open(symbol=s.symbol, timeframe=s.timeframe, strategy=strat.name,
                             direction=sig.direction, price=o, time=t, stop=stop,
                             target=target, reason=sig.reason, prices=last_close,
                             trail_atr=strat.exits.trail_atr)
            continue

        # ---- SCHLUSS
        pos = acc.positions.get(key)
        atr = a.atr[i]
        if pos is not None:
            acc.check_exit(pos, a.o[i], a.h[i], a.l[i], t, atr)
        last_close[s.symbol] = a.c[i]
        if strat.flat_at_day_end and t % 86_400_000 == 0:
            pos = acc.positions.get(key)
            if pos is not None:
                acc.close(pos, a.c[i], t, "Tagesende")
            pending.pop(k, None)
            snapshot(t)
            end_t = t
            continue  # keine neuen Einstiege über den Tageswechsel
        if a.t[i] < trade_until and not math.isnan(atr) and atr > 0:
            pos = acc.positions.get(key)
            sigs = s.signals.get(int(a.t[i]))
            chosen = strat.pick(sigs, a.row(i)) if sigs else None
            if chosen is not None and (pos is None or pos.direction != chosen.direction):
                pending[k].append(("open", chosen, float(atr)))
            elif (pos is not None and strat.pyramid_max and pos.units <= strat.pyramid_max
                  and pos.risk_free()):
                sign = 1 if pos.direction == "long" else -1
                if sign * (a.c[i] - pos.last_add_price) >= strat.pyramid_step_atr * atr:
                    pending[k].append(("add", None, float(atr)))
        snapshot(t)
        end_t = t

    # Offene Positionen zum letzten bekannten Schlusskurs schließen
    for pos in list(acc.positions.values()):
        acc.close(pos, last_close[pos.symbol], end_t, "Backtest-Ende")
    if eq_v:
        eq_v[-1] = acc.cash
    # pro Zeitpunkt nur den letzten Stand behalten
    equity = pd.Series(eq_v, index=pd.to_datetime(eq_t, unit="ms", utc=True), dtype="float64")
    keep = ~equity.index.duplicated(keep="last")
    equity = equity[keep]
    exposure = pd.Series(ex_v, index=pd.to_datetime(eq_t, unit="ms", utc=True))[keep]
    long_exposure = pd.Series(lx_v, index=pd.to_datetime(eq_t, unit="ms", utc=True))[keep]

    result = BacktestResult(
        strategy=name or (streams[0].strategy.name if streams else ""),
        timeframe=timeframe_label or "+".join(sorted({s.timeframe for s in streams})),
        symbols=sorted({s.symbol for s in streams}),
        start=trade_from or 0, end=end_t, start_equity=start_equity, end_equity=acc.cash,
        trades=acc.trades, orders=acc.orders, equity=equity, funding=-acc.funding_total,
        exposure=exposure, long_exposure=long_exposure,
    )
    result.metrics = compute_metrics(result)
    return result


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
    funding: dict[str, pd.DataFrame] | None = None,
) -> BacktestResult:
    """Eine Strategie auf einem Timeframe über mehrere Symbole.
    `frames`: pro Symbol ein DataFrame mit Indikatoren (indicators.add_all).
    `signals`: vorab berechnete Signale (index_signals), sonst werden sie hier berechnet."""
    sig_at = signals if signals is not None else index_signals(frames, timeframe)
    streams = [Stream(sym, timeframe, df, strategy, sig_at.get(sym, {}))
               for sym, df in frames.items()]
    return run_streams(streams, start_equity=start_equity, risk=risk, warmup=warmup,
                       trade_from=trade_from, trade_until=trade_until, funding=funding)


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
    if len(eq) > 1:
        weekly = pd.concat([pd.Series([r.start_equity], index=[eq.index[0]]), eq])
        weekly = weekly[~weekly.index.duplicated(keep="last")].resample("W").last()
        weekly = weekly.pct_change().dropna() * 100
    else:
        weekly = pd.Series(dtype="float64")
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
        "Funding": r.funding,
        "Tage": days,
        "Wochen im Plus %": 100 * (weekly > 0).mean() if len(weekly) else math.nan,
        "Beste Woche %": weekly.max() if len(weekly) else math.nan,
        "Schlechteste Woche %": weekly.min() if len(weekly) else math.nan,
    }
