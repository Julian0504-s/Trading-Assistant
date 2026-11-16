"""Watchlist scannen: Kerzen aus der DB + Funding live → neue Signale speichern."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from bitunix.client import BitunixError, BitunixPublicClient
from data.store import CandleStore
from indicators import add_all

from .models import Signal
from .rules import FUNDING_EXTREME_PER_8H, evaluate_candles, funding_extreme
from .store import SignalStore

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    new: list[Signal] = field(default_factory=list)
    # pro (symbol, timeframe): open_time der neuesten Kerze – um "frische" Signale
    # von nachgetragenen historischen zu unterscheiden
    latest_candle: dict[tuple[str, str], int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def is_fresh(self, s: Signal) -> bool:
        """Signal an der neuesten abgeschlossenen Kerze (oder Funding)?"""
        if s.timeframe == "funding":
            return True
        return self.latest_candle.get((s.symbol, s.timeframe)) == s.candle_time


async def scan_watchlist(
    client: BitunixPublicClient | None,
    candles: CandleStore,
    signals: SignalStore,
    symbols: list[str],
    timeframes: list[str],
    *,
    funding_threshold_per_8h: float = FUNDING_EXTREME_PER_8H,
) -> ScanResult:
    """Wertet alle Regeln aus und speichert neue Signale.

    Ohne `client` werden keine Funding-Daten geholt (nur Kerzen-Regeln).
    """
    result = ScanResult()
    found: list[Signal] = []

    for sym in symbols:
        for tf in timeframes:
            df = candles.load_df(sym, tf)
            if df.empty:
                result.errors.append(f"{sym} {tf}: keine Kerzen in der DB")
                continue
            result.latest_candle[(sym, tf)] = int(df.index[-1].value // 1_000_000)
            found.extend(evaluate_candles(add_all(df), sym, tf))

    if client is not None:
        try:
            rates = await client.get_funding_rates()
        except BitunixError as e:
            result.errors.append(f"Funding: {e}")
            log.error("Funding-Daten nicht verfügbar: %s", e)
        else:
            wanted = set(symbols)
            for fr in rates:
                if fr.symbol in wanted:
                    s = funding_extreme(fr, funding_threshold_per_8h)
                    if s is not None:
                        found.append(s)

    result.new = signals.add(found)
    log.info("Scan: %d Signale ausgewertet, %d neu", len(found), len(result.new))
    return result
