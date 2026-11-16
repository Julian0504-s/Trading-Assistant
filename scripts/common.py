"""Gemeinsame Konfiguration für die Skripte (aus Umgebungsvariablen / .env)."""

from __future__ import annotations

import logging
import os

from alerts import AlertPolicy, TelegramNotifier
from bitunix.client import INTERVAL_MS
from data.settings import (  # noqa: F401  (für die Skripte re-exportiert)
    DEFAULT_DB_PATH,
    DEFAULT_TIMEFRAMES,
    DEFAULT_WATCHLIST,
    load_dotenv,
    split_list,
)


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx loggt auf INFO jede URL – bei Telegram steht der Token in der URL!
    logging.getLogger("httpx").setLevel(logging.WARNING)


def check_timeframes(timeframes: list[str]) -> list[str]:
    unknown = [t for t in timeframes if t not in INTERVAL_MS]
    if unknown:
        raise SystemExit(f"Unbekannte Timeframes {unknown}, erlaubt: {', '.join(INTERVAL_MS)}")
    return timeframes


def alert_policy_from_env() -> AlertPolicy:
    tfs = os.getenv("ALERT_TIMEFRAMES", "").strip()
    return AlertPolicy(
        min_strength=int(os.getenv("ALERT_MIN_STRENGTH", "2")),
        timeframes=frozenset(split_list(tfs)) if tfs else None,
        cooldown_min=int(os.getenv("ALERT_COOLDOWN_MIN", "60")),
    )


def notifier_from_env() -> TelegramNotifier | None:
    """Telegram-Notifier, wenn Token und Chat-ID gesetzt sind, sonst None."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return None
    return TelegramNotifier(token, chat_id)
