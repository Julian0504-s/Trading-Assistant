"""Datenklassen für die öffentlichen Bitunix-Futures-Marktdaten.

Feldnamen und Bedeutungen laut offizieller Doku
(https://www.bitunix.com/api-docs/futures/market/...).
Die API liefert Zahlen als Strings; wir wandeln sie hier in float/int um.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


def _f(value: Any) -> float | None:
    """String/Zahl -> float, leere Werte -> None."""
    if value is None or value == "":
        return None
    return float(value)


def _i(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


@dataclass(frozen=True, slots=True)
class Kline:
    """Eine abgeschlossene Kerze. `open_time` = Eröffnungszeit in ms (UTC)."""

    open_time: int
    open: float
    high: float
    low: float
    close: float
    base_vol: float  # Volumen in Basis-Coin (z. B. BTC)
    quote_vol: float  # Umsatz in Quote-Währung (USDT)

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Kline:
        return cls(
            open_time=int(d["time"]),
            open=float(d["open"]),
            high=float(d["high"]),
            low=float(d["low"]),
            close=float(d["close"]),
            base_vol=float(d["baseVol"]),
            quote_vol=float(d["quoteVol"]),
        )


@dataclass(frozen=True, slots=True)
class Ticker:
    """24h-Ticker eines Kontrakts."""

    symbol: str
    last_price: float
    mark_price: float | None
    open_24h: float | None  # Preis vor 24h
    high_24h: float | None
    low_24h: float | None
    base_vol_24h: float | None
    quote_vol_24h: float | None

    @property
    def change_24h_pct(self) -> float | None:
        """Prozentuale Änderung über 24h, falls berechenbar."""
        if not self.open_24h:
            return None
        return (self.last_price / self.open_24h - 1.0) * 100.0

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Ticker:
        last = d.get("lastPrice") or d.get("last")
        return cls(
            symbol=d["symbol"],
            last_price=float(last),
            mark_price=_f(d.get("markPrice")),
            open_24h=_f(d.get("open")),
            high_24h=_f(d.get("high")),
            low_24h=_f(d.get("low")),
            base_vol_24h=_f(d.get("baseVol")),
            quote_vol_24h=_f(d.get("quoteVol")),
        )


@dataclass(frozen=True, slots=True)
class FundingRate:
    """Aktuelle Funding Rate.

    `funding_rate`, `max_funding_rate`, `min_funding_rate` sind hier als
    DEZIMALBRUCH gespeichert (0.0001 = 0,01 % pro Intervall).

    Achtung Einheiten: Die Doku nennt keine Einheit. Die Endpunkte für die
    AKTUELLE Rate (funding_rate, funding_rate/batch) liefern Prozent, der
    Historien-Endpunkt liefert Brüche. Belege (live geprüft): Historie ist bei
    BTC exakt auf ±0.0001 begrenzt, die aktuellen Werte liegen ~100× höher
    (z. B. ETH -0.003312) und das Limit ist 0.3 bzw. 0.375 – die üblichen
    Börsen-Limits in Prozent. Deshalb wird hier durch 100 geteilt.

    `funding_interval_h` = Abrechnungsintervall in Stunden.
    """

    symbol: str
    funding_rate: float
    mark_price: float | None
    last_price: float | None
    index_price: float | None
    next_funding_time: int | None  # ms
    funding_interval_h: int | None
    max_funding_rate: float | None
    min_funding_rate: float | None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> FundingRate:
        def pct(v: Any) -> float | None:
            x = _f(v)
            return None if x is None else x / 100.0

        return cls(
            symbol=d["symbol"],
            funding_rate=float(d["fundingRate"]) / 100.0,
            mark_price=_f(d.get("markPrice")),
            last_price=_f(d.get("lastPrice")),
            index_price=_f(d.get("indexPrice")),
            next_funding_time=_i(d.get("nextFundingTime")),
            funding_interval_h=_i(d.get("fundingInterval")),
            max_funding_rate=pct(d.get("maxFundingRate")),
            min_funding_rate=pct(d.get("minFundingRate")),
        )
