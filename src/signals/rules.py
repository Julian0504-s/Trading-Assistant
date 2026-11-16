"""Signal-Regeln: regelbasiert und nachvollziehbar.

Kerzen-Regeln bekommen einen DataFrame mit Indikatorspalten (siehe
`indicators.add_all`) und liefern ein Signal für JEDE Kerze, an der die Regel
auslöst – nicht nur für die letzte. Das liefert die Historie für Chart-Marker;
Duplikate verhindert der eindeutige Schlüssel beim Speichern.

Ausgelöst wird immer beim ÜBERGANG (z. B. RSI steigt über 70), nicht in jeder
Kerze, in der der Zustand anhält.

Stärke 1–3 setzt sich aus einfachen, sichtbaren Bestätigungen zusammen
(z. B. Trend laut EMA 200, erhöhtes Volumen) – steht jeweils in der Begründung.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from bitunix.models import FundingRate

from .models import Signal

CandleRule = Callable[[pd.DataFrame, str, str], list[Signal]]

# Schwellen (bewusst als Konstanten, damit sie in der Begründung auftauchen)
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
VOLUME_SPIKE = 2.0       # Bollinger-Breakout braucht mind. 2× Ø-Volumen
VOLUME_CONFIRM = 1.5     # "erhöhtes Volumen" als Bonus bei anderen Regeln
# Funding: 0,05 % pro 8h (normal sind ~0,01 %); wird aufs Intervall umgerechnet
FUNDING_EXTREME_PER_8H = 0.0005


# ---------------------------------------------------------------- Hilfsfunktionen

def cross_above(a: pd.Series, b: pd.Series | float) -> pd.Series:
    """True an der Kerze, an der `a` von <= b auf > b wechselt."""
    b_prev = b.shift(1) if isinstance(b, pd.Series) else b
    res = (a > b) & (a.shift(1) <= b_prev)
    valid = a.notna() & a.shift(1).notna()
    if isinstance(b, pd.Series):
        valid &= b.notna() & b.shift(1).notna()
    return (res & valid).fillna(False).astype(bool)


def cross_below(a: pd.Series, b: pd.Series | float) -> pd.Series:
    """True an der Kerze, an der `a` von >= b auf < b wechselt."""
    neg_b = -b if isinstance(b, pd.Series) else -b
    return cross_above(-a, neg_b)


def fmt_price(x: float) -> str:
    """Preis mit sinnvoller Anzahl Nachkommastellen (≈ 6 signifikante Stellen)."""
    digits = len(str(int(abs(x)))) if abs(x) >= 1 else 0
    decimals = max(0, 6 - digits) if abs(x) >= 1 else 8
    s = f"{x:.{decimals}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def _ms(ts: pd.Timestamp) -> int:
    return int(ts.value // 1_000_000)


def _trend(row: pd.Series) -> str | None:
    """'long' wenn Close über EMA 200, 'short' wenn darunter, sonst None."""
    ema200 = row.get("ema_200")
    if ema200 is None or pd.isna(ema200):
        return None
    return "long" if row["close"] > ema200 else "short"


def _volume_note(row: pd.Series) -> tuple[int, str]:
    ratio = row.get("volume_ratio")
    if ratio is not None and not pd.isna(ratio) and ratio >= VOLUME_CONFIRM:
        return 1, f"Volumen {ratio:.1f}× über Ø"
    return 0, ""


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


# ------------------------------------------------------------------- Kerzen-Regeln

def ema_cross(df: pd.DataFrame, symbol: str, timeframe: str) -> list[Signal]:
    """EMA 20 kreuzt EMA 50 (Golden Cross / Death Cross).

    Stärke: 1 + Trend laut EMA 200 in gleicher Richtung + erhöhtes Volumen.
    """
    out: list[Signal] = []
    up = cross_above(df["ema_20"], df["ema_50"])
    down = cross_below(df["ema_20"], df["ema_50"])
    for ts in df.index[up | down]:
        row = df.loc[ts]
        direction = "long" if up.loc[ts] else "short"
        strength, notes = 1, []
        trend = _trend(row)
        if trend == direction:
            strength += 1
            notes.append("passt zum Trend (Kurs " + ("über" if trend == "long" else "unter")
                         + f" EMA 200 = {fmt_price(row['ema_200'])})")
        elif trend is not None:
            notes.append("gegen den übergeordneten Trend (EMA 200)")
        bonus, vol = _volume_note(row)
        strength += bonus
        name = "Golden Cross: EMA 20 kreuzt EMA 50 nach oben" if direction == "long" \
            else "Death Cross: EMA 20 kreuzt EMA 50 nach unten"
        reason = _join(
            f"{name} (EMA 20 = {fmt_price(row['ema_20'])}, EMA 50 = {fmt_price(row['ema_50'])})",
            *notes, vol,
        )
        out.append(Signal(symbol, timeframe, "ema_cross", direction, strength, reason,
                          float(row["close"]), _ms(ts)))
    return out


EMA200_HYSTERESIS_ATR = 0.25  # Kurs muss so viele ATR jenseits der EMA 200 schließen


def ema200_trend(df: pd.DataFrame, symbol: str, timeframe: str) -> list[Signal]:
    """Schlusskurs wechselt die Seite der EMA 200 (möglicher Trendwechsel).

    Mit Hysterese gegen Hin-und-her-Springen: Die Seite wechselt erst, wenn
    der Schlusskurs mehr als 0,25 ATR jenseits der EMA 200 schließt. Liegt er
    näher dran, gilt die bisherige Seite weiter.

    Stärke: 1 + erhöhtes Volumen + Schlusskurs mehr als 0,5 ATR von der EMA 200 entfernt.
    """
    out: list[Signal] = []
    band = EMA200_HYSTERESIS_ATR * df["atr_14"]
    above = df["close"] > df["ema_200"] + band
    below = df["close"] < df["ema_200"] - band
    side = pd.Series(np.where(above, 1.0, np.where(below, -1.0, np.nan)), index=df.index).ffill()
    prev = side.shift(1)
    changed = side.notna() & prev.notna() & (side != prev)
    for ts in df.index[changed]:
        row = df.loc[ts]
        direction = "long" if side.loc[ts] > 0 else "short"
        strength, notes = 1, []
        bonus, vol = _volume_note(row)
        strength += bonus
        dist = abs(row["close"] - row["ema_200"])
        atr = row["atr_14"]
        if dist > 0.5 * atr:
            strength += 1
            notes.append(f"deutlicher Abstand ({dist / atr:.1f} ATR)")
        where = "über" if direction == "long" else "unter"
        reason = _join(
            f"Schlusskurs {fmt_price(row['close'])} schließt {where} der EMA 200 "
            f"({fmt_price(row['ema_200'])}) – möglicher Trendwechsel",
            *notes, vol,
        )
        out.append(Signal(symbol, timeframe, "ema200_trend", direction, strength, reason,
                          float(row["close"]), _ms(ts)))
    return out


def rsi_extreme(df: pd.DataFrame, symbol: str, timeframe: str) -> list[Signal]:
    """RSI 14 steigt über 70 (überkauft → bärisch) oder fällt unter 30 (überverkauft → bullisch).

    Stärke nach Ausprägung: ab 80/20 mittel, ab 85/15 stark.
    """
    out: list[Signal] = []
    r = df["rsi_14"]
    over = cross_above(r, RSI_OVERBOUGHT)
    under = cross_below(r, RSI_OVERSOLD)
    for ts in df.index[over | under]:
        row = df.loc[ts]
        value = float(row["rsi_14"])
        if over.loc[ts]:
            direction = "short"
            extremeness = value - RSI_OVERBOUGHT
            text = f"RSI 14 steigt auf {value:.1f} (über {RSI_OVERBOUGHT:.0f}) – überkauft"
        else:
            direction = "long"
            extremeness = RSI_OVERSOLD - value
            text = f"RSI 14 fällt auf {value:.1f} (unter {RSI_OVERSOLD:.0f}) – überverkauft"
        strength = 3 if extremeness >= 15 else 2 if extremeness >= 10 else 1
        out.append(Signal(symbol, timeframe, "rsi_extreme", direction, strength,
                          text + ", Gegenbewegung möglich", float(row["close"]), _ms(ts)))
    return out


def bb_breakout(df: pd.DataFrame, symbol: str, timeframe: str) -> list[Signal]:
    """Schlusskurs bricht aus dem Bollinger-Band aus – nur mit Volumen-Spike (≥ 2× Ø).

    Stärke: 1 + Volumen ≥ 3× Ø + Ausbruch in Trendrichtung (EMA 200).
    """
    out: list[Signal] = []
    spike = df["volume_ratio"] >= VOLUME_SPIKE
    up = cross_above(df["close"], df["bb_upper"]) & spike
    down = cross_below(df["close"], df["bb_lower"]) & spike
    for ts in df.index[up | down]:
        row = df.loc[ts]
        direction = "long" if up.loc[ts] else "short"
        ratio = float(row["volume_ratio"])
        strength, notes = 1, []
        if ratio >= 3:
            strength += 1
        if _trend(row) == direction:
            strength += 1
            notes.append("in Trendrichtung (EMA 200)")
        if direction == "long":
            where, band_val = "über das obere", row["bb_upper"]
        else:
            where, band_val = "unter das untere", row["bb_lower"]
        reason = _join(
            f"Ausbruch {where} Bollinger-Band ({fmt_price(band_val)}) mit "
            f"{ratio:.1f}× Ø-Volumen, Schluss {fmt_price(row['close'])}",
            *notes,
        )
        out.append(Signal(symbol, timeframe, "bb_breakout", direction, strength, reason,
                          float(row["close"]), _ms(ts)))
    return out


CANDLE_RULES: list[CandleRule] = [ema_cross, ema200_trend, rsi_extreme, bb_breakout]


def evaluate_candles(df: pd.DataFrame, symbol: str, timeframe: str) -> list[Signal]:
    """Wendet alle Kerzen-Regeln an. `df` muss die Spalten aus `indicators.add_all` haben."""
    signals: list[Signal] = []
    for rule in CANDLE_RULES:
        signals.extend(rule(df, symbol, timeframe))
    signals.sort(key=lambda s: (s.candle_time, s.rule))
    return signals


# ------------------------------------------------------------------- Funding-Regel

def funding_extreme(
    fr: FundingRate, threshold_per_8h: float = FUNDING_EXTREME_PER_8H
) -> Signal | None:
    """Extreme Funding Rate – ein Kontra-Hinweis.

    Stark positiv: Longs zahlen viel, der Markt ist einseitig long → bärischer Hinweis.
    Stark negativ: umgekehrt → bullischer Hinweis.
    Die Schwelle gilt pro 8h und wird auf das Funding-Intervall des Kontrakts
    umgerechnet. Höchstens ein Signal pro Funding-Periode (Schlüssel = nächste
    Abrechnung). Stärke: ab 2× Schwelle mittel, ab 4× stark.
    """
    interval_h = fr.funding_interval_h or 8
    threshold = threshold_per_8h * interval_h / 8
    rate = fr.funding_rate
    if abs(rate) < threshold or fr.next_funding_time is None:
        return None
    direction = "short" if rate > 0 else "long"
    factor = abs(rate) / threshold
    strength = 3 if factor >= 4 else 2 if factor >= 2 else 1
    side = "Longs zahlen an Shorts – Markt einseitig long" if rate > 0 \
        else "Shorts zahlen an Longs – Markt einseitig short"
    reason = (
        f"Extreme Funding Rate {rate * 100:+.4f} % pro {interval_h}h "
        f"(Schwelle ±{threshold * 100:.4f} %, {factor:.1f}×): {side}"
    )
    price = fr.last_price or fr.mark_price or 0.0
    return Signal(fr.symbol, "funding", "funding_extreme", direction, strength, reason,
                  float(price), int(fr.next_funding_time))
