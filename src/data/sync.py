"""Kerzen von Bitunix laden und inkrementell in SQLite speichern."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bitunix.client import INTERVAL_MS, BitunixPublicClient
from data.store import CandleStore

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SyncResult:
    symbol: str
    interval: str
    fetched: int
    total: int
    latest_open_time: int | None


async def sync_candles(
    client: BitunixPublicClient,
    store: CandleStore,
    symbol: str,
    interval: str,
    *,
    backfill: int = 500,
    now_ms: int | None = None,
) -> SyncResult:
    """Holt fehlende Kerzen für ein Symbol.

    - Leere DB: die letzten `backfill` Kerzen.
    - Sonst: ab der zuletzt gespeicherten Kerze (die wird zur Sicherheit neu geladen).
    """
    step = INTERVAL_MS[interval]
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)

    latest = store.latest_open_time(symbol, interval)
    start = latest if latest is not None else now_ms - backfill * step

    klines = await client.get_klines_since(symbol, interval, start)
    store.upsert(symbol, interval, klines)
    result = SyncResult(
        symbol=symbol,
        interval=interval,
        fetched=len(klines),
        total=store.count(symbol, interval),
        latest_open_time=store.latest_open_time(symbol, interval),
    )
    log.info("%s %s: %d Kerzen geladen, %d gesamt", symbol, interval, result.fetched, result.total)
    return result
