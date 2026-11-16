"""Gemeinsame Konfiguration für die Skripte (aus Umgebungsvariablen / .env)."""

from __future__ import annotations

import logging
import os

from alerts import AlertPolicy, TelegramNotifier
from bitunix.client import INTERVAL_MS

DEFAULT_WATCHLIST = "BTCUSDT,ETHUSDT,SOLUSDT"
DEFAULT_TIMEFRAMES = "1m,3m,5m,15m,1h,4h,1d"
DEFAULT_DB_PATH = "data/market.db"


def load_dotenv(path: str = ".env") -> None:
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


def setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx loggt auf INFO jede URL – bei Telegram steht der Token in der URL!
    logging.getLogger("httpx").setLevel(logging.WARNING)


def split_list(value: str, upper: bool = False) -> list[str]:
    items = [x.strip() for x in value.split(",") if x.strip()]
    return [x.upper() for x in items] if upper else items


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
