"""Paper Trading: die Strategie läuft live mit, Orders werden nur simuliert und geloggt.

Dieselbe Ausführungslogik wie im Backtest (SimAccount). Der Zustand (Konto,
offene Positionen, Order-Log) liegt in SQLite und übersteht Neustarts.
Es werden NIE echte Orders gesendet.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd

from risk import RiskConfig, RiskManager
from signals.rules import evaluate_candles

from .account import Order, Position, SimAccount, Trade
from .base import Strategy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_accounts (
    name          TEXT PRIMARY KEY,
    strategy      TEXT NOT NULL,
    timeframes    TEXT NOT NULL,
    start_equity  REAL NOT NULL,
    cash          REAL NOT NULL,
    next_id       INTEGER NOT NULL,
    created_at    INTEGER NOT NULL,
    progress      TEXT NOT NULL DEFAULT '{}'   -- zuletzt verarbeitete Kerze je Symbol/TF
);
CREATE TABLE IF NOT EXISTS paper_positions (
    account TEXT NOT NULL, id INTEGER NOT NULL, symbol TEXT, timeframe TEXT, strategy TEXT,
    direction TEXT, entry_time INTEGER, entry_price REAL, qty REAL, stop REAL, target REAL,
    reason TEXT, entry_fee REAL, trail_atr REAL, initial_stop REAL, funding REAL DEFAULT 0,
    PRIMARY KEY (account, id)
);
CREATE TABLE IF NOT EXISTS paper_orders (
    account TEXT NOT NULL, time INTEGER, mode TEXT, strategy TEXT, symbol TEXT,
    timeframe TEXT, action TEXT, side TEXT, price REAL, qty REAL, notional REAL, fee REAL,
    pnl REAL, reason TEXT, position_id INTEGER
);
CREATE TABLE IF NOT EXISTS paper_trades (
    account TEXT NOT NULL, position_id INTEGER, symbol TEXT, timeframe TEXT, strategy TEXT,
    direction TEXT, entry_time INTEGER, exit_time INTEGER, entry_price REAL, exit_price REAL,
    qty REAL, pnl REAL, fees REAL, r_multiple REAL, entry_reason TEXT, exit_reason TEXT
);
CREATE TABLE IF NOT EXISTS paper_equity (
    account TEXT NOT NULL, time INTEGER NOT NULL, equity REAL NOT NULL,
    PRIMARY KEY (account, time)
);
"""


@dataclass
class StepResult:
    opened: list[Position]
    closed: list[Trade]


class PaperStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)
        # Ältere Datenbanken um neue Spalten ergänzen
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(paper_positions)")}
        for col in ("trail_atr", "initial_stop", "funding"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} REAL"
                                  + (" DEFAULT 0" if col == "funding" else ""))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> PaperStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ Laden
    def load(self, name: str) -> tuple[SimAccount, dict[str, int]] | None:
        row = self.conn.execute(
            "SELECT start_equity, cash, next_id, progress FROM paper_accounts WHERE name=?",
            (name,)).fetchone()
        if row is None:
            return None
        start, cash, next_id, progress = row
        acc = SimAccount(start, mode="paper", cash=cash, next_id=next_id)
        for r in self.conn.execute(
                "SELECT id, symbol, timeframe, strategy, direction, entry_time, entry_price, qty,"
                " stop, target, reason, entry_fee, trail_atr, initial_stop, COALESCE(funding, 0)"
                " FROM paper_positions WHERE account=?", (name,)):
            pos = Position(*r)
            acc.positions[(pos.symbol, pos.timeframe)] = pos
        return acc, json.loads(progress)

    def create(self, name: str, strategy: str, timeframes: list[str], start_equity: float,
               now: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO paper_accounts (name, strategy, timeframes, start_equity, cash,"
                " next_id, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                (name, strategy, ",".join(timeframes), start_equity, start_equity, now))

    # -------------------------------------------------------------- Speichern
    def save(self, name: str, acc: SimAccount, progress: dict[str, int],
             new_orders: list[Order], new_trades: list[Trade], now: int, equity: float) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE paper_accounts SET cash=?, next_id=?, progress=? WHERE name=?",
                (acc.cash, acc.next_id, json.dumps(progress), name))
            self.conn.execute("DELETE FROM paper_positions WHERE account=?", (name,))
            self.conn.executemany(
                "INSERT INTO paper_positions (account, id, symbol, timeframe, strategy, direction,"
                " entry_time, entry_price, qty, stop, target, reason, entry_fee, trail_atr,"
                " initial_stop, funding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(name, p.id, p.symbol, p.timeframe, p.strategy, p.direction, p.entry_time,
                  p.entry_price, p.qty, p.stop, p.target, p.reason, p.entry_fee, p.trail_atr,
                  p.initial_stop, p.funding)
                 for p in acc.positions.values()])
            self.conn.executemany(
                "INSERT INTO paper_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(name, *asdict(o).values()) for o in new_orders])
            self.conn.executemany(
                "INSERT INTO paper_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                [(name, *asdict(t).values()) for t in new_trades])
            self.conn.execute("INSERT OR REPLACE INTO paper_equity VALUES (?,?,?)",
                              (name, now, equity))

    # ------------------------------------------------------------- Abfragen
    def accounts(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM paper_accounts ORDER BY created_at", self.conn)

    def orders(self, name: str | None = None) -> list[Order]:
        q = "SELECT time, mode, strategy, symbol, timeframe, action, side, price, qty, notional," \
            " fee, pnl, reason, position_id FROM paper_orders"
        rows = self.conn.execute(q + (" WHERE account=?" if name else "") + " ORDER BY time",
                                 (name,) if name else ()).fetchall()
        return [Order(*r) for r in rows]

    def trades(self, name: str) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM paper_trades WHERE account=? ORDER BY exit_time",
                                 self.conn, params=(name,))

    def positions(self, name: str) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM paper_positions WHERE account=?", self.conn,
                                 params=(name,))

    def equity(self, name: str) -> pd.Series:
        df = pd.read_sql_query("SELECT time, equity FROM paper_equity WHERE account=? ORDER BY time",
                               self.conn, params=(name,))
        return pd.Series(df["equity"].to_numpy(),
                         index=pd.to_datetime(df["time"], unit="ms", utc=True), dtype="float64")


class PaperTrader:
    """Ein Paper-Konto für eine Strategie auf einem oder mehreren Timeframes."""

    def __init__(self, store: PaperStore, name: str, strategy: Strategy, timeframes: list[str],
                 *, start_equity: float = 1000.0, risk: RiskConfig | None = None, now: int = 0):
        if risk is not None and risk.research_only:
            raise ValueError("Forschungs-Risikoeinstellungen sind im Paper Trading nicht erlaubt")
        if strategy.pyramid_max:
            raise ValueError("Nachkauf (Pyramiding) ist im Paper Trading noch nicht umgesetzt")
        self.store, self.name, self.strategy, self.timeframes = store, name, strategy, timeframes
        loaded = store.load(name)
        if loaded is None:
            store.create(name, strategy.name, timeframes, start_equity, now)
            loaded = store.load(name)
        self.acc, self.progress = loaded
        self.acc.risk = RiskManager(risk or RiskConfig())

    def step(self, frames: dict[tuple[str, str], pd.DataFrame], prices: dict[str, float],
             now: int) -> StepResult:
        """Verarbeitet alle neu abgeschlossenen Kerzen.

        `frames`: (Symbol, TF) -> Kerzen mit Indikatoren (genug Vorlauf für EMA 200).
        `prices`: aktuelle Kurse je Symbol (Einstieg bei Signal an der neuesten Kerze).
        """
        n_orders, n_trades = len(self.acc.orders), len(self.acc.trades)
        opened: list[Position] = []
        for (sym, tf), df in frames.items():
            if tf not in self.timeframes or df.empty:
                continue
            key = f"{sym}|{tf}"
            last_done = self.progress.get(key)
            times = [int(t.value // 1_000_000) for t in df.index]
            if last_done is None:
                # Erster Lauf: nicht die Vergangenheit nachhandeln, ab jetzt beobachten
                self.progress[key] = times[-1]
                continue
            new_idx = [i for i, t in enumerate(times) if t > last_done]
            if not new_idx:
                continue
            signals = {}
            for s in evaluate_candles(df.iloc[max(0, new_idx[0] - 1):], sym, tf):
                signals.setdefault(s.candle_time, []).append(s)
            for i in new_idx:
                row = df.iloc[i]
                t = times[i]
                o, h, low, c = (float(row[k]) for k in ("open", "high", "low", "close"))
                pos = self.acc.positions.get((sym, tf))
                # Nur Kerzen prüfen, die nach dem Einstieg enden
                if pos is not None and t + _tf_ms(tf) > pos.entry_time:
                    # Zeitstempel = Kerzenschluss (erst dann steht der Ausstieg fest)
                    self.acc.check_exit(pos, o, h, low, t + _tf_ms(tf), float(row["atr_14"]))
                chosen = self.strategy.pick(signals.get(t, []), row)
                atr = row.get("atr_14")
                if chosen is not None and pd.notna(atr) and atr > 0:
                    latest = i == len(times) - 1
                    price = prices.get(sym, c) if latest else c
                    entry_time = now if latest else t + _tf_ms(tf)
                    pos = self.acc.positions.get((sym, tf))
                    if pos is not None and pos.direction != chosen.direction:
                        self.acc.close(pos, price, entry_time, f"Gegensignal: {chosen.reason}")
                    if (sym, tf) not in self.acc.positions:
                        stop, target = self.strategy.stop_and_target(chosen.direction, price,
                                                                      float(atr))
                        new = self.acc.open(symbol=sym, timeframe=tf, strategy=self.strategy.name,
                                            direction=chosen.direction, price=price,
                                            time=entry_time, stop=stop, target=target,
                                            reason=chosen.reason, prices=prices,
                                            trail_atr=self.strategy.exits.trail_atr)
                        if new is not None:
                            opened.append(new)
                self.progress[key] = t
        closed = self.acc.trades[n_trades:]
        self.store.save(self.name, self.acc, self.progress, self.acc.orders[n_orders:], closed,
                        now, self.acc.equity(prices))
        return StepResult(opened, closed)

    def symbols_with_positions(self) -> set[str]:
        return {p.symbol for p in self.acc.positions.values()}

    def apply_funding(self, symbol: str, rows: list[tuple[int, float, float]], now: int) -> float:
        """Rechnet tatsächlich abgerechnete Funding-Raten ab (Zeit, Rate, Mark-Preis).
        Jede Abrechnung nur einmal, nur für Positionen, die zu dem Zeitpunkt offen waren."""
        key = f"funding|{symbol}"
        n_orders = len(self.acc.orders)
        total = 0.0
        last = self.progress.get(key, 0)
        for t, rate, mark in sorted(rows):
            if t <= last:
                continue
            total += self.acc.apply_funding(symbol, rate, mark, t, opened_before=t)
            last = t
        self.progress[key] = last
        if len(self.acc.orders) > n_orders or last:
            self.store.save(self.name, self.acc, self.progress, self.acc.orders[n_orders:], [],
                            now, self.acc.equity())
        return total

    def close_all(self, prices: dict[str, float], now: int, reason: str) -> list[Trade]:
        n_orders, n_trades = len(self.acc.orders), len(self.acc.trades)
        for pos in list(self.acc.positions.values()):
            self.acc.close(pos, prices.get(pos.symbol, pos.entry_price), now, reason)
        closed = self.acc.trades[n_trades:]
        self.store.save(self.name, self.acc, self.progress, self.acc.orders[n_orders:], closed,
                        now, self.acc.equity(prices))
        return closed


def _tf_ms(tf: str) -> int:
    from bitunix.client import INTERVAL_MS

    return INTERVAL_MS[tf]
