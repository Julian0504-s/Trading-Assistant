# Krypto Trading Assistant (Bitunix)

Persönliches Hobbyprojekt: Marktbeobachtung und Signale für Bitunix USDT-Perpetuals.
Details und Phasenplan: siehe `CLAUDE.md`.

> Signale sind Hinweise, keine Anlageempfehlung.

## Stand: Phase 1 – Markt & Signale

- `src/bitunix/` – async Client für die **öffentlichen** Futures-Marktdaten
  (Kerzen, Ticker, Funding Rate). Kein API-Key nötig.
- `src/data/` – SQLite-Cache für Kerzen und inkrementelles Laden
- `scripts/fetch_candles.py` – lädt Kerzen für Watchlist × Timeframes
  (Standard: 1m, 3m, 5m, 15m, 1h, 4h, 1d) in SQLite

## Einrichtung

```bash
uv sync
cp .env.example .env   # optional: Watchlist / DB-Pfad anpassen
```

## Kerzen laden

```bash
uv run python scripts/fetch_candles.py                  # Watchlist + Timeframes aus .env
uv run python scripts/fetch_candles.py --timeframes 1h,4h
uv run python scripts/fetch_candles.py --symbols BTCUSDT --backfill 1000
```

Beim ersten Lauf werden pro Timeframe die letzten `--backfill` Kerzen geladen
(Standard: 500; bei 1m sind das nur gut 8 Stunden).
Danach lädt jeder Lauf nur noch fehlende Kerzen nach. Gespeichert werden nur
abgeschlossene Kerzen (so liefert sie die API).

## Tests

```bash
uv run pytest
```

## Bitunix-API

Quelle: offizielle Doku unter https://www.bitunix.com/api-docs/futures/
(früher openapidoc.bitunix.com).

| Daten       | Endpunkt                                    | Hinweise                                        |
|-------------|---------------------------------------------|-------------------------------------------------|
| Kerzen      | `GET /api/v1/futures/market/kline`          | max. 200 pro Abfrage, `endTime` exklusiv; `3m` undokumentiert, live getestet |
| Ticker      | `GET /api/v1/futures/market/tickers`        | `symbols=BTCUSDT,ETHUSDT`                       |
| Funding     | `GET /api/v1/futures/market/funding_rate`   | `symbol=BTCUSDT`                                |
| Funding alle| `GET /api/v1/futures/market/funding_rate/batch` |                                             |

Rate-Limit laut Doku: 10 Anfragen/s pro IP. Der Client drosselt auf 8/s und
wiederholt Anfragen bei Netzwerkfehlern, HTTP 429/5xx sowie den Bitunix-Codes
10001/10005/10006 mit exponentiellem Backoff.
