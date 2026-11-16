"""Lädt Kerzen (Standard: 1h) für die Watchlist und speichert sie in SQLite.

Aufruf:
    uv run python scripts/fetch_candles.py
    uv run python scripts/fetch_candles.py --interval 4h --backfill 1000
    uv run python scripts/fetch_candles.py --symbols BTCUSDT,ETHUSDT

Watchlist und DB-Pfad kommen aus den Umgebungsvariablen WATCHLIST und DB_PATH
(siehe .env.example), sonst aus den Standardwerten unten.
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
from data.sync import sync_candles

DEFAULT_WATCHLIST = "BTCUSDT,ETHUSDT,SOLUSDT"
DEFAULT_DB_PATH = "data/market.db"


def _load_dotenv(path: str = ".env") -> None:
    """Minimaler .env-Leser (nur KEY=VALUE), überschreibt keine gesetzten Variablen."""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _fmt(ms: int | None) -> str:
    if ms is None:
        return "-"
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


async def main() -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Kerzen für die Watchlist laden")
    parser.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    parser.add_argument("--interval", default="1h", choices=sorted(INTERVAL_MS))
    parser.add_argument("--backfill", type=int, default=500, help="Anzahl Kerzen beim ersten Laden")
    parser.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # nicht jede Anfrage loggen
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    errors = 0
    with CandleStore(args.db) as store:
        async with BitunixPublicClient() as client:
            for symbol in symbols:
                try:
                    r = await sync_candles(client, store, symbol, args.interval, backfill=args.backfill)
                except BitunixError as e:
                    errors += 1
                    logging.error("%s: Laden fehlgeschlagen: %s", symbol, e)
                    continue
                print(
                    f"{r.symbol:<10} {r.interval:>4}  neu/aktualisiert: {r.fetched:>5}  "
                    f"gesamt: {r.total:>6}  letzte Kerze: {_fmt(r.latest_open_time)}"
                )
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
