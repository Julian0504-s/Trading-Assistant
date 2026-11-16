"""Lädt Kerzen für die Watchlist (alle Timeframes) und speichert sie in SQLite.

Aufruf:
    uv run python scripts/fetch_candles.py
    uv run python scripts/fetch_candles.py --timeframes 1h,4h --backfill 1000
    uv run python scripts/fetch_candles.py --symbols BTCUSDT,ETHUSDT
    uv run python scripts/fetch_candles.py --history   # lange Historie für Backtests

Watchlist, Timeframes und DB-Pfad kommen aus den Umgebungsvariablen WATCHLIST,
TIMEFRAMES und DB_PATH (siehe .env.example), sonst aus den Standardwerten unten.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime

from bitunix.client import INTERVAL_MS, BitunixError, BitunixPublicClient
from data.store import CandleStore
from data.sync import HISTORY_DEFAULTS, backfill_history, sync_candles

from common import (
    DEFAULT_DB_PATH,
    DEFAULT_TIMEFRAMES,
    DEFAULT_WATCHLIST,
    load_dotenv,
    setup_logging,
    split_list,
)

def _fmt(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


async def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Kerzen für die Watchlist laden")
    parser.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    parser.add_argument(
        "--timeframes", default=os.getenv("TIMEFRAMES", DEFAULT_TIMEFRAMES),
        help="Komma-getrennt, z. B. 1m,3m,5m,15m,1h,4h,1d",
    )
    parser.add_argument(
        "--backfill", type=int, default=500,
        help="Anzahl Kerzen pro Timeframe beim ersten Laden",
    )
    parser.add_argument(
        "--history", action="store_true",
        help="zusätzlich lange Historie für Backtests laden (z. B. 1h: 1 Jahr, 15m: ~100 Tage)",
    )
    parser.add_argument(
        "--history-target", default="",
        help="eigene Ziel-Anzahl je Timeframe für --history, z. B. 1h=17520,4h=8760",
    )
    parser.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    args = parser.parse_args()

    setup_logging()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    timeframes = [t.strip() for t in args.timeframes.split(",") if t.strip()]
    unknown = [t for t in timeframes if t not in INTERVAL_MS]
    if unknown:
        parser.error(f"Unbekannte Timeframes {unknown}, erlaubt: {', '.join(INTERVAL_MS)}")

    targets = {k: int(v) for k, _, v in
               (item.partition("=") for item in split_list(args.history_target))}
    errors = 0
    with CandleStore(args.db) as store:
        async with BitunixPublicClient() as client:
            for symbol in symbols:
                for tf in timeframes:
                    try:
                        r = await sync_candles(client, store, symbol, tf, backfill=args.backfill)
                        if args.history:
                            await backfill_history(client, store, symbol, tf,
                                                   targets.get(tf, HISTORY_DEFAULTS.get(tf, args.backfill)))
                            r = type(r)(r.symbol, r.interval, r.fetched,
                                        store.count(symbol, tf), r.latest_open_time)
                    except BitunixError as e:
                        errors += 1
                        logging.error("%s %s: Laden fehlgeschlagen: %s", symbol, tf, e)
                        continue
                    print(
                        f"{r.symbol:<10} {r.interval:>4}  neu/aktualisiert: {r.fetched:>5}  "
                        f"gesamt: {r.total:>6}  letzte Kerze: {_fmt(r.latest_open_time)}"
                    )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
