"""Farben für die Charts – je ein eigener Satz für helles und dunkles Theme.

Steigend/fallend ist eine Polarität -> divergierendes Paar Blau/Rot (statt
Grün/Rot: auch bei Rot-Grün-Schwäche unterscheidbar; Grün bleibt für Status).
Linien-Overlays nutzen feste Kategorie-Farben; Raster/Achsen sind zurückhaltend.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Palette:
    surface: str
    text: str
    text_secondary: str
    muted: str
    grid: str
    axis: str
    up: str
    down: str
    ema20: str
    ema50: str
    ema200: str
    band: str
    band_fill: str
    rsi: str
    macd: str
    macd_signal: str
    ref_line: str


LIGHT = Palette(
    surface="#fcfcfb", text="#0b0b0b", text_secondary="#52514e", muted="#898781",
    grid="#e1e0d9", axis="#c3c2b7",
    up="#2a78d6", down="#e34948",
    ema20="#eb6834", ema50="#1baf7a", ema200="#6250d6",
    band="#898781", band_fill="rgba(137,135,129,0.10)",
    rsi="#2a78d6", macd="#eb6834", macd_signal="#1baf7a", ref_line="#c3c2b7",
)

DARK = Palette(
    surface="#1a1a19", text="#f0efec", text_secondary="#c3c2b7", muted="#898781",
    grid="#2c2c2a", axis="#383835",
    up="#3987e5", down="#e66767",
    ema20="#d95926", ema50="#199e70", ema200="#9085e9",
    band="#898781", band_fill="rgba(137,135,129,0.14)",
    rsi="#3987e5", macd="#d95926", macd_signal="#199e70", ref_line="#383835",
)


def palette(theme_type: str | None) -> Palette:
    return DARK if theme_type == "dark" else LIGHT
