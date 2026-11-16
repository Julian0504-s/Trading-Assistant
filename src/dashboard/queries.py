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
