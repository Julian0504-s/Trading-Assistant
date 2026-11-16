"""Historische Funding-Raten: Laden, Speichern und als Zeitreihe für Backtests."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pandas as pd

from bitunix.client import BitunixPublicClient

log = logging.getLogger(__name__)

FUNDING_INTERVAL_MS = 8 * 3_600_000  # alle Coins der Watchlist rechnen alle 8 h ab

_SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_history (
    symbol  TEXT    NOT NULL,
    time    INTEGER NOT NULL,   -- Abrechnungszeitpunkt, ms UTC
    rate    REAL    NOT NULL,   -- Bruch pro Abrechnung (0.0001 = 0,01 %)
    mark    REAL,               -- Mark-Preis zur Abrechnung
    PRIMARY KEY (symbol, time)
) WITHOUT ROWID;
"""


class FundingStore:
    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> FundingStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def upsert(self, symbol: str, rows: list[tuple[int, float, float]]) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT OR REPLACE INTO funding_history VALUES (?, ?, ?, ?)",
                [(symbol, t, r, m) for t, r, m in rows])

    def bounds(self, symbol: str) -> tuple[int | None, int | None]:
        return self._conn.execute(
            "SELECT MIN(time), MAX(time) FROM funding_history WHERE symbol=?", (symbol,)).fetchone()

    def load(self, symbol: str) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT time, rate, mark FROM funding_history WHERE symbol=?"
                               " ORDER BY time", self._conn, params=(symbol,))
        return df


async def sync_funding(client: BitunixPublicClient, store: FundingStore, symbol: str,
                       max_pages: int = 100) -> int:
    """Lädt neue und ältere Funding-Raten, bis die API keine älteren mehr liefert."""
    loaded = 0
    rows = await client.get_funding_history(symbol)  # neueste
    store.upsert(symbol, rows)
    loaded += len(rows)
    for _ in range(max_pages):
        earliest, _latest = store.bounds(symbol)
        rows = [r for r in await client.get_funding_history(symbol, end_time=earliest - 1)
                if r[0] < earliest]
        if not rows:
            break
        store.upsert(symbol, rows)
        loaded += len(rows)
    lo, hi = store.bounds(symbol)
    log.info("%s Funding: %d Einträge geladen, gespeichert %s bis %s", symbol, loaded,
             pd.Timestamp(lo, unit="ms").date() if lo else "-",
             pd.Timestamp(hi, unit="ms").date() if hi else "-")
    return loaded


def funding_schedule(store: FundingStore, symbol: str, start: int, end: int) -> pd.DataFrame:
    """Funding-Abrechnungen alle 8 h von `start` bis `end` (ms) für den Backtest.

    Echte Raten, wo vorhanden. An jedem Abrechnungszeitpunkt OHNE echten Wert
    (Bitunix-Historie beginnt ca. 03/2024, dazu einzelne Lücken) wird die
    MEDIAN-Rate des Coins angenommen – Spalte `estimated` = True.
    Ohne jede Historie: +0,01 % (üblicher Standardwert).
    """
    hist = store.load(symbol)
    med = float(hist["rate"].median()) if len(hist) else 0.0001
    t0 = -(-start // FUNDING_INTERVAL_MS) * FUNDING_INTERVAL_MS
    grid = pd.DataFrame({"time": range(t0, end + 1, FUNDING_INTERVAL_MS)})
    if len(hist):
        snapped = hist.assign(time=(hist["time"] + FUNDING_INTERVAL_MS // 2)
                              // FUNDING_INTERVAL_MS * FUNDING_INTERVAL_MS)
        snapped = snapped.drop_duplicates("time", keep="last")
        grid = grid.merge(snapped, on="time", how="left")
    else:
        grid["rate"], grid["mark"] = float("nan"), float("nan")
    grid["estimated"] = grid["rate"].isna()
    grid["rate"] = grid["rate"].fillna(med)
    return grid


def load_schedules(db: str | Path, symbols: list[str], start: int, end: int) -> dict[str, pd.DataFrame]:
    """funding_schedule für mehrere Symbole (für Backtests)."""
    with FundingStore(db) as store:
        return {s: funding_schedule(store, s, start, end) for s in symbols}
