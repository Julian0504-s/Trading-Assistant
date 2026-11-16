"""Kerzen von Bitunix laden und inkrementell in SQLite speichern."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from bitunix.client import INTERVAL_MS, KLINE_MAX_LIMIT, BitunixPublicClient
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
    if latest is not None:
        start = latest
    else:
        # Auf den Beginn der laufenden (noch offenen) Kerze abrunden, damit genau
        # `backfill` abgeschlossene Kerzen geladen werden.
        start = (now_ms // step) * step - backfill * step

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


# Wie viele Kerzen pro Timeframe für Backtests vorgehalten werden (≈ Zeitraum)
HISTORY_DEFAULTS = {
    "1m": 10_000,   # ~7 Tage
    "3m": 10_000,   # ~21 Tage
    "5m": 10_000,   # ~35 Tage
    "15m": 10_000,  # ~104 Tage
    "30m": 8_000,   # ~167 Tage
    "1h": 8_760,    # 1 Jahr
    "2h": 6_000,
    "4h": 4_380,    # 2 Jahre
    "6h": 3_000,
    "8h": 2_500,
    "12h": 2_000,
    "1d": 1_500,    # ~4 Jahre
    "3d": 500,
    "1w": 300,
}


MAX_EMPTY_PAGES = 4


async def backfill_history(
    client: BitunixPublicClient,
    store: CandleStore,
    symbol: str,
    interval: str,
    target: int,
) -> int:
    """Lädt ältere Kerzen nach, bis mindestens `target` Kerzen gespeichert sind.

    Blättert von der ältesten gespeicherten Kerze rückwärts. Gibt die Anzahl
    neu geladener Kerzen zurück.
    """
    loaded = 0
    step = INTERVAL_MS[interval]
    empty = 0  # aufeinanderfolgende leere Seiten
    while store.count(symbol, interval) < target:
        earliest = store.earliest_open_time(symbol, interval)
        # Bei leeren Antworten erst erneut versuchen, dann über eine mögliche
        # Datenlücke hinwegspringen (je ein Seitenumfang weiter zurück).
        end = None if earliest is None else earliest - max(0, empty - 1) * KLINE_MAX_LIMIT * step
        page = await client.get_klines(symbol, interval, limit=KLINE_MAX_LIMIT, end_time=end)
        page = [k for k in page if earliest is None or k.open_time < earliest]
        if not page:
            empty += 1
            if empty > MAX_EMPTY_PAGES:
                break  # Anfang der Historie erreicht
            continue
        empty = 0
        store.upsert(symbol, interval, page)
        loaded += len(page)
    log.info("%s %s: %d ältere Kerzen geladen, %d gesamt",
             symbol, interval, loaded, store.count(symbol, interval))
    return loaded


def needs_sync(store: CandleStore, symbol: str, interval: str, now_ms: int,
               grace_ms: int = 3_000) -> bool:
    """True, wenn die zuletzt abgeschlossene Kerze noch nicht gespeichert ist.

    Kommt eine Kerze von der API verspätet, wird es beim nächsten Durchlauf
    einfach erneut versucht (statt bis zur nächsten Kerze zu warten).
    """
    step = INTERVAL_MS[interval]
    expected = ((now_ms - grace_ms) // step) * step - step  # Eröffnung der letzten fertigen Kerze
    latest = store.latest_open_time(symbol, interval)
    return latest is None or latest < expected
