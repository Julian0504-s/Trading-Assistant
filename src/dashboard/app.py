"""Streamlit-Dashboard: Marktübersicht, Detail-Chart, Signal-Feed.

Start:  uv run streamlit run src/dashboard/app.py

Nur öffentliche Marktdaten (Phase 1). Signale sind Hinweise, keine Anlageempfehlung.
"""

from __future__ import annotations

import math
import os

import pandas as pd
import streamlit as st

from dashboard import queries as data
from dashboard.charts import detail_chart
from dashboard.theme import palette
from data import settings

DISCLAIMER = "Signale sind Hinweise, keine Anlageempfehlung."

settings.load_dotenv()
DB = settings.db_path()
SYMBOLS = settings.watchlist()
TIMEFRAMES = settings.timeframes()
# Für Tests/ohne Netz: keine Live-Abfragen an Bitunix
OFFLINE = os.getenv("DASHBOARD_OFFLINE") == "1"

st.set_page_config(page_title="Trading Assistant", page_icon="📈", layout="wide")


# ------------------------------------------------------------------ Daten (gecacht)

@st.cache_data(ttl=15, show_spinner=False)
def live(symbols: tuple[str, ...]) -> data.LiveData:
    if OFFLINE:
        return data.LiveData({}, {}, error="Offline-Modus")
    return data.fetch_live(list(symbols))


@st.cache_data(ttl=30, show_spinner=False)
def overview(symbols: tuple[str, ...], tfs: tuple[str, ...], rsi_tf: str) -> pd.DataFrame:
    return data.overview(DB, list(symbols), list(tfs), rsi_tf, live(symbols))


@st.cache_data(ttl=30, show_spinner=False)
def candles(symbol: str, tf: str) -> pd.DataFrame:
    return data.candles_with_indicators(DB, symbol, tf)


@st.cache_data(ttl=30, show_spinner=False)
def signals() -> pd.DataFrame:
    return data.signals_frame(DB)


# ------------------------------------------------------------------------- Seitenleiste

with st.sidebar:
    st.header("Trading Assistant")
    st.caption("Bitunix USDT-Perpetuals · Phase 1: Markt & Signale")
    if st.button("Daten aktualisieren", type="primary", use_container_width=True,
                 disabled=OFFLINE, help="Kerzen nachladen und Signale neu berechnen"):
        with st.spinner("Lade Kerzen und berechne Signale …"):
            n_new, errors = data.refresh(DB, SYMBOLS, TIMEFRAMES)
        st.cache_data.clear()
        st.success(f"Aktualisiert – {n_new} neue Signale (inkl. historischer).")
        for e in errors:
            st.warning(e)
    st.caption(f"Watchlist: {', '.join(SYMBOLS)}")
    st.caption(f"Datenbank: `{DB}`")
    st.divider()
    st.caption(DISCLAIMER)

theme = palette(getattr(st.context.theme, "type", None))

tab_overview, tab_chart, tab_feed = st.tabs(["Marktübersicht", "Chart", "Signal-Feed"])

# ------------------------------------------------------------------------- Übersicht

with tab_overview:
    c1, c2 = st.columns([1, 3])
    rsi_tf = c1.selectbox("RSI-Timeframe", TIMEFRAMES,
                          index=TIMEFRAMES.index("1h") if "1h" in TIMEFRAMES else 0)
    lv = live(tuple(SYMBOLS))
    if lv.error and not OFFLINE:
        st.warning(f"Live-Daten nicht verfügbar ({lv.error}) – Preise aus der Datenbank.")
    df = overview(tuple(SYMBOLS), tuple(TIMEFRAMES), rsi_tf)

    col_cfg = {
        "Preis": st.column_config.NumberColumn(format="%.6g"),
        "24h %": st.column_config.NumberColumn(format="%+.2f %%"),
        f"RSI {rsi_tf}": st.column_config.NumberColumn(format="%.1f",
                                                       help="RSI 14; > 70 überkauft, < 30 überverkauft"),
        "Funding %": st.column_config.NumberColumn(format="%+.4f %%",
                                                   help="Aktuelle Funding Rate pro Intervall"),
    }
    for tf in TIMEFRAMES:
        col_cfg[f"Trend {tf}"] = st.column_config.TextColumn(
            tf, help=f"▲ Schlusskurs über EMA 200, ▼ darunter ({tf})", width="small")
    st.dataframe(df, hide_index=True, use_container_width=True, column_config=col_cfg)
    st.caption("Trend: ▲ Schlusskurs über EMA 200 · ▼ darunter · – zu wenig Daten. "
               "Preis und 24h-Änderung live von Bitunix.")

    recent = signals()
    if not recent.empty:
        st.subheader("Neueste Signale")
        st.dataframe(recent.head(8)[["Zeit", "Symbol", "Timeframe", "Richtung", "Stärke",
                                     "Begründung"]],
                     hide_index=True, use_container_width=True,
                     column_config={"Zeit": st.column_config.DatetimeColumn(
                         format="DD.MM. HH:mm", help="Kerze (UTC)")})

