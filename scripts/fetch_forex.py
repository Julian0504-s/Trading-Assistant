"""Lädt ECB-Referenzkurse und speichert 10 Forex-Paare als Tageskerzen (nur für Backtests).

    uv run python scripts/fetch_forex.py      # -> data/forex.db
"""

from __future__ import annotations

import argparse
import sys

import httpx

from data.ecb import fetch, pair_closes, to_klines
from data.store import CandleStore


def main() -> int:
    p = argparse.ArgumentParser(description="ECB-Forex-Kurse laden")
    p.add_argument("--db", default="data/forex.db")
    args = p.parse_args()
    with httpx.Client(timeout=120, headers={"User-Agent": "trading-assistant/0.1"}) as client:
        table = fetch(client)
    with CandleStore(args.db) as cs:
        for name, closes in pair_closes(table).items():
            cs.upsert(name, "1d", to_klines(closes))
            print(f"{name}: {len(closes)} Tage, {closes.index[0]:%d.%m.%Y}–{closes.index[-1]:%d.%m.%Y}, "
                  f"zuletzt {closes.iloc[-1]:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
