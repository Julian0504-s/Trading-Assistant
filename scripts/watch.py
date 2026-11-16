"""Dauerbetrieb: Kerzen nachladen, Signale erzeugen, per Telegram melden – in einer Schleife.

Aufruf:
    uv run python scripts/watch.py                 # Standard: alle 60 s
    uv run python scripts/watch.py --interval 120

Ein Timeframe wird nur nachgeladen, wenn seit dem letzten Durchlauf eine neue
Kerze abgeschlossen sein kann (1h-Kerzen also nur einmal pro Stunde).
Beenden mit Strg+C.

Telegram: TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID in .env setzen. Ohne diese
läuft alles, es wird nur nichts verschickt.
Signale sind Hinweise, keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

from alerts import CooldownStore, TelegramError, dispatch
from bitunix.client import INTERVAL_MS, BitunixError, BitunixPublicClient
from data.store import CandleStore
from data.sync import sync_candles
from signals import SignalStore
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

log = logging.getLogger("watch")

# Kerzen werden erst kurz nach dem Schließen von der API geliefert
CLOSE_GRACE_S = 5


def due_timeframes(timeframes: list[str], last_bucket: dict[str, int], now_s: float) -> list[str]:
    """Timeframes, bei denen seit dem letzten Laden eine neue Kerze geschlossen hat."""
    due = []
    for tf in timeframes:
        bucket = int((now_s - CLOSE_GRACE_S) * 1000) // INTERVAL_MS[tf]
        if last_bucket.get(tf) != bucket:
            due.append(tf)
            last_bucket[tf] = bucket
    return due


async def run(args: argparse.Namespace) -> None:
    symbols = split_list(args.symbols, upper=True)
    timeframes = check_timeframes(split_list(args.timeframes))
    policy = alert_policy_from_env()
    notifier = notifier_from_env()
    threshold = float(os.getenv("FUNDING_EXTREME_PER_8H", FUNDING_EXTREME_PER_8H))

    log.info("Start: %s × %s, alle %ds, Telegram %s, Alerts ab Stärke %d, Cooldown %d min",
             ",".join(symbols), ",".join(timeframes), args.interval,
             "aktiv" if notifier else "NICHT konfiguriert", policy.min_strength, policy.cooldown_min)

    last_bucket: dict[str, int] = {}
    with CandleStore(args.db) as candles, SignalStore(args.db) as store, \
            CooldownStore(args.db) as cooldowns:
        async with BitunixPublicClient() as client:
            if notifier is not None:
                await notifier.__aenter__()
            try:
                while True:
                    started = time.monotonic()
                    try:
                        due = due_timeframes(timeframes, last_bucket, time.time())
                        for sym in symbols:
                            for tf in due:
                                try:
                                    await sync_candles(client, candles, sym, tf)
                                except BitunixError as e:
                                    log.error("%s %s: Kerzen laden fehlgeschlagen: %s", sym, tf, e)
                        result = await scan_watchlist(
                            client, candles, store, symbols, timeframes,
                            funding_threshold_per_8h=threshold,
                        )
                        fresh = [s for s in result.new if result.is_fresh(s)]
                        for s in fresh:
                            log.info("Signal: %s %s %s %s – %s", s.symbol, s.timeframe,
                                     s.direction, s.strength, s.reason)
                        chosen = await dispatch(fresh, policy, cooldowns, notifier)
                        if chosen:
                            log.info("%d Signal(e) %s", len(chosen),
                                     "per Telegram gemeldet" if notifier else
                                     "wären gemeldet worden (Telegram nicht konfiguriert)")
                    except TelegramError as e:
                        log.error("Telegram-Versand fehlgeschlagen: %s", e)
                    except Exception:  # Schleife soll weiterlaufen
                        log.exception("Fehler im Durchlauf")
                    elapsed = time.monotonic() - started
                    await asyncio.sleep(max(1.0, args.interval - elapsed))
            finally:
                if notifier is not None:
                    await notifier.__aexit__(None, None, None)


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Markt beobachten und Signale melden")
    p.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    p.add_argument("--timeframes", default=os.getenv("TIMEFRAMES", DEFAULT_TIMEFRAMES))
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--interval", type=int, default=60, help="Sekunden zwischen Durchläufen")
    args = p.parse_args()
    setup_logging()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("Beendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
