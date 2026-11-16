"""Daten für das Dashboard – ohne Streamlit, damit testbar."""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass

import pandas as pd

from bitunix.client import BitunixError, BitunixPublicClient
from bitunix.models import FundingRate, Ticker
from data.store import CandleStore
from data.sync import sync_candles
from indicators import add_all
from signals import DIRECTION_LABEL, STRENGTH_LABEL, SignalStore
from signals.scan import scan_watchlist

RULE_LABEL = {
    "ema_cross": "EMA-Cross",
    "ema200_trend": "EMA-200-Trend",
    "rsi_extreme": "RSI extrem",
    "bb_breakout": "Bollinger-Breakout",
    "donchian_breakout": "Donchian-Ausbruch",
    "funding_extreme": "Funding extrem",
}


@dataclass
class LiveData:
    tickers: dict[str, Ticker]
    funding: dict[str, FundingRate]
    error: str | None = None


async def _fetch_live(symbols: list[str]) -> LiveData:
    async with BitunixPublicClient() as client:
        tickers = await client.get_tickers(symbols)
        funding = await client.get_funding_rates()
    wanted = set(symbols)
    return LiveData(
        tickers={t.symbol: t for t in tickers},
        funding={f.symbol: f for f in funding if f.symbol in wanted},
    )


def fetch_live(symbols: list[str]) -> LiveData:
    """Ticker + Funding live von Bitunix; bei Fehlern leere Daten mit Fehlertext."""
    try:
        return asyncio.run(_fetch_live(symbols))
    except BitunixError as e:
        return LiveData({}, {}, error=str(e))


def candles_with_indicators(db: str, symbol: str, timeframe: str) -> pd.DataFrame:
    with CandleStore(db) as store:
        df = store.load_df(symbol, timeframe)
    return add_all(df) if not df.empty else df


def _trend_cell(df: pd.DataFrame) -> str:
    if df.empty or pd.isna(df["ema_200"].iloc[-1]):
        return "–"
    return "▲" if df["close"].iloc[-1] > df["ema_200"].iloc[-1] else "▼"


def overview(
    db: str, symbols: list[str], timeframes: list[str], rsi_timeframe: str, live: LiveData
) -> pd.DataFrame:
    """Marktübersicht: eine Zeile pro Symbol."""
    rows = []
    for sym in symbols:
        frames = {tf: candles_with_indicators(db, sym, tf) for tf in timeframes}
        t = live.tickers.get(sym)
        fr = live.funding.get(sym)
        last_close = next((f["close"].iloc[-1] for f in frames.values() if not f.empty), math.nan)
        rsi_df = frames.get(rsi_timeframe)
        row = {
            "Symbol": sym,
            "Preis": t.last_price if t else last_close,
            "24h %": t.change_24h_pct if t and t.change_24h_pct is not None else math.nan,
        }
        for tf in timeframes:
            row[f"Trend {tf}"] = _trend_cell(frames[tf])
        row[f"RSI {rsi_timeframe}"] = (
            float(rsi_df["rsi_14"].iloc[-1]) if rsi_df is not None and not rsi_df.empty
            else math.nan
        )
        row["Funding %"] = fr.funding_rate * 100 if fr else math.nan
        row["Funding-Intervall"] = f"{fr.funding_interval_h}h" if fr and fr.funding_interval_h else ""
        rows.append(row)
    return pd.DataFrame(rows)


def signals_frame(db: str, limit: int = 2000) -> pd.DataFrame:
    """Gespeicherte Signale als DataFrame, neueste zuerst."""
    with SignalStore(db) as store:
        sigs = store.recent(limit)
    df = pd.DataFrame(
        [
            {
                "Zeit": pd.Timestamp(s.candle_time, unit="ms", tz="UTC"),
                "Symbol": s.symbol,
                "Timeframe": s.timeframe,
                "Regel": RULE_LABEL.get(s.rule, s.rule),
                "Richtung": ("▲ " if s.direction == "long" else "▼ ") + DIRECTION_LABEL[s.direction],
                "Stärke": STRENGTH_LABEL[s.strength],
                "Stärke_n": s.strength,
                "Begründung": s.reason,
                "Preis": s.price,
                "direction": s.direction,
                "rule": s.rule,
            }
            for s in sigs
        ]
    )
    if df.empty:
        df = pd.DataFrame(columns=["Zeit", "Symbol", "Timeframe", "Regel", "Richtung", "Stärke",
                                   "Stärke_n", "Begründung", "Preis", "direction", "rule"])
    return df


