"""Grundeinstellungen (Watchlist, Timeframes, DB-Pfad) aus Umgebungsvariablen / .env."""

from __future__ import annotations

import os

DEFAULT_WATCHLIST = "BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT"
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


def split_list(value: str, upper: bool = False) -> list[str]:
    items = [x.strip() for x in value.split(",") if x.strip()]
    return [x.upper() for x in items] if upper else items


def watchlist() -> list[str]:
    return split_list(os.getenv("WATCHLIST", DEFAULT_WATCHLIST), upper=True)


def timeframes() -> list[str]:
    return split_list(os.getenv("TIMEFRAMES", DEFAULT_TIMEFRAMES))


def db_path() -> str:
    return os.getenv("DB_PATH", DEFAULT_DB_PATH)
