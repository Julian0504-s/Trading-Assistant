"""Simuliertes Konto: Positionen eröffnen/schließen, Gebühren, Slippage, Order-Log.

Es werden NIE echte Orders gesendet – dieses Modul kennt keine API.
Wird vom Backtest und vom Paper Trading gleichermaßen benutzt.
"""

from __future__ import annotations

import csv
import itertools
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from risk import RiskManager, RiskViolation

FEE_RATE = 0.0006   # 0,06 % pro Seite (Taker-Gebühr, konservativ)
SLIPPAGE = 0.0002   # 0,02 % ungünstigere Ausführung


@dataclass
class Position:
    id: int
    symbol: str
    timeframe: str
    strategy: str
    direction: str          # long | short
    entry_time: int         # ms
    entry_price: float
    qty: float
    stop: float
    target: float
    reason: str
    entry_fee: float
    trail_atr: float | None = None   # Trailing-Stop-Abstand in ATR (None = aus)
    initial_stop: float | None = None
    units: int = 1                    # 1 + Anzahl Nachkäufe
    risk_amount: float | None = None  # ursprünglich eingesetztes Risiko in USDT (für R)
    last_add_price: float | None = None

    def __post_init__(self) -> None:
        if self.initial_stop is None:
            self.initial_stop = self.stop
        if self.risk_amount is None:
            self.risk_amount = abs(self.entry_price - self.initial_stop) * self.qty
        if self.last_add_price is None:
            self.last_add_price = self.entry_price

    def risk_free(self) -> bool:
        """True, wenn der Stop mindestens beim Ø-Einstieg liegt (kein Verlustrisiko mehr)."""
        if self.direction == "long":
            return self.stop >= self.entry_price
        return self.stop <= self.entry_price

    @property
    def notional(self) -> float:
        return self.qty * self.entry_price

    def unrealized(self, price: float) -> float:
        sign = 1 if self.direction == "long" else -1
        return sign * (price - self.entry_price) * self.qty


