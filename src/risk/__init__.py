"""Risk Manager (ab Phase 3): harte Limits für Hebel, Risiko pro Trade, Tagesverlust."""

from .manager import (
    HARD_MAX_LEVERAGE,
    HARD_MAX_RISK_PER_TRADE,
    MAINTENANCE_MARGIN,
    RiskConfig,
    RiskManager,
    RiskViolation,
    kill_switch_active,
)

__all__ = [
    "HARD_MAX_LEVERAGE",
    "HARD_MAX_RISK_PER_TRADE",
    "MAINTENANCE_MARGIN",
    "RiskConfig",
    "RiskManager",
    "RiskViolation",
    "kill_switch_active",
]