async def _refresh(db: str, symbols: list[str], timeframes: list[str]) -> tuple[int, list[str]]:
    errors: list[str] = []
    with CandleStore(db) as candles, SignalStore(db) as store:
        async with BitunixPublicClient() as client:
            for sym in symbols:
                for tf in timeframes:
                    try:
                        await sync_candles(client, candles, sym, tf)
                    except BitunixError as e:
                        errors.append(f"{sym} {tf}: {e}")
            result = await scan_watchlist(client, candles, store, symbols, timeframes)
    return len(result.new), errors + result.errors


def refresh(db: str, symbols: list[str], timeframes: list[str]) -> tuple[int, list[str]]:
    """Kerzen nachladen und Signale neu berechnen. Gibt (neue Signale, Fehler) zurück."""
    return asyncio.run(_refresh(db, symbols, timeframes))


# ------------------------------------------------------------- Karten-Übersicht

# Auf 1m–5m entstehen sehr viele Signale – in Übersichten standardmäßig ausgeblendet
SHORT_TIMEFRAMES = ("1m", "3m", "5m")

TREND_GROUPS = (
    ("kurzfristig", ("1m", "3m", "5m")),
    ("mittelfristig", ("15m", "1h")),
    ("langfristig", ("4h", "1d")),
)


def trend_word(arrows: list[str]) -> str:
    """Fasst ▲/▼ mehrerer Timeframes zusammen: aufwärts / abwärts / gemischt."""
    known = [a for a in arrows if a in ("▲", "▼")]
    if not known:
        return "–"
    if all(a == "▲" for a in known):
        return "aufwärts"
    if all(a == "▼" for a in known):
        return "abwärts"
    return "gemischt"


def rsi_word(value: float) -> str:
    if value is None or math.isnan(value):
        return "–"
    if value >= 70:
        return "überkauft"
    if value <= 30:
        return "überverkauft"
    return "neutral"


def funding_word(rate_fraction: float | None, extreme_per_8h: float, interval_h: int = 8) -> str:
    """normal (< 0,01 %), erhöht oder extrem (ab Signal-Schwelle), je pro Intervall."""
    if rate_fraction is None or math.isnan(rate_fraction):
        return "–"
    extreme = extreme_per_8h * interval_h / 8
    a = abs(rate_fraction)
    if a >= extreme:
        return "extrem"
    if a >= 0.0001 * interval_h / 8:
        return "erhöht"
    return "normal"


