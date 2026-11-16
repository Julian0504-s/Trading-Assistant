"""Strategien: Welches Signal eröffnet einen Trade, wo liegen Stop und Ziel?

Gemeinsames Interface für Backtest, Paper Trading und (ab Phase 4) Live.
Eine Strategie entscheidet nur anhand von Daten bis zur aktuellen,
abgeschlossenen Kerze; ausgeführt wird zur nächsten Kerze (kein Blick in die Zukunft).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from signals.models import Signal

CANDLE_RULES = ("ema_cross", "ema200_trend", "rsi_extreme", "bb_breakout")


@dataclass(frozen=True)
class ExitConfig:
    """Stop und Ziel als Vielfaches der ATR 14 zum Einstiegszeitpunkt."""

    stop_atr: float = 1.5
    target_atr: float = 3.0  # Chance-Risiko 1:2


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    description: str
    rules: frozenset[str]
    min_strength: int = 2
    with_trend_only: bool = False
    exits: ExitConfig = field(default_factory=ExitConfig)

    def pick(self, signals: list[Signal], row: pd.Series) -> Signal | None:
        """Wählt aus den Signalen einer Kerze das zu handelnde (stärkstes zuerst)."""
        candidates = [s for s in signals
                      if s.rule in self.rules and s.strength >= self.min_strength]
        if self.with_trend_only:
            ema200 = row.get("ema_200")
            if ema200 is None or pd.isna(ema200):
                return None
            trend = "long" if row["close"] > ema200 else "short"
            candidates = [s for s in candidates if s.direction == trend]
        if not candidates:
            return None
        # Bei widersprüchlichen Signalen an derselben Kerze: nichts tun
        if len({s.direction for s in candidates}) > 1:
            return None
        return max(candidates, key=lambda s: s.strength)

    def stop_and_target(self, direction: str, entry: float, atr: float) -> tuple[float, float]:
        sign = 1 if direction == "long" else -1
        return (entry - sign * self.exits.stop_atr * atr,
                entry + sign * self.exits.target_atr * atr)


STRATEGIES: dict[str, Strategy] = {
    s.name: s for s in (
        Strategy(
            name="trend",
            label="Trendfolge",
            description="EMA-Cross, EMA-200-Trend und Bollinger-Breakout ab Stärke mittel, "
                        "nur in Richtung des Trends (Kurs über/unter EMA 200).",
            rules=frozenset({"ema_cross", "ema200_trend", "bb_breakout"}),
            min_strength=2,
            with_trend_only=True,
        ),
        Strategy(
            name="all_medium",
            label="Alle ab mittel",
            description="Jede Kerzen-Regel ab Stärke mittel, auch Gegentrend (RSI).",
            rules=frozenset(CANDLE_RULES),
            min_strength=2,
        ),
        Strategy(
            name="strong",
            label="Nur stark",
            description="Jede Kerzen-Regel, nur Stärke stark.",
            rules=frozenset(CANDLE_RULES),
            min_strength=3,
        ),
    )
}