# ----------------------------------------------------------------------------- Chart

with tab_chart:
    c1, c2, c3 = st.columns([1, 1, 2])
    symbol = c1.selectbox("Symbol", SYMBOLS)
    tf = c2.selectbox("Timeframe", TIMEFRAMES,
                      index=TIMEFRAMES.index("1h") if "1h" in TIMEFRAMES else 0)
    n = c3.slider("Kerzen", min_value=50, max_value=500, value=150, step=10)
    full = candles(symbol, tf)
    if full.empty:
        st.info("Keine Kerzen in der Datenbank – links auf „Daten aktualisieren“ klicken.")
    else:
        view = full.iloc[-n:]
        sig = signals()
        sig = sig[(sig["Symbol"] == symbol) & (sig["Timeframe"] == tf)]
        last = view.iloc[-1]
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Schluss (letzte Kerze)", f"{last['close']:.6g}")
        m2.metric("RSI 14", "–" if math.isnan(last["rsi_14"]) else f"{last['rsi_14']:.1f}")
        m3.metric("ATR 14", f"{last['atr_14'] / last['close'] * 100:.2f} %")
        m4.metric("Signale im Ausschnitt", int((sig["Zeit"] >= view.index[0]).sum()))
        fig = detail_chart(view, sig, theme, f"{symbol} · {tf}")
        st.plotly_chart(fig, use_container_width=True, theme=None,
                        config={"displaylogo": False, "scrollZoom": True})
        st.caption(f"Kerzen in UTC, nur abgeschlossene. ▲/▼ = Signale (Größe = Stärke, "
                   f"Details beim Darüberfahren). {DISCLAIMER}")

# ------------------------------------------------------------------------ Signal-Feed

with tab_feed:
    feed = signals()
    if feed.empty:
        st.info("Noch keine Signale – links auf „Daten aktualisieren“ klicken.")
    else:
        f1, f2, f3, f4, f5 = st.columns([2, 2, 2, 1.4, 1.4])
        sel_sym = f1.multiselect("Symbol", SYMBOLS, placeholder="alle")
        sel_tf = f2.multiselect("Timeframe", TIMEFRAMES + ["funding"], placeholder="alle")
        sel_rule = f3.multiselect("Regel", list(data.RULE_LABEL.values()), placeholder="alle")
        sel_dir = f4.selectbox("Richtung", ["alle", "bullisch", "bärisch"])
        min_strength = f5.selectbox("Stärke ab", [1, 2, 3],
                                    format_func=lambda v: {1: "schwach", 2: "mittel",
                                                           3: "stark"}[v])
        mask = feed["Stärke_n"] >= min_strength
        if sel_sym:
            mask &= feed["Symbol"].isin(sel_sym)
        if sel_tf:
            mask &= feed["Timeframe"].isin(sel_tf)
        if sel_rule:
            mask &= feed["Regel"].isin(sel_rule)
        if sel_dir != "alle":
            mask &= feed["direction"] == ("long" if sel_dir == "bullisch" else "short")
        shown = feed[mask]
        st.caption(f"{len(shown)} von {len(feed)} Signalen · neueste zuerst")
        st.dataframe(
            shown[["Zeit", "Symbol", "Timeframe", "Regel", "Richtung", "Stärke", "Preis",
                   "Begründung"]],
            hide_index=True, use_container_width=True, height=600,
            column_config={
                "Zeit": st.column_config.DatetimeColumn(format="DD.MM.YYYY HH:mm",
                                                        help="Kerze (UTC); bei Funding: Abrechnung"),
                "Preis": st.column_config.NumberColumn(format="%.6g"),
                "Begründung": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(DISCLAIMER)
