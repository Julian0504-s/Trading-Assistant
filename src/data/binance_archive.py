"""Historische Daten aus dem öffentlichen Binance-Archiv (data.binance.vision).

Nur für Backtests über längere Zeiträume (Bitunix liefert Kerzen erst ab 04/2022).
Gehandelt wird weiterhin ausschließlich auf Bitunix. USDT-M-Futures, Monatsdateien:
  futures/um/monthly/klines/<SYMBOL>/<TF>/<SYMBOL>-<TF>-<YYYY-MM>.zip
  futures/um/monthly/fundingRate/<SYMBOL>/<SYMBOL>-fundingRate-<YYYY-MM>.zip
Die Dateien werden nur im Speicher entpackt und als Zahlen eingelesen.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile

import httpx

from bitunix.models import Kline

log = logging.getLogger(__name__)

BASE = "https://data.binance.vision/data/futures/um/monthly"


def _rows(zip_bytes: bytes) -> list[list[str]]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError(f"unerwarteter ZIP-Inhalt: {z.namelist()}")
        text = z.read(names[0]).decode("utf-8")
    rows = list(csv.reader(io.StringIO(text)))
    # Neuere Dateien haben eine Kopfzeile, ältere nicht
    return [r for r in rows if r and r[0].strip().lstrip("-").isdigit()]


def parse_klines(zip_bytes: bytes) -> list[Kline]:
    """Spalten: open_time, open, high, low, close, volume, close_time, quote_volume, ..."""
    out = []
    for r in _rows(zip_bytes):
        out.append(Kline(open_time=int(r[0]), open=float(r[1]), high=float(r[2]),
                         low=float(r[3]), close=float(r[4]), base_vol=float(r[5]),
                         quote_vol=float(r[7])))
    return out


def parse_funding(zip_bytes: bytes) -> list[tuple[int, float, float]]:
    """Spalten: calc_time, funding_interval_hours, last_funding_rate (Bruch)."""
    return [(int(r[0]), float(r[2]), float("nan")) for r in _rows(zip_bytes)]


def months(start: str, end: str) -> list[str]:
    y, m = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_month(client: httpx.Client, kind: str, symbol: str, month: str,
                timeframe: str = "1d") -> bytes | None:
    if kind == "klines":
        url = f"{BASE}/klines/{symbol}/{timeframe}/{symbol}-{timeframe}-{month}.zip"
    else:
        url = f"{BASE}/fundingRate/{symbol}/{symbol}-fundingRate-{month}.zip"
    resp = client.get(url)
    if resp.status_code == 404:
        return None  # Coin damals noch nicht gelistet
    resp.raise_for_status()
    return resp.content
