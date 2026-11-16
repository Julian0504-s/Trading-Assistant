"""Datenmodell für Signale."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["long", "short"]

DIRECTION_LABEL = {"long": "bullisch", "short": "bärisch"}
STRENGTH_LABEL = {1: "schwach", 2: "mittel", 3: "stark"}


def now_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True, slots=True)
class Signal:
    """Ein regelbasiertes Signal – ein Hinweis, keine Anlageempfehlung.

    candle_time: Eröffnungszeit (ms, UTC) der Kerze, an der die Regel ausgelöst hat.
                 Bei Funding-Signalen: Zeitpunkt der nächsten Funding-Abrechnung.
    strength:    1 = schwach, 2 = mittel, 3 = stark
    """

    symbol: str
    timeframe: str
    rule: str
    direction: Direction
    strength: int
    reason: str
    price: float
    candle_time: int
    created_at: int = field(default_factory=now_ms)

    def __post_init__(self) -> None:
        if self.direction not in DIRECTION_LABEL:
            raise ValueError(f"Ungültige Richtung: {self.direction!r}")
        if self.strength not in STRENGTH_LABEL:
            raise ValueError(f"Stärke muss 1–3 sein, nicht {self.strength!r}")

    @property
    def key(self) -> tuple[str, str, str, str, int]:
        """Eindeutiger Schlüssel – dasselbe Ereignis wird nur einmal gespeichert."""
        return (self.symbol, self.timeframe, self.rule, self.direction, self.candle_time)
