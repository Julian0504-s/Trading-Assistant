"""Strategien (ab Phase 3): gemeinsames Interface für Backtest, Paper Trading und Live.

Phase 3: nur Simulation. Es gibt hier keinen Code, der echte Orders sendet.
"""

from .account import Order, Position, SimAccount, Trade, export_orders_csv
from .backtest import BacktestResult, run_backtest
from .base import STRATEGIES, ExitConfig, Strategy

__all__ = [
    "STRATEGIES",
    "BacktestResult",
    "ExitConfig",
    "Order",
    "Position",
    "SimAccount",
    "Strategy",
    "Trade",
    "export_orders_csv",
    "run_backtest",
]
