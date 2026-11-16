"""Strategien: Welches Signal eröffnet einen Trade, wo liegen Stop und Ziel?

Gemeinsames Interface für Backtest, Paper Trading und (ab Phase 4) Live.
Eine Strategie entscheidet nur anhand von Daten bis zur aktuellen,
abgeschlossenen Kerze; ausgeführt wird zur nächsten Kerze (kein Blick in die Zukunft).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from signals.models import Signal

CANDLE_RULES = ("ema_cross", "ema200_trend", "rsi_extreme", "bb_breakout")


@dataclass(frozen=True)
class ExitConfig:
    """Stop und Ziel als Vielfaches der ATR 14 zum Einstiegszeitpunkt.

    target_atr=None: kein festes Ziel. trail_atr: Trailing-Stop, der dem Kurs im
    Abstand von trail_atr × ATR folgt (nur in Gewinnrichtung).
    """

    stop_atr: float = 1.5
    target_atr: float | None = 3.0  # Chance-Risiko 1:2
    trail_atr: float | None = None

    @property
    def label(self) -> str:
        parts = [f"Stop {self.stop_atr:g} ATR"]
        if self.target_atr is not None:
            parts.append(f"Ziel {self.target_atr:g} ATR")
        if self.trail_atr is not None:
            parts.append(f"Trailing {self.trail_atr:g} ATR")
        return ", ".join(parts)


@dataclass(frozen=True)
class Strategy:
    name: str
    label: str
    description: str
    rules: frozenset[str]
    min_strength: int = 2
    with_trend_only: bool = False
    adx_min: float = 0.0          # nur handeln, wenn ADX 14 mindestens so hoch ist
    exits: ExitConfig = field(default_factory=ExitConfig)
    long_only: bool = False       # keine Short-Trades
    btc_filter: bool = False      # Long nur, wenn BTC über EMA 200 (Short nur darunter)
    pyramid_max: int = 0          # max. Nachkäufe pro Position (0 = aus)
    pyramid_step_atr: float = 2.0 # Nachkauf, wenn Kurs so viele ATR weitergelaufen ist

    def pick(self, signals: list[Signal], row: pd.Series) -> Signal | None:
        """Wählt aus den Signalen einer Kerze das zu handelnde (stärkstes zuerst)."""
        candidates = [s for s in signals
                      if s.rule in self.rules and s.strength >= self.min_strength]
        if self.long_only:
            candidates = [s for s in candidates if s.direction == "long"]
        if self.btc_filter:
            btc_up = row.get("btc_up")
            if btc_up is None or pd.isna(btc_up):
                return None
            allowed = "long" if btc_up else "short"
            candidates = [s for s in candidates if s.direction == allowed]
        if self.with_trend_only:
            ema200 = row.get("ema_200")
            if ema200 is None or pd.isna(ema200):
                return None
            trend = "long" if row["close"] > ema200 else "short"
            candidates = [s for s in candidates if s.direction == trend]
        if self.adx_min > 0:
            adx = row.get("adx_14")
            if adx is None or pd.isna(adx) or adx < self.adx_min:
                return None
        if not candidates:
            return None
        # Bei widersprüchlichen Signalen an derselben Kerze: nichts tun
        if len({s.direction for s in candidates}) > 1:
            return None
        return max(candidates, key=lambda s: s.strength)

    def stop_and_target(self, direction: str, entry: float, atr: float) -> tuple[float, float]:
        sign = 1 if direction == "long" else -1
        stop = entry - sign * self.exits.stop_atr * atr
        if self.exits.target_atr is None:
            return stop, sign * math.inf
        return stop, entry + sign * self.exits.target_atr * atr


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
            name="trend_trail",
            label="Trendfolge mit Trailing-Stop",
            description="Wie Trendfolge, aber ohne festes Ziel: Stop 3 ATR, danach "
                        "Trailing-Stop im Abstand von 4 ATR – Gewinner dürfen laufen. "
                        "Ergebnis der Forschung (Entwicklung bis 11/2025, Prüfung danach).",
            rules=frozenset({"ema_cross", "ema200_trend", "bb_breakout"}),
            min_strength=2,
            with_trend_only=True,
            exits=ExitConfig(stop_atr=3.0, target_atr=None, trail_atr=4.0),
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
