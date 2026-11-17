"""Risk Manager – gilt für Backtest, Paper Trading und (ab Phase 4) Live.

Harte Grenzen aus der CLAUDE.md, fest im Code. Eine Konfiguration darf sie nur
verschärfen, nie lockern:
  - max. Hebel 3x (Gesamt-Exposure / Kontostand)
  - max. Risiko pro Trade 1 % des Kontos
  - Stop-Loss bei jeder Order Pflicht
  - Tagesverlust-Limit -> keine neuen Trades mehr an diesem Tag
  - Kill-Switch-Datei -> alles schließen und anhalten
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

HARD_MAX_LEVERAGE = 3.0
HARD_MAX_RISK_PER_TRADE = 0.01
HARD_MAX_DAILY_LOSS = 0.05  # Obergrenze für das konfigurierbare Tagesverlust-Limit
RESEARCH_MAX_RISK_PER_TRADE = 0.05  # nur für Backtest-Vergleiche (for_research)
RESEARCH_MAX_LEVERAGE = 20.0        # nur für Backtest-Vergleiche (for_research)
MAINTENANCE_MARGIN = 0.005          # Erhaltungsmarge für die Liquidations-Simulation


class RiskViolation(Exception):
    """Eine Order verletzt eine Risikoregel und wird nicht ausgeführt."""


@dataclass(frozen=True)
class RiskConfig:
    max_leverage: float = 3.0
    risk_per_trade: float = 0.01
    daily_loss_limit: float = 0.03
    # Nur für Backtest-Vergleiche: erlaubt mehr Risiko pro Trade. Paper Trading und
    # Live lehnen solche Konfigurationen ab (siehe strategy.paper.PaperTrader).
    research_only: bool = False

    def __post_init__(self) -> None:
        max_lev = RESEARCH_MAX_LEVERAGE if self.research_only else HARD_MAX_LEVERAGE
        if not 0 < self.max_leverage <= max_lev:
            raise ValueError(f"max_leverage muss in (0, {max_lev}] liegen")
        max_risk = RESEARCH_MAX_RISK_PER_TRADE if self.research_only else HARD_MAX_RISK_PER_TRADE
        if not 0 < self.risk_per_trade <= max_risk:
            raise ValueError(f"risk_per_trade muss in (0, {max_risk}] liegen")
        max_daily = 1.0 if self.research_only else HARD_MAX_DAILY_LOSS
        if not 0 < self.daily_loss_limit <= max_daily:
            raise ValueError(f"daily_loss_limit muss in (0, {max_daily}] liegen")

    @classmethod
    def for_research(cls, risk_per_trade: float, **kw) -> RiskConfig:
        """Backtest-Vergleich mit höherem Risiko pro Trade (max. 5 %), optional mehr Hebel
        (max. 20x) und abgeschaltetem Tagesverlust-Limit (daily_loss_limit=1.0).
        NIE für Paper Trading oder Live – dort wird so eine Konfiguration abgelehnt."""
        return cls(risk_per_trade=risk_per_trade, research_only=True, **kw)


def _utc_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=UTC).strftime("%Y-%m-%d")


@dataclass
class RiskManager:
    config: RiskConfig = field(default_factory=RiskConfig)
    _day: str | None = None
    _day_start_equity: float = 0.0

    def size_position(
        self, equity: float, entry: float, stop: float | None, open_notional: float = 0.0
    ) -> float:
        """Positionsgröße (Menge im Basis-Coin), sodass beim Stop höchstens
        `risk_per_trade` des Kontos verloren geht – begrenzt durch den Hebel.

        Wirft RiskViolation, wenn kein gültiger Stop gesetzt ist oder kein
        Spielraum mehr beim Hebel bleibt.
        """
        if stop is None or stop <= 0 or stop == entry:
            raise RiskViolation("Stop-Loss fehlt oder ist ungültig – Order abgelehnt")
        if equity <= 0:
            raise RiskViolation("Kein Kapital mehr")
        qty = equity * self.config.risk_per_trade / abs(entry - stop)
        room = equity * self.config.max_leverage - open_notional
        if room <= 0:
            raise RiskViolation(f"Hebel-Limit {self.config.max_leverage:g}x erreicht")
        return min(qty, room / entry)

    def start_of_day(self, now_ms: int, equity: float) -> None:
        """Merkt sich den Kontostand zu Beginn jedes UTC-Tages."""
        day = _utc_day(now_ms)
        if day != self._day:
            self._day, self._day_start_equity = day, equity

    def daily_limit_hit(self, now_ms: int, equity: float) -> bool:
        """True, wenn der Tagesverlust das Limit erreicht hat."""
        self.start_of_day(now_ms, equity)
        if self._day_start_equity <= 0:
            return True
        loss = (self._day_start_equity - equity) / self._day_start_equity
        return loss >= self.config.daily_loss_limit


def kill_switch_active(path: str | None = None) -> bool:
    """Kill-Switch: existiert die Datei (Standard: ./KILL_SWITCH), wird alles gestoppt."""
    return Path(path or os.getenv("KILL_SWITCH_FILE", "KILL_SWITCH")).exists()
