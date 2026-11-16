"""Lädt die Funding-Historie (alle 8 h) für die Watchlist in SQLite – für Backtests.

    uv run python scripts/fetch_funding.py
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from bitunix.client import BitunixPublicClient
from data.funding import FundingStore, sync_funding

from common import DEFAULT_DB_PATH, DEFAULT_WATCHLIST, load_dotenv, setup_logging, split_list


async def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Funding-Historie laden")
    p.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    args = p.parse_args()
    setup_logging()
    with FundingStore(args.db) as store:
        async with BitunixPublicClient() as client:
            for sym in split_list(args.symbols, upper=True):
                await sync_funding(client, store, sym)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
