# Krypto Trading Assistant (Bitunix)

Persönliches Hobbyprojekt: Marktbeobachtung und Signale für Bitunix USDT-Perpetuals.
Details und Phasenplan: siehe `CLAUDE.md`.

> Signale sind Hinweise, keine Anlageempfehlung.

## Stand: Phase 3 – Backtesting & Paper Trading (nur Simulation)

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
- `src/alerts/` – Telegram-Alerts mit Filter (Stärke, Timeframes) und Cooldown
- `src/dashboard/` – Streamlit-Dashboard: Marktübersicht, Detail-Chart, Signal-Feed
- `src/risk/` – Risk Manager mit harten Limits (max. 3x Hebel, max. 1 % Risiko pro
  Trade, Stop-Loss Pflicht, Tagesverlust-Limit, Kill-Switch)
- `src/strategy/` – Strategien, simuliertes Konto, Backtest und Paper Trading
- `scripts/backtest.py` – Strategien × Timeframes auf historischen Kerzen testen
- `scripts/paper_trade.py` – Paper Trading live (nur simulierte Orders)
- `scripts/run_signals.py` – Signale erzeugen, speichern, anzeigen (optional melden)
- `scripts/watch.py` – Dauerbetrieb: nachladen → Signale → Telegram, jede Minute

## Einrichtung

```bash
uv sync
cp .env.example .env   # optional: Watchlist / DB-Pfad anpassen
# Standard-Watchlist: BTC, ETH, SOL, XRP, DOGE, BNB, ADA, LINK, ZEC, AVAX (USDT-Perpetuals)
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

## Dashboard

```bash
uv run streamlit run src/dashboard/app.py
```

Öffnet sich im Browser (http://localhost:8501).

- **Marktübersicht:** Preis und 24h-Änderung (live), Trend pro Timeframe
  (▲/▼ = Schluss über/unter EMA 200), RSI 14 für einen wählbaren Timeframe, aktuelle
  Funding Rate, dazu die neuesten Signale.
- **Chart:** Kerzen mit EMA 20/50/200 und Bollinger Bands, darunter Volumen, RSI und
  MACD. Signale als ▲/▼ (Größe = Stärke, Begründung beim Darüberfahren).
- **Signal-Feed:** alle gespeicherten Signale, neueste zuerst, filterbar nach Symbol,
  Timeframe, Regel, Richtung und Stärke.
- „Daten aktualisieren“ in der Seitenleiste lädt Kerzen nach und berechnet Signale neu.
  Für Daten im Dauerbetrieb parallel `scripts/watch.py` laufen lassen.
- Steigend/fallend ist Blau/Rot (auch bei Rot-Grün-Schwäche unterscheidbar);
  helles und dunkles Theme haben eigene Farbstufen.

## Telegram-Alerts & Dauerbetrieb

1. Bei [@BotFather](https://t.me/BotFather) einen Bot anlegen und den Token kopieren.
2. Dem Bot eine Nachricht schreiben, dann `https://api.telegram.org/bot<TOKEN>/getUpdates`
   öffnen und `"chat":{"id": …}` ablesen.
3. In `.env` eintragen: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (siehe `.env.example`).

```bash
uv run python scripts/watch.py                 # läuft dauerhaft, Strg+C beendet
uv run python scripts/run_signals.py --fetch --alert   # einmaliger Durchlauf mit Alerts
```

- Gemeldet werden nur **aktuelle** Signale (letzte 3 Kerzen), nie die historisch nachgetragenen.
- Filter: `ALERT_MIN_STRENGTH` (Standard 2 = mittel), `ALERT_TIMEFRAMES` (leer = alle).
- Cooldown: gleiche Kombination aus Symbol, Timeframe, Regel und Richtung frühestens
  nach `ALERT_COOLDOWN_MIN` Minuten erneut (Standard 60). Wird in SQLite gespeichert,
  gilt also auch nach einem Neustart.
- Alle Signale eines Durchlaufs kommen gebündelt in **einer** Nachricht.
- Ohne Token läuft alles normal, es wird nur nichts verschickt.
- Der Token wird nie geloggt (steht in der URL, daher httpx-Logging auf WARNING und
  geschwärzte Fehlermeldungen).

## Backtest

```bash
uv run python scripts/fetch_candles.py --history   # einmalig: lange Historie (1h: 1 Jahr …)
uv run python scripts/backtest.py                  # alle Strategien × alle Timeframes
uv run python scripts/backtest.py --strategies trend --timeframes 4h,1d
```

- **Strategien:** `trend` (Trendfolge: EMA-Cross, EMA-200-Trend, Bollinger-Breakout ab
  „mittel“, nur in Trendrichtung), `trend_trail` (wie `trend`, aber Stop 3 ATR und
  Trailing-Stop 4 ATR statt festem Ziel), `all_medium` (alle Regeln ab „mittel“), `strong`
  (alle Regeln, nur „stark“).
