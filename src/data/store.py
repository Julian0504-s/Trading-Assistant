"""SQLite-Speicher für Kerzen."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from bitunix.models import Kline

_SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    symbol     TEXT    NOT NULL,
    interval   TEXT    NOT NULL,
    open_time  INTEGER NOT NULL,  -- Eröffnungszeit, ms seit Epoch (UTC)
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    base_vol   REAL    NOT NULL,  -- Volumen in Basis-Coin
    quote_vol  REAL    NOT NULL,  -- Umsatz in USDT
    PRIMARY KEY (symbol, interval, open_time)
) WITHOUT ROWID;
"""


class CandleStore:
    """Kerzen-Cache in SQLite. Gleiche (symbol, interval, open_time) werden überschrieben."""

    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CandleStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert(self, symbol: str, interval: str, klines: Iterable[Kline]) -> int:
        """Speichert Kerzen; gibt die Anzahl geschriebener Zeilen zurück."""
        rows = [
            (symbol, interval, k.open_time, k.open, k.high, k.low, k.close, k.base_vol, k.quote_vol)
            for k in klines
        ]
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO candles
                    (symbol, interval, open_time, open, high, low, close, base_vol, quote_vol)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, open_time) DO UPDATE SET
                    open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, base_vol=excluded.base_vol, quote_vol=excluded.quote_vol
                """,
                rows,
            )
        return len(rows)

    def latest_open_time(self, symbol: str, interval: str) -> int | None:
        """open_time der neuesten gespeicherten Kerze oder None."""
        row = self._conn.execute(
            "SELECT MAX(open_time) FROM candles WHERE symbol = ? AND interval = ?",
            (symbol, interval),
        ).fetchone()
        return row[0]

    def count(self, symbol: str, interval: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM candles WHERE symbol = ? AND interval = ?",
            (symbol, interval),
        ).fetchone()
        return row[0]

    def load(self, symbol: str, interval: str, limit: int | None = None) -> list[Kline]:
        """Lädt Kerzen aufsteigend sortiert (mit `limit`: die neuesten N)."""
        sql = (
            "SELECT open_time, open, high, low, close, base_vol, quote_vol FROM candles "
            "WHERE symbol = ? AND interval = ? ORDER BY open_time DESC"
        )
        params: tuple = (symbol, interval)
        if limit is not None:
            sql += " LIMIT ?"
            params += (limit,)
        rows = self._conn.execute(sql, params).fetchall()
        return [Kline(*r) for r in reversed(rows)]
