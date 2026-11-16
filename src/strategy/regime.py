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


def add_htf_trend(
    frames: dict[str, pd.DataFrame],
    timeframe: str,
    higher: dict[str, dict[str, pd.DataFrame]],
) -> dict[str, pd.DataFrame]:
    """Trend höherer Timeframes für Entscheidungen auf `timeframe`.

    `higher`: {"4h": {Symbol: df}, "1d": {...}} jeweils mit `ema_200`.
    Spalten `htf_up` / `htf_down`: True, wenn ALLE höheren Timeframes über bzw.
    unter ihrer EMA 200 schließen. Verwendet wird nur die zum Entscheidungs-
    zeitpunkt (Schluss der kleinen Kerze) bereits ABGESCHLOSSENE höhere Kerze.
    """
    from bitunix.client import INTERVAL_MS

    step = pd.Timedelta(milliseconds=INTERVAL_MS[timeframe])
    out = {}
    for sym, df in frames.items():
        decide = pd.DataFrame({"decide": df.index + step}, index=df.index)
        ups, downs = [], []
        for tf, by_sym in higher.items():
            h = by_sym.get(sym)
            if h is None or "ema_200" not in h:
                ups.append(pd.Series(False, index=df.index))
                downs.append(pd.Series(False, index=df.index))
                continue
            known = pd.DataFrame({
                "known": h.index + pd.Timedelta(milliseconds=INTERVAL_MS[tf]),
                "up": (h["close"] > h["ema_200"]) & h["ema_200"].notna(),
                "down": (h["close"] < h["ema_200"]) & h["ema_200"].notna(),
            })
            m = pd.merge_asof(decide.reset_index().sort_values("decide"),
                              known.sort_values("known"), left_on="decide",
                              right_on="known", direction="backward").set_index(df.index.name or "index")
            m.index = df.index
            ups.append(m["up"].fillna(False).astype(bool))
            downs.append(m["down"].fillna(False).astype(bool))
        df = df.copy()
        df["htf_up"] = pd.concat(ups, axis=1).all(axis=1)
        df["htf_down"] = pd.concat(downs, axis=1).all(axis=1)
        out[sym] = df
    return out
