"""Schlanker async Client für die ÖFFENTLICHEN Bitunix-Futures-Marktdaten.

Phase 1: ausschließlich öffentliche Endpunkte, keine API-Keys, keine Orders.

Quelle: offizielle Bitunix OpenAPI-Doku
  https://www.bitunix.com/api-docs/futures/common/introduction.html
  - Basis-URL: https://fapi.bitunix.com
  - Öffentliche Endpunkte brauchen keine Authentifizierung
  - Antwortformat: {"code": 0, "msg": "Success", "data": ...}; code != 0 = Fehler
  - Rate-Limit Marktdaten: 10 req/sec/ip
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Iterable
from typing import Any, Literal

import httpx

from .models import FundingRate, Kline, Ticker

log = logging.getLogger(__name__)

BASE_URL = "https://fapi.bitunix.com"
MARKET_PATH = "/api/v1/futures/market"

# Laut Doku: 1m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w 1M
INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 5 * 60_000,
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 3_600_000,
    "2h": 2 * 3_600_000,
    "4h": 4 * 3_600_000,
    "6h": 6 * 3_600_000,
    "8h": 8 * 3_600_000,
    "12h": 12 * 3_600_000,
    "1d": 86_400_000,
    "3d": 3 * 86_400_000,
    "1w": 7 * 86_400_000,
    # "1M" hat keine feste Länge – wird unterstützt, aber nicht in INTERVAL_MS
}
VALID_INTERVALS = frozenset(INTERVAL_MS) | {"1M"}

KLINE_MAX_LIMIT = 200  # laut Doku

# Bitunix-Fehlercodes, bei denen sich ein erneuter Versuch lohnt
# (10001 Network Error, 10005 Too many requests, 10006 Request too frequently)
_RETRYABLE_API_CODES = {10001, 10005, 10006}
_RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}

PriceType = Literal["LAST_PRICE", "MARK_PRICE"]


class BitunixError(Exception):
    """Basisklasse für Fehler des Bitunix-Clients."""


class BitunixAPIError(BitunixError):
    """Die API hat mit einem Fehlercode (code != 0) oder HTTP-Fehler geantwortet."""

    def __init__(self, message: str, *, code: int | None = None, status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


class _RateLimiter:
    """Einfacher Limiter: höchstens `rate` Anfragen pro Sekunde (gleichmäßig verteilt)."""

    def __init__(self, rate: float):
        self._interval = 1.0 / rate
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
                now = time.monotonic()
            self._next = max(now, self._next) + self._interval


class BitunixPublicClient:
    """Async Client für öffentliche Futures-Marktdaten.

    Verwendung:
        async with BitunixPublicClient() as client:
            klines = await client.get_klines("BTCUSDT", "1h")
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 4,
        backoff_base: float = 0.5,
        rate_limit_per_sec: float = 8.0,  # etwas unter dem offiziellen Limit von 10/s
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "trading-assistant/0.1"},
        )
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._limiter = _RateLimiter(rate_limit_per_sec)

    async def __aenter__(self) -> BitunixPublicClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ intern

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET auf einen Markt-Endpunkt mit Rate-Limit, Retries und Backoff.

        Gibt das `data`-Feld der Antwort zurück.
        """
        path = f"{MARKET_PATH}/{endpoint}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        while True:
            attempt += 1
            await self._limiter.wait()
            try:
                resp = await self._client.get(path, params=params)
            except httpx.TransportError as e:
                err: BitunixError = BitunixError(f"Netzwerkfehler bei {path}: {e!r}")
                retryable = True
            else:
                err, retryable = self._check_response(path, resp)
                if err is None:
                    return resp.json()["data"]

            if not retryable or attempt > self._max_retries:
                log.error("Anfrage fehlgeschlagen (%s, Versuch %d): %s", path, attempt, err)
                raise err

            delay = self._backoff_base * 2 ** (attempt - 1) + random.uniform(0, 0.25)
            log.warning(
                "Anfrage an %s fehlgeschlagen (Versuch %d/%d): %s – neuer Versuch in %.1fs",
                path, attempt, self._max_retries + 1, err, delay,
            )
            await asyncio.sleep(delay)

    @staticmethod
    def _check_response(path: str, resp: httpx.Response) -> tuple[BitunixError | None, bool]:
        """Prüft HTTP-Status und Bitunix-`code`. Liefert (Fehler oder None, retrybar?)."""
        if resp.status_code != 200:
            return (
                BitunixAPIError(
                    f"HTTP {resp.status_code} bei {path}: {resp.text[:200]}",
                    status=resp.status_code,
                ),
                resp.status_code in _RETRYABLE_HTTP_STATUS,
            )
        try:
            body = resp.json()
        except ValueError:
            return BitunixAPIError(f"Ungültiges JSON bei {path}: {resp.text[:200]}"), True
        code = body.get("code")
        if code != 0:
            return (
                BitunixAPIError(f"API-Fehler {code} bei {path}: {body.get('msg')}", code=code),
                code in _RETRYABLE_API_CODES,
            )
        return None, False

    # ---------------------------------------------------------------- öffentlich

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        *,
        limit: int = KLINE_MAX_LIMIT,
        end_time: int | None = None,
        price_type: PriceType = "LAST_PRICE",
    ) -> list[Kline]:
        """Lädt bis zu `limit` (max. 200) ABGESCHLOSSENE Kerzen vor `end_time`.

        `end_time` (ms) ist laut Doku exklusiv; ohne `end_time` gibt es die
        neuesten abgeschlossenen Kerzen. Ergebnis ist aufsteigend nach Zeit sortiert.
        """
        if interval not in VALID_INTERVALS:
            raise ValueError(f"Ungültiges Intervall {interval!r}, erlaubt: {sorted(VALID_INTERVALS)}")
        if not 1 <= limit <= KLINE_MAX_LIMIT:
            raise ValueError(f"limit muss zwischen 1 und {KLINE_MAX_LIMIT} liegen")
        data = await self._get(
            "kline",
            {
                "symbol": symbol,
                "interval": interval,
                "limit": limit,
                "endTime": end_time,
                "type": price_type,
            },
        )
        klines = [Kline.from_api(d) for d in data or []]
        klines.sort(key=lambda k: k.open_time)  # API liefert neueste zuerst
        return klines

    async def get_klines_since(
        self,
        symbol: str,
        interval: str,
        start_time: int,
        *,
        price_type: PriceType = "LAST_PRICE",
        max_pages: int = 100,
    ) -> list[Kline]:
        """Lädt alle abgeschlossenen Kerzen mit open_time >= `start_time` (ms).

        Blättert dazu seitenweise (je 200) von jetzt rückwärts.
        Ergebnis aufsteigend sortiert, ohne Duplikate.
        """
        result: dict[int, Kline] = {}
        end_time: int | None = None
        for _ in range(max_pages):
            page = await self.get_klines(
                symbol, interval, limit=KLINE_MAX_LIMIT, end_time=end_time, price_type=price_type
            )
            if not page:
                break
            for k in page:
                if k.open_time >= start_time:
                    result[k.open_time] = k
            oldest = page[0].open_time
            if oldest <= start_time or len(page) < KLINE_MAX_LIMIT:
                break
            if end_time is not None and oldest >= end_time:
                break  # Schutz gegen Endlosschleife
            end_time = oldest
        else:
            log.warning("get_klines_since: max_pages=%d erreicht für %s %s", max_pages, symbol, interval)
        return [result[t] for t in sorted(result)]

    async def get_tickers(self, symbols: Iterable[str] | None = None) -> list[Ticker]:
        """24h-Ticker; ohne `symbols` für alle Kontrakte."""
        params = {"symbols": ",".join(symbols)} if symbols else None
        data = await self._get("tickers", params)
        return [Ticker.from_api(d) for d in data or []]

    async def get_funding_rate(self, symbol: str) -> FundingRate:
        """Aktuelle Funding Rate für ein Symbol."""
        data = await self._get("funding_rate", {"symbol": symbol})
        # Live kommt ein Objekt, das Doku-Beispiel zeigt eine Liste – beides akzeptieren.
        if isinstance(data, list):
            if not data:
                raise BitunixAPIError(f"Keine Funding-Daten für {symbol}")
            data = data[0]
        return FundingRate.from_api(data)

    async def get_funding_rates(self) -> list[FundingRate]:
        """Aktuelle Funding Rates aller Kontrakte (Batch-Endpunkt)."""
        data = await self._get("funding_rate/batch")
        return [FundingRate.from_api(d) for d in data or []]
