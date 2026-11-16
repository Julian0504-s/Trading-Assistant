"""SQLite-Speicher für Signale (gleiche DB-Datei wie die Kerzen möglich)."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .models import Signal

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol       TEXT    NOT NULL,
    timeframe    TEXT    NOT NULL,
    rule         TEXT    NOT NULL,
    direction    TEXT    NOT NULL,   -- long | short
    strength     INTEGER NOT NULL,   -- 1..3
    reason       TEXT    NOT NULL,   -- Begründung in Klartext
    price        REAL    NOT NULL,
    candle_time  INTEGER NOT NULL,   -- ms UTC, auslösende Kerze
    created_at   INTEGER NOT NULL,   -- ms UTC, wann erkannt
    UNIQUE (symbol, timeframe, rule, direction, candle_time)
);
CREATE INDEX IF NOT EXISTS idx_signals_candle_time ON signals (candle_time DESC);
"""

_COLS = "symbol, timeframe, rule, direction, strength, reason, price, candle_time, created_at"


class SignalStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SignalStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def add(self, signals: Iterable[Signal]) -> list[Signal]:
        """Speichert Signale; bereits bekannte (gleicher Schlüssel) werden übersprungen.

        Gibt nur die tatsächlich NEU gespeicherten Signale zurück.
        """
        new: list[Signal] = []
        with self._conn:
            for s in signals:
                cur = self._conn.execute(
                    f"INSERT OR IGNORE INTO signals ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (s.symbol, s.timeframe, s.rule, s.direction, s.strength, s.reason,
                     s.price, s.candle_time, s.created_at),
                )
                if cur.rowcount:
                    new.append(s)
        return new

    def recent(
        self,
        limit: int = 50,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        rule: str | None = None,
        direction: str | None = None,
        min_strength: int = 1,
    ) -> list[Signal]:
        """Neueste Signale zuerst, optional gefiltert."""
        where, params = ["strength >= ?"], [min_strength]
        for col, val in (("symbol", symbol), ("timeframe", timeframe),
                         ("rule", rule), ("direction", direction)):
            if val is not None:
                where.append(f"{col} = ?")
                params.append(val)
        rows = self._conn.execute(
            f"SELECT {_COLS} FROM signals WHERE {' AND '.join(where)} "
            "ORDER BY candle_time DESC, id DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [Signal(*r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM signals").fetchone()[0]
