"""Technische Indikatoren als reine Funktionen: DataFrame rein, Serie raus.

Erwartete Spalten im DataFrame: open, high, low, close, volume
(so liefert sie `data.store.CandleStore.load_df`). Zeilen aufsteigend nach Zeit.

Berechnung wie in gängigen Charting-Tools (z. B. TradingView):
- EMA: Start mit dem SMA der ersten `period` Werte, danach rekursiv mit
  alpha = 2 / (period + 1).
- RSI und ATR: Wilder-Glättung (alpha = 1 / period), Start mit dem SMA.
- Bollinger: Standardabweichung der Grundgesamtheit (ddof=0).

Am Anfang stehen NaN-Werte, bis genug Daten für den Indikator vorliegen
("Aufwärmphase"). Mehrteilige Indikatoren (MACD, Bollinger) liefern einen
DataFrame mit mehreren Spalten.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _seeded_ewm(values: pd.Series, period: int, alpha: float) -> pd.Series:
    """Exponentielle Glättung, Startwert = SMA der ersten `period` gültigen Werte."""
    if period < 1:
        raise ValueError("period muss >= 1 sein")
    out = pd.Series(np.nan, index=values.index, dtype="float64")
    valid = values.dropna()
    if len(valid) < period:
        return out
    seed_pos = values.index.get_loc(valid.index[period - 1])
    seeded = values.iloc[seed_pos:].astype("float64").copy()
    seeded.iloc[0] = valid.iloc[:period].mean()
    out.iloc[seed_pos:] = seeded.ewm(alpha=alpha, adjust=False).mean().to_numpy()
    return out


def sma(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
    """Einfacher gleitender Durchschnitt."""
    return df[column].rolling(period, min_periods=period).mean().rename(f"sma_{period}")


def ema(df: pd.DataFrame, period: int, column: str = "close") -> pd.Series:
    """Exponentieller gleitender Durchschnitt (SMA-Start)."""
    return _seeded_ewm(df[column], period, 2.0 / (period + 1)).rename(f"ema_{period}")


def rsi(df: pd.DataFrame, period: int = 14, column: str = "close") -> pd.Series:
    """Relative Strength Index nach Wilder (0–100)."""
    delta = df[column].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = _seeded_ewm(gain, period, 1.0 / period)
    avg_loss = _seeded_ewm(loss, period, 1.0 / period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - 100.0 / (1.0 + rs)
    # Sonderfälle: nur Gewinne -> 100, keine Bewegung -> 50
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_gain == 0) & (avg_loss == 0)), 50.0)
    out[avg_gain.isna()] = np.nan
    return out.rename(f"rsi_{period}")


def macd(
    df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9, column: str = "close"
) -> pd.DataFrame:
    """MACD-Linie (EMA fast − EMA slow), Signal-Linie (EMA der MACD) und Histogramm."""
    line = ema(df, fast, column) - ema(df, slow, column)
    sig = _seeded_ewm(line, signal, 2.0 / (signal + 1))
    return pd.DataFrame({"macd": line, "macd_signal": sig, "macd_hist": line - sig})


def true_range(df: pd.DataFrame) -> pd.Series:
    """True Range: max(high−low, |high−close_vorher|, |low−close_vorher|)."""
    prev_close = df["close"].shift(1)
    ranges = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    )
    return ranges.max(axis=1, skipna=True).rename("true_range")


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range nach Wilder."""
    return _seeded_ewm(true_range(df), period, 1.0 / period).rename(f"atr_{period}")


def bollinger(
    df: pd.DataFrame, period: int = 20, num_std: float = 2.0, column: str = "close"
) -> pd.DataFrame:
    """Bollinger Bands: Mitte (SMA), oberes/unteres Band, Bandbreite und %B."""
    mid = df[column].rolling(period, min_periods=period).mean()
    std = df[column].rolling(period, min_periods=period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = upper - lower
    with np.errstate(divide="ignore", invalid="ignore"):
        bandwidth = width / mid
        pct_b = (df[column] - lower) / width
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": upper, "bb_lower": lower,
         "bb_bandwidth": bandwidth, "bb_pct_b": pct_b.where(width != 0)}
    )


def volume_ratio(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Volumen im Verhältnis zum Durchschnitt der VORHERIGEN `period` Kerzen.

    Die aktuelle Kerze zählt nicht zum Durchschnitt, damit ein Ausreißer
    seine eigene Vergleichsbasis nicht anhebt. 3.0 = dreifaches Durchschnittsvolumen.
    """
    baseline = df["volume"].shift(1).rolling(period, min_periods=period).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = df["volume"] / baseline
    return ratio.where(baseline > 0).rename("volume_ratio")


def volume_spike(df: pd.DataFrame, period: int = 20, factor: float = 2.0) -> pd.Series:
    """True, wenn das Volumen mindestens `factor` × Durchschnitt beträgt."""
    return (volume_ratio(df, period) >= factor).rename("volume_spike")


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """Hängt alle Standard-Indikatoren aus der CLAUDE.md als Spalten an (Kopie)."""
    out = df.copy()
    for p in (20, 50, 200):
        out[f"ema_{p}"] = ema(df, p)
    out["rsi_14"] = rsi(df, 14)
    out = out.join(macd(df))
    out["atr_14"] = atr(df, 14)
    out = out.join(bollinger(df))
    out["volume_ratio"] = volume_ratio(df)
    out["volume_spike"] = volume_spike(df)
    return out
