"""Forex-Tageskurse aus den ECB-Referenzkursen (data-api.ecb.europa.eu) – nur für Backtests.

Die EZB veröffentlicht seit 04.01.1999 werktäglich um ca. 14:15 MEZ die Kurse des Euro
gegen ~30 Währungen. Daraus werden Währungspaare gebildet (z. B. GBP/USD = EUR/USD ÷
EUR/GBP). Es gibt nur EINEN Kurs pro Tag – kein Hoch/Tief. Für die Kerzen gilt daher:
Eröffnung = Vortagesschluss, Hoch/Tief = größerer/kleinerer der beiden Werte. Stops
greifen damit nur zum Tageskurs (ein Bot, der einmal täglich entscheidet).
"""

from __future__ import annotations

import csv
import io

import httpx
import pandas as pd

from bitunix.models import Kline

URL = "https://data-api.ecb.europa.eu/service/data/EXR/D.{cur}.EUR.SP00.A?format=csvdata"
CURRENCIES = ("USD", "GBP", "JPY", "CHF", "AUD", "CAD", "NZD")

# Paar -> (Zähler, Nenner) als Ausdruck in "Währung je EUR": Kurs = je_EUR[b] / je_EUR[a]
# Für ein Paar A/B (Preis von 1 A in B) gilt: A/B = (B je EUR) / (A je EUR); EUR je EUR = 1.
PAIRS = {
    "EURUSD": ("EUR", "USD"), "GBPUSD": ("GBP", "USD"), "USDJPY": ("USD", "JPY"),
    "USDCHF": ("USD", "CHF"), "AUDUSD": ("AUD", "USD"), "USDCAD": ("USD", "CAD"),
    "NZDUSD": ("NZD", "USD"), "EURGBP": ("EUR", "GBP"), "EURJPY": ("EUR", "JPY"),
    "GBPJPY": ("GBP", "JPY"),
}


def parse_csv(text: str) -> pd.DataFrame:
    """ECB-CSV -> Tabelle (Datum x Währung) mit 'Währung je EUR'."""
    rows = []
    for r in csv.DictReader(io.StringIO(text)):
        value = r.get("OBS_VALUE", "").strip()
        if not value:
            continue  # Feiertage o. Ä.
        rows.append((r["TIME_PERIOD"], r["CURRENCY"], float(value)))
    df = pd.DataFrame(rows, columns=["date", "currency", "per_eur"])
    table = df.pivot(index="date", columns="currency", values="per_eur").sort_index()
    table.index = pd.to_datetime(table.index, utc=True)
    table["EUR"] = 1.0
    return table


def fetch(client: httpx.Client, currencies=CURRENCIES) -> pd.DataFrame:
    resp = client.get(URL.format(cur="+".join(currencies)))
    resp.raise_for_status()
    return parse_csv(resp.text)


def pair_closes(table: pd.DataFrame) -> dict[str, pd.Series]:
    out = {}
    for name, (base, quote) in PAIRS.items():
        s = (table[quote] / table[base]).dropna()
        out[name] = s
    return out


def to_klines(closes: pd.Series) -> list[Kline]:
    """Tageskurse -> Kerzen (Eröffnung = Vortagesschluss, Hoch/Tief aus beiden Werten)."""
    prev = closes.shift(1).fillna(closes)
    out = []
    for ts, o, c in zip(closes.index, prev, closes):
        out.append(Kline(open_time=int(ts.value // 1_000_000), open=float(o),
                         high=float(max(o, c)), low=float(min(o, c)), close=float(c),
                         base_vol=1.0, quote_vol=1.0))
    return out