def symbol_cards(
    db: str,
    symbols: list[str],
    timeframes: list[str],
    live: LiveData,
    signals: pd.DataFrame,
    *,
    rsi_timeframe: str = "1h",
    funding_extreme_per_8h: float = 0.0005,
) -> list[dict]:
    """Eine leicht lesbare Zusammenfassung pro Symbol für die Übersicht."""
    cards = []
    for sym in symbols:
        frames = {tf: candles_with_indicators(db, sym, tf) for tf in timeframes}
        arrows = {tf: _trend_cell(frames[tf]) for tf in timeframes}
        t = live.tickers.get(sym)
        fr = live.funding.get(sym)
        last_close = next((f["close"].iloc[-1] for f in frames.values() if not f.empty), math.nan)
        rsi_df = frames.get(rsi_timeframe)
        rsi_val = (float(rsi_df["rsi_14"].iloc[-1])
                   if rsi_df is not None and not rsi_df.empty else math.nan)
        groups = []
        for name, tfs in TREND_GROUPS:
            present = [tf for tf in tfs if tf in arrows]
            if present:
                groups.append({"name": name, "tfs": present,
                               "word": trend_word([arrows[tf] for tf in present])})
        interval_h = (fr.funding_interval_h or 8) if fr else 8
        rate = fr.funding_rate if fr else None
        sig = signals[(signals["Symbol"] == sym) & (signals["Stärke_n"] >= 2)
                      & ~signals["Timeframe"].isin(SHORT_TIMEFRAMES)].head(3) \
            if not signals.empty else signals
        cards.append({
            "symbol": sym,
            "price": t.last_price if t else last_close,
            "change_24h": t.change_24h_pct if t and t.change_24h_pct is not None else math.nan,
            "arrows": arrows,
            "groups": groups,
            "rsi": rsi_val,
            "rsi_word": rsi_word(rsi_val),
            "rsi_timeframe": rsi_timeframe,
            "funding_pct": rate * 100 if rate is not None else math.nan,
            "funding_word": funding_word(rate, funding_extreme_per_8h, interval_h),
            "funding_interval_h": interval_h,
            "signals": sig.to_dict("records"),
        })
    return cards


# ------------------------------------------------------------ Backtest & Paper

def latest_backtest(out_dir: str = "data/backtests") -> tuple[str | None, list[dict]]:
    """Neuester Backtest-Lauf: (Zeitstempel, Liste der Ergebnisse inkl. Kontoverlauf)."""
    import json
    from pathlib import Path

    base = Path(out_dir)
    marker = base / "latest.txt"
    if not marker.exists():
        return None, []
    stamp = marker.read_text().strip()
    summary_path = base / stamp / "summary.json"
    if not summary_path.exists():
        return stamp, []
    runs = json.loads(summary_path.read_text())
    for r in runs:
        eq_path = base / stamp / f"{r['strategy']}_{r['timeframe']}" / "equity.csv"
        if eq_path.exists():
            eq = pd.read_csv(eq_path, index_col=0, parse_dates=True)["Kontostand"]
            step = max(1, len(eq) // 600)  # für die Anzeige ausdünnen
            r["equity"] = eq.iloc[::step]
        else:
            r["equity"] = pd.Series(dtype="float64")
    return stamp, runs


def backtest_table(runs: list[dict]) -> pd.DataFrame:
    rows = []
    for r in runs:
        m = r["metrics"]
        rows.append({"Strategie": r["label"], "Timeframe": r["timeframe"],
                     "Zeitraum (Tage)": m["Tage"], "Trades": m["Trades"],
                     "Trefferquote %": m["Trefferquote %"], "Rendite %": m["Rendite %"],
                     "Max. Drawdown %": m["Max. Drawdown %"], "Profitfaktor": m["Profitfaktor"],
                     "Ø R pro Trade": m["Ø R pro Trade"], "Gebühren USDT": m["Gebühren"]})
    return pd.DataFrame(rows)


def paper_overview(db: str) -> list[dict]:
    """Paper-Konten mit Kennzahlen, offenen Positionen, Trades und Kontoverlauf."""
    from strategy.paper import PaperStore

    out = []
    with PaperStore(db) as ps:
        for _, a in ps.accounts().iterrows():
            name = a["name"]
            trades = ps.trades(name)
            eq = ps.equity(name)
            last = float(eq.iloc[-1]) if len(eq) else float(a["cash"])
            wins = int((trades["pnl"] > 0).sum()) if len(trades) else 0
            out.append({
                "name": name, "strategy": a["strategy"], "timeframes": a["timeframes"],
                "start_equity": float(a["start_equity"]), "equity_now": last,
                "return_pct": (last / a["start_equity"] - 1) * 100,
                "trades": len(trades), "win_rate": 100 * wins / len(trades) if len(trades) else math.nan,
                "since": pd.Timestamp(int(a["created_at"]), unit="ms", tz="UTC"),
                "positions": ps.positions(name), "closed": trades, "equity": eq,
            })
    return out
