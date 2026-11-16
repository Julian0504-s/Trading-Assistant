"""Signal-Regeln: jedes Signal liefert Symbol, Richtung, Stärke und Begründung."""

from .models import DIRECTION_LABEL, STRENGTH_LABEL, Signal
from .rules import CANDLE_RULES, evaluate_candles, funding_extreme
from .store import SignalStore

__all__ = [
    "CANDLE_RULES",
    "DIRECTION_LABEL",
    "STRENGTH_LABEL",
    "Signal",
    "SignalStore",
    "evaluate_candles",
    "funding_extreme",
]
