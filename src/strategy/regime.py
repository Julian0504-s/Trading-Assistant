"""Markt-Regime: Hilfsspalten, die mehrere Symbole betreffen (z. B. BTC-Trend)."""

from __future__ import annotations

import pandas as pd

BTC = "BTCUSDT"


def add_btc_regime(frames: dict[str, pd.DataFrame], btc: str = BTC) -> dict[str, pd.DataFrame]:
    """Ergänzt jede Tabelle um `btc_up`: True, wenn BTC zur selben (abgeschlossenen)
    Kerze über seiner EMA 200 schließt. Erwartet Indikatorspalten (add_all) bei BTC.
    Ohne BTC-Daten bleibt die Spalte leer (NaN) – der Filter handelt dann nicht."""
    ref = frames.get(btc)
    if ref is None or "ema_200" not in ref:
        up = None
    else:
        up = (ref["close"] > ref["ema_200"]).where(ref["ema_200"].notna())
    out = {}
    for sym, df in frames.items():
        df = df.copy()
        df["btc_up"] = (up.reindex(df.index, method="ffill") if up is not None
                        else pd.Series(float("nan"), index=df.index))
        out[sym] = df
    return out
