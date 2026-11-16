"""Signale für die Watchlist erzeugen und in SQLite speichern.

Aufruf:
    uv run python scripts/run_signals.py            # Kerzen aus der DB + Funding live
    uv run python scripts/run_signals.py --fetch    # vorher Kerzen nachladen
    uv run python scripts/run_signals.py --feed 30  # zusätzlich die 30 neuesten Signale zeigen
    uv run python scripts/run_signals.py --alert    # neue Signale per Telegram melden

Beim ersten Lauf werden auch historische Signale aus den vorhandenen Kerzen
nachgetragen (für Chart-Marker); angezeigt werden davon nur die Anzahl.

Signale sind Hinweise, keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime

from bitunix.client import BitunixError, BitunixPublicClient
from data.store import CandleStore
from data.sync import sync_candles
from alerts import CooldownStore, TelegramError, dispatch
from signals import DIRECTION_LABEL, STRENGTH_LABEL, Signal, SignalStore
from signals.rules import FUNDING_EXTREME_PER_8H
from signals.scan import scan_watchlist

from common import (
    DEFAULT_DB_PATH,
    DEFAULT_TIMEFRAMES,
    DEFAULT_WATCHLIST,
    alert_policy_from_env,
    check_timeframes,
    load_dotenv,
    notifier_from_env,
    setup_logging,
    split_list,
)


def _ts(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d %H:%M")


def _line(s: Signal) -> str:
    arrow = "▲" if s.direction == "long" else "▼"
    return (f"{_ts(s.candle_time)}  {s.symbol:<9}{s.timeframe:>7}  {arrow} "
            f"{DIRECTION_LABEL[s.direction]:<8} {STRENGTH_LABEL[s.strength]:<7} {s.reason}")


async def _send_alerts(fresh: list[Signal], db: str) -> None:
    policy = alert_policy_from_env()
    notifier = notifier_from_env()
    with CooldownStore(db) as cooldowns:
        try:
            if notifier is None:
                chosen = await dispatch(fresh, policy, cooldowns, None)
                print(f"\nTelegram nicht konfiguriert (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) – "
                      f"{len(chosen)} Signal(e) wären gemeldet worden.")
            else:
                async with notifier:
                    chosen = await dispatch(fresh, policy, cooldowns, notifier)
                print(f"\nTelegram: {len(chosen)} Signal(e) gemeldet.")
        except TelegramError as e:
            logging.error("Telegram-Versand fehlgeschlagen: %s", e)


async def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Signale erzeugen")
    p.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    p.add_argument("--timeframes", default=os.getenv("TIMEFRAMES", DEFAULT_TIMEFRAMES))
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--fetch", action="store_true", help="vorher Kerzen nachladen")
    p.add_argument("--feed", type=int, default=0, metavar="N", help="N neueste Signale zeigen")
    p.add_argument("--alert", action="store_true", help="neue Signale per Telegram melden")
    p.add_argument(
        "--funding-threshold", type=float,
        default=float(os.getenv("FUNDING_EXTREME_PER_8H", FUNDING_EXTREME_PER_8H)),
        help="Schwelle für extreme Funding Rate pro 8h als Bruch (0.0005 = 0,05 %%)",
    )
    args = p.parse_args()

    setup_logging()
    symbols = split_list(args.symbols, upper=True)
    timeframes = check_timeframes(split_list(args.timeframes))

    with CandleStore(args.db) as candles, SignalStore(args.db) as store:
        async with BitunixPublicClient() as client:
            if args.fetch:
                for sym in symbols:
                    for tf in timeframes:
                        try:
                            await sync_candles(client, candles, sym, tf)
                        except BitunixError as e:
                            logging.error("%s %s: Kerzen laden fehlgeschlagen: %s", sym, tf, e)
            result = await scan_watchlist(
                client, candles, store, symbols, timeframes,
                funding_threshold_per_8h=args.funding_threshold,
            )

        fresh = [s for s in result.new if result.is_fresh(s)]
        historic = len(result.new) - len(fresh)
        print(f"\nNeue aktuelle Signale (letzte 3 Kerzen): {len(fresh)}"
              + (f"  (+ {historic} historische nachgetragen)" if historic else ""))
        for s in fresh:
            print("  " + _line(s))
        for err in result.errors:
            print(f"  Hinweis: {err}")

        if args.alert:
            await _send_alerts(fresh, args.db)

        if args.feed:
            print(f"\nSignal-Feed (neueste {args.feed}):")
            for s in store.recent(args.feed):
                print("  " + _line(s))

    print("\nSignale sind Hinweise, keine Anlageempfehlung.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
