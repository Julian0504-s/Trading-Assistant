"""Lädt lange Historie (Tageskerzen + Funding) aus dem Binance-Archiv – nur für Backtests.

    uv run python scripts/fetch_binance_history.py

Speichert in eine EIGENE Datenbank (Standard: data/binance.db), getrennt von den
Bitunix-Daten. Gehandelt wird weiterhin nur auf Bitunix.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import UTC, datetime, timedelta

import httpx

from data.binance_archive import fetch_month, months, parse_funding, parse_klines
from data.funding import FundingStore
from data.store import CandleStore

from common import setup_logging, split_list

UNIVERSE = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT"


def main() -> int:
    p = argparse.ArgumentParser(description="Binance-Archiv laden (nur Backtest)")
    p.add_argument("--symbols", default=UNIVERSE)
    p.add_argument("--start", default="2019-09")
    p.add_argument("--timeframe", default="1d")
    p.add_argument("--db", default="data/binance.db")
    p.add_argument("--market", choices=("futures", "spot"), default="futures",
                   help="spot z. B. für PAXGUSDT (Gold) oder EURUSDT (EUR/USD)")
    args = p.parse_args()
    setup_logging()
    last_month = (datetime.now(UTC).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    ms = months(args.start, last_month)
    with CandleStore(args.db) as cs, FundingStore(args.db) as fs, \
            httpx.Client(timeout=30, headers={"User-Agent": "trading-assistant/0.1"}) as client:
        for sym in split_list(args.symbols, upper=True):
            n_k = n_f = 0
            first = None
            for m in ms:
                data = fetch_month(client, "klines", sym, m, args.timeframe, args.market)
                if data:
                    kl = parse_klines(data)
                    cs.upsert(sym, args.timeframe, kl)
                    n_k += len(kl)
                    first = first or m
                fd = fetch_month(client, "funding", sym, m, market=args.market)
                if fd:
                    rows = parse_funding(fd)
                    fs.upsert(sym, rows)
                    n_f += len(rows)
                time.sleep(0.05)
            print(f"{sym:<9} {args.timeframe}-Kerzen: {n_k:>5} ab {first or '-'} · Funding-Einträge: {n_f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
