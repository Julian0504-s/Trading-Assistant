# Krypto Trading Assistant (Bitunix)

Persönliches Hobbyprojekt: Marktbeobachtung und Signale für Bitunix USDT-Perpetuals.
Details und Phasenplan: siehe `CLAUDE.md`.

> Signale sind Hinweise, keine Anlageempfehlung.

## Stand: Phase 1 – Markt & Signale

- `src/bitunix/` – async Client für die **öffentlichen** Futures-Marktdaten
  (Kerzen, Ticker, Funding Rate). Kein API-Key nötig.
- `src/data/` – SQLite-Cache für Kerzen und inkrementelles Laden
- `src/indicators/` – EMA, SMA, RSI (Wilder), MACD, ATR (Wilder), Bollinger Bands,
  Volumen-Verhältnis/-Spikes als reine Funktionen (DataFrame rein, Serie raus);
  `add_all(df)` hängt alle Standard-Indikatoren an
- `src/signals/` – regelbasierte Signale mit Richtung, Stärke (1–3) und Begründung
  in Klartext, gespeichert in SQLite (Tabelle `signals`)
- `scripts/fetch_candles.py` – lädt Kerzen für Watchlist × Timeframes
  (Standard: 1m, 3m, 5m, 15m, 1h, 4h, 1d) in SQLite
- `scripts/show_indicators.py` – Terminal-Übersicht der aktuellen Indikatorwerte
- `scripts/run_signals.py` – Signale erzeugen, speichern und anzeigen

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

## Indikatoren ansehen

```bash
uv run python scripts/show_indicators.py
```

```python
from data.store import CandleStore
from indicators import add_all, rsi

with CandleStore("data/market.db") as store:
    df = store.load_df("BTCUSDT", "1h")
df = add_all(df)          # ema_20/50/200, rsi_14, macd*, atr_14, bb_*, volume_ratio/-spike
```

Berechnung wie in gängigen Charting-Tools: EMA mit SMA-Start, RSI/ATR mit
Wilder-Glättung, Bollinger mit Populations-Standardabweichung. Die ersten Werte
sind NaN, bis genug Kerzen vorliegen (EMA 200 braucht 200 Kerzen).

## Signale

```bash
uv run python scripts/run_signals.py --fetch           # Kerzen nachladen + Signale
uv run python scripts/run_signals.py --feed 30         # zusätzlich die 30 neuesten zeigen
```

| Regel           | Löst aus, wenn …                                              | Stärke-Bonus                         |
|-----------------|---------------------------------------------------------------|--------------------------------------|
| `ema_cross`     | EMA 20 kreuzt EMA 50 (Golden/Death Cross)                     | in Trendrichtung (EMA 200), Volumen ≥ 1,5× |
| `ema200_trend`  | Schluss wechselt die Seite der EMA 200 (Hysterese ±0,25 ATR)   | Abstand > 0,5 ATR, Volumen ≥ 1,5×    |
| `rsi_extreme`   | RSI 14 steigt über 70 / fällt unter 30                        | ab 80/20 mittel, ab 85/15 stark      |
| `bb_breakout`   | Schluss bricht aus dem Bollinger-Band **und** Volumen ≥ 2×    | Volumen ≥ 3×, in Trendrichtung       |
| `funding_extreme` | Funding ≥ ±0,05 % pro 8h (Kontra-Hinweis)                   | ab 2× Schwelle mittel, ab 4× stark   |

Ausgelöst wird beim Übergang, nicht solange ein Zustand anhält. Jedes Ereignis wird
nur einmal gespeichert (Symbol, Timeframe, Regel, Richtung, Kerze). Beim ersten Lauf
werden auch historische Signale aus den vorhandenen Kerzen nachgetragen.

**Funding-Einheiten:** Die aktuellen Funding-Endpunkte liefern Prozent, der
Historien-Endpunkt Brüche (in der Doku nicht angegeben, live geprüft). Intern wird
alles als Bruch gespeichert.

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