@dataclass
class Order:
    """Jede (simulierte) Order – mit Grund, Preis, Größe und Zeit (CSV für die Steuer)."""

    time: int
    mode: str               # backtest | paper
    strategy: str
    symbol: str
    timeframe: str
    action: str             # Eröffnung | Schließung
    side: str               # Kauf | Verkauf
    price: float
    qty: float
    notional: float
    fee: float
    pnl: float | None       # bei Schließung: Ergebnis nach Gebühren
    reason: str
    position_id: int

    @property
    def time_utc(self) -> str:
        return datetime.fromtimestamp(self.time / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M:%S")


@dataclass
class Trade:
    """Abgeschlossener Trade (Eröffnung + Schließung)."""

    position_id: int
    symbol: str
    timeframe: str
    strategy: str
    direction: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    qty: float
    pnl: float              # nach Gebühren
    fees: float
    r_multiple: float       # Ergebnis in Vielfachen des Anfangsrisikos
    entry_reason: str
    exit_reason: str


@dataclass
class SimAccount:
    start_equity: float
    mode: str = "backtest"
    risk: RiskManager = field(default_factory=RiskManager)
    fee_rate: float = FEE_RATE
    slippage: float = SLIPPAGE
    cash: float = 0.0       # realisierter Kontostand
    positions: dict[tuple[str, str], Position] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    next_id: int = 1

    def __post_init__(self) -> None:
        if self.cash == 0.0:
            self.cash = self.start_equity
        self._ids = itertools.count(self.next_id)

    # ------------------------------------------------------------ Kennzahlen
    def equity(self, prices: dict[str, float] | None = None) -> float:
        """Kontostand inkl. offener Positionen (zu `prices` bewertet, sonst Einstieg)."""
        prices = prices or {}
        return self.cash + sum(p.unrealized(prices.get(p.symbol, p.entry_price))
                               for p in self.positions.values())

    def open_notional(self) -> float:
        return sum(p.notional for p in self.positions.values())

    # ------------------------------------------------------------- Aktionen
    def open(self, *, symbol: str, timeframe: str, strategy: str, direction: str, price: float,
             time: int, stop: float, target: float, reason: str,
             prices: dict[str, float] | None = None,
             trail_atr: float | None = None) -> Position | None:
        """Eröffnet eine Position. Gibt None zurück, wenn der Risk Manager ablehnt."""
        if (symbol, timeframe) in self.positions:
            return None
        equity = self.equity(prices)
        if self.risk.daily_limit_hit(time, equity):
            return None
        fill = price * (1 + self.slippage if direction == "long" else 1 - self.slippage)
        # Stop/Ziel relativ zum tatsächlichen Füllpreis verschieben
        stop, target = stop + (fill - price), target + (fill - price)
        try:
            qty = self.risk.size_position(equity, fill, stop, self.open_notional())
        except RiskViolation:
            return None
        fee = qty * fill * self.fee_rate
        self.cash -= fee
        pid = next(self._ids)
        self.next_id = pid + 1
        pos = Position(pid, symbol, timeframe, strategy, direction, time, fill, qty,
                       stop, target, reason, fee, trail_atr)
        self.positions[(symbol, timeframe)] = pos
        self._log(pos, time, "Eröffnung", "Kauf" if direction == "long" else "Verkauf",
                  fill, fee, None, reason)
        return pos

    def close(self, pos: Position, price: float, time: int, reason: str) -> Trade:
        fill = price * (1 - self.slippage if pos.direction == "long" else 1 + self.slippage)
        fee = pos.qty * fill * self.fee_rate
        gross = pos.unrealized(fill)
        pnl = gross - fee - pos.entry_fee
        self.cash += gross - fee
        del self.positions[(pos.symbol, pos.timeframe)]
        risk = pos.risk_amount
        trade = Trade(pos.id, pos.symbol, pos.timeframe, pos.strategy, pos.direction,
                      pos.entry_time, time, pos.entry_price, fill, pos.qty, pnl,
                      fee + pos.entry_fee, pnl / risk if risk else 0.0, pos.reason, reason)
        self.trades.append(trade)
        self._log(pos, time, "Schließung", "Verkauf" if pos.direction == "long" else "Kauf",
                  fill, fee, pnl, reason)
        return trade

    def add_to(self, pos: Position, price: float, time: int, reason: str,
               prices: dict[str, float] | None = None) -> bool:
        """Nachkauf (Pyramiding): nur wenn die bestehende Position durch den Stop bereits
        ohne Verlustrisiko ist. Der Nachkauf riskiert bis zum aktuellen Stop höchstens
        `risk_per_trade` – das offene Risiko der Position bleibt so <= 1 %."""
        fill = price * (1 + self.slippage if pos.direction == "long" else 1 - self.slippage)
        if not pos.risk_free():
            return False
        sign = 1 if pos.direction == "long" else -1
        if sign * (fill - pos.stop) <= 0:
            return False
        equity = self.equity(prices)
        if self.risk.daily_limit_hit(time, equity):
            return False
        try:
            qty = self.risk.size_position(equity, fill, pos.stop, self.open_notional())
        except RiskViolation:
            return False
        fee = qty * fill * self.fee_rate
        self.cash -= fee
        pos.entry_price = (pos.entry_price * pos.qty + fill * qty) / (pos.qty + qty)
        pos.qty += qty
        pos.entry_fee += fee
        pos.units += 1
        pos.last_add_price = fill
        pos.risk_amount += abs(fill - pos.stop) * qty
        self.orders.append(Order(time, self.mode, pos.strategy, pos.symbol, pos.timeframe,
                                 "Aufstockung", "Kauf" if pos.direction == "long" else "Verkauf",
                                 fill, qty, qty * fill, fee, None, reason, pos.id))
        return True

    def check_exit(self, pos: Position, o: float, h: float, low: float, time: int,
                   atr: float | None = None) -> Trade | None:
        """Prüft eine Kerze auf Stop/Ziel. Berührt eine Kerze beides, zählt
        vorsichtshalber der Stop. Kurslücken werden zum Eröffnungskurs ausgeführt.
        Ohne Ausstieg wird danach der Trailing-Stop nachgezogen (gilt ab der nächsten Kerze)."""
        trade = self._exit(pos, o, h, low, time)
        if trade is None and pos.trail_atr and atr and atr > 0:
            if pos.direction == "long":
                pos.stop = max(pos.stop, h - pos.trail_atr * atr)
            else:
                pos.stop = min(pos.stop, low + pos.trail_atr * atr)
        return trade

    def _exit(self, pos: Position, o: float, h: float, low: float, time: int) -> Trade | None:
        if pos.direction == "long":
            if o <= pos.stop:
                return self.close(pos, o, time, "Stop-Loss (Kurslücke)")
            if low <= pos.stop:
                return self.close(pos, pos.stop, time, self._stop_name(pos))
            if o >= pos.target:
                return self.close(pos, o, time, "Kursziel (Kurslücke)")
            if h >= pos.target:
                return self.close(pos, pos.target, time, "Kursziel")
        else:
            if o >= pos.stop:
                return self.close(pos, o, time, "Stop-Loss (Kurslücke)")
            if h >= pos.stop:
                return self.close(pos, pos.stop, time, self._stop_name(pos))
            if o <= pos.target:
                return self.close(pos, o, time, "Kursziel (Kurslücke)")
            if low <= pos.target:
                return self.close(pos, pos.target, time, "Kursziel")
        return None

    @staticmethod
    def _stop_name(pos: Position) -> str:
        return "Trailing-Stop" if pos.stop != pos.initial_stop else "Stop-Loss"

    def _log(self, pos: Position, time: int, action: str, side: str, price: float, fee: float,
             pnl: float | None, reason: str) -> None:
        self.orders.append(Order(time, self.mode, pos.strategy, pos.symbol, pos.timeframe,
                                 action, side, price, pos.qty, pos.qty * price, fee, pnl,
                                 reason, pos.id))


def export_orders_csv(orders: list[Order], path: str | Path) -> Path:
    """Order-Log als CSV (Semikolon, deutsches Dezimalkomma) – z. B. für die Steuer."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    cols = ["Zeit (UTC)", "Modus", "Strategie", "Symbol", "Timeframe", "Aktion", "Seite",
            "Preis", "Menge", "Volumen USDT", "Gebühr USDT", "Ergebnis USDT", "Grund", "Position"]

    def de(x: float | None, digits: int = 8) -> str:
        return "" if x is None else f"{x:.{digits}f}".rstrip("0").rstrip(".").replace(".", ",")

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cols)
        for o in orders:
            w.writerow([o.time_utc, o.mode, o.strategy, o.symbol, o.timeframe, o.action, o.side,
                        de(o.price), de(o.qty), de(o.notional, 4), de(o.fee, 6),
                        de(o.pnl, 4), o.reason, o.position_id])
    return path


def order_dict(o: Order) -> dict:
    return asdict(o)
