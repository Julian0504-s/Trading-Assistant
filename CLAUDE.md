# Krypto Trading Assistant (Bitunix)

Persönliches Hobbyprojekt: Marktbeobachtung, Signale und Übersichten für Bitunix Futures (USDT-Perpetuals).
Später: Paper Trading, danach optional automatisches Trading mit kleinem Betrag.

## Phasen (strikt einhalten)

1. **Phase 1 – Markt & Signale (aktuell)**
   Nur öffentliche Marktdaten. KEINE privaten Endpunkte, KEINE Orders.
2. **Phase 2 – Konto lesen**
   Private Endpunkte nur lesend (Balance, Positionen, Trade-History).
3. **Phase 3 – Backtesting & Paper Trading**
   Strategien gegen historische Daten testen, dann live im Dry-Run (simulierte Orders, nur Logging).
4. **Phase 4 – Live Trading**
   Erst nach expliziter Freigabe durch mich. Kleiner Betrag, harte Risiko-Limits.

Nie Code für eine spätere Phase schreiben, solange ich die nicht freigebe.

## Tech-Stack

- Python 3.12, `uv` für Dependencies
- `httpx` (async) für die Bitunix-API
- `pandas` + eigene Indikator-Funktionen (EMA, RSI, MACD, ATR, Bollinger, Volumen)
- SQLite (über `sqlmodel` oder `sqlite3`) für Kerzen, Signale, später Trades
- Dashboard: Streamlit + Plotly (Candlestick-Charts)
- Alerts: Telegram-Bot
- Scheduler: `apscheduler` oder einfache asyncio-Loop

## Projektstruktur

```
src/
  bitunix/        # API-Client (public zuerst, private ab Phase 2)
  data/           # Laden & Speichern von Kerzen, Funding, Open Interest
  indicators/     # reine Funktionen: DataFrame rein, Serie raus
  signals/        # Signal-Regeln, jede liefert: Symbol, Richtung, Stärke, Begründung
  alerts/         # Telegram
  dashboard/      # Streamlit-App
  strategy/       # ab Phase 3: gemeinsames Interface für Backtest, Paper und Live
  risk/           # ab Phase 3: Risk Manager
tests/
```

## Bitunix-API

- Endpunkte, Signatur-Verfahren und Rate-Limits immer gegen die **offizielle Bitunix OpenAPI-Dokumentation** prüfen, nicht raten.
- Öffentliche Futures-Marktdaten: `https://fapi.bitunix.com/api/v1/futures/market/...` (kein Key nötig).
- `ccxt` unterstützt Bitunix nicht offiziell → eigener schlanker Client.
- Rate-Limits respektieren, Retries mit Backoff, Fehler sauber loggen.

## Phase 1 – Features

- **Watchlist** (konfigurierbar, z. B. BTCUSDT, ETHUSDT, SOLUSDT)
- **Kerzen** für 15m, 1h, 4h, 1d laden und in SQLite cachen
- **Indikatoren**: EMA 20/50/200, RSI 14, MACD, ATR, Bollinger Bands, Volumen-Spikes
- **Futures-Daten**: Funding Rate, Open Interest (falls verfügbar)
- **Signale** (regelbasiert, nachvollziehbar), z. B.:
  - EMA-Cross, Trend über/unter EMA 200
  - RSI überkauft/überverkauft
  - Breakout aus Bollinger-Band mit Volumen
  - Extreme Funding Rate
  Jedes Signal mit Begründung in Klartext und Zeitstempel speichern.
- **Dashboard**:
  - Marktübersicht: Watchlist mit Preis, 24h-Änderung, Trend pro Timeframe, RSI, Funding
  - Detail-Chart pro Symbol mit Indikatoren und Signal-Markern
  - Signal-Feed (neueste zuerst, filterbar)
- **Telegram-Alerts** für neue Signale (mit Cooldown gegen Spam)

## Sicherheitsregeln (nicht verhandelbar)

- API-Keys nur in `.env`, `.env` steht in `.gitignore`. Nie Keys loggen oder ausgeben.
- API-Keys ohne Auszahlungsrecht, wenn möglich IP-gebunden.
- Ab Phase 3: `DRY_RUN=true` ist Default. Live nur, wenn explizit `DRY_RUN=false` gesetzt ist.
- Ab Phase 4 hart im Code im Risk Manager durchsetzen:
  - max. Hebel: 3x
  - max. Risiko pro Trade: 1 % des Kontos
  - Stop-Loss bei jeder Order Pflicht
  - Tagesverlust-Limit → Bot stoppt sich selbst
  - Kill-Switch (Datei oder Telegram-Befehl), der alle Orders storniert und den Bot anhält
- Jede (auch simulierte) Order mit Grund, Preis, Größe und Zeit loggen; CSV-Export für die Steuer.

## Arbeitsweise

- Kleine Schritte, nach jedem Schritt lauffähig.
- Indikatoren und Signale mit Tests abdecken (bekannte Beispieldaten).
- Signale sind Hinweise, keine Anlageempfehlung. Im Dashboard dezent so kennzeichnen.
- Sprache für UI und Kommentare: Deutsch.