- **Ausführung:** Signal am Kerzenschluss → Einstieg zur Eröffnung der nächsten Kerze.
  Stop 1,5 ATR, Ziel 3 ATR. Berührt eine Kerze beides, zählt der Stop. Gebühren 0,06 %
  und Slippage 0,02 % pro Seite.
- **Risiko:** 1 % des Kontos pro Trade, Hebel max. 3x über alle offenen Positionen,
  Tagesverlust-Limit 3 % (danach keine neuen Trades bis zum nächsten UTC-Tag).
- Ergebnisse in `data/backtests/<Zeitstempel>/`: Übersicht, Kontoverlauf, Order-Log als CSV.

## Strategie-Forschung

```bash
uv run python scripts/fetch_candles.py --symbols BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT,DOGEUSDT,BNBUSDT,ADAUSDT,LINKUSDT,ZECUSDT,AVAXUSDT \
    --timeframes 1h,4h,1d --history --history-target 1h=17520,4h=8760,1d=1500
uv run python scripts/research.py
```

45 Varianten pro Timeframe (Einstiegsregeln × ADX-Filter × Ausstiege inkl. Trailing-Stop)
auf 10 Coins. **Entwicklung** auf allen Daten vor dem letzten Jahr, Auswahl nach vorab
festgelegter Regel (genug Trades, Profitfaktor ≥ 1,1, bestes Verhältnis Jahresrendite /
Drawdown), **Prüfung** einmalig auf dem letzten Jahr. Bericht in `data/research/`.

Stand 16.11.2026: Nur auf 1d hat eine Variante die Prüfung bestanden
(`trend_trail`: Trendfolge mit Stop 3 ATR und Trailing-Stop 4 ATR, Prüfjahr +27 %,
max. Drawdown −12,5 %). 1h und 4h sind in der Prüfung durchgefallen.

## Ideen-Vergleich über Halbjahre (Walk-forward)

```bash
uv run python scripts/walkforward.py
```

Vergleicht vorab festgelegte Ideen mit der Basis `trend_trail@1d` – in jedem der letzten
4 Halbjahre (frisches Konto) und über den Gesamtzeitraum: nur Long, BTC-Filter (Long nur
bei BTC über EMA 200), Nachkauf bei Gewinnern (nur wenn die Position durch den Stop schon
ohne Verlustrisiko ist), 1d + 4h in einem Konto. Regel: besser in ≥ 3 von 4 Halbjahren
und Verhältnis Jahresrendite / Drawdown mindestens so gut wie die Basis.

Stand 16.11.2026: Nur „1d + 4h in einem Konto“ erfüllt die Regel (+100 % p.a. statt
+41 %, aber max. Drawdown −55 % statt −28 %). Der Mehrertrag kommt größtenteils von
mehr Risiko (mehr gleichzeitige Positionen).

## Daytrading-Test

```bash
uv run python scripts/fetch_candles.py --symbols <10 Coins> --timeframes 15m --history --history-target 15m=70080
uv run python scripts/daytrade.py
```

16 Varianten auf 15m/1h mit strengen Filtern (Trend auf eigenem Timeframe, 4h und 1d
gleichgerichtet, ADX ≥ 25, Schluss zum Tagesende), mit Market- oder Limit-Orders.
Stand 16.11.2026: **keine** Variante bestand die Auswahl – alle verloren im
Auswahljahr 35–99 % (bei 1 % Risiko pro Trade). Die 1d-Strategie bleibt die Basis.

## Paper Trading

```bash
uv run python scripts/paper_trade.py                       # läuft dauerhaft (PAPER_RUNS)
uv run python scripts/paper_trade.py --runs trend_trail@1d,trend_trail@1d+4h
# "1d+4h" = beide Timeframes in EINEM gemeinsamen Konto
uv run python scripts/paper_trade.py --status              # Kontostände
uv run python scripts/paper_trade.py --export orders.csv   # Order-Log (CSV, für die Steuer)
```

- Jedes `strategie@timeframe` ist ein eigenes Konto (Standard 1.000 USDT), gleiche Logik
  wie im Backtest. Zustand in SQLite, übersteht Neustarts. Beim ersten Start wird nicht
  rückwirkend gehandelt.
- Jede simulierte Order wird mit Grund, Preis, Größe und Zeit geloggt.
- Mit Telegram-Konfiguration kommt zu jedem Paper-Trade eine Nachricht.
- **Kill-Switch:** Datei `KILL_SWITCH` im Projektordner anlegen → alle Paper-Positionen
  werden geschlossen, der Bot hält an.
- **`DRY_RUN=true` ist Pflicht.** Es gibt keinen Code, der echte Orders sendet
  (Live-Trading erst in Phase 4 nach Freigabe).

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
