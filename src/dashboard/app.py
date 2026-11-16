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
from signals.rules import FUNDING_EXTREME_PER_8H

DISCLAIMER = "Signale sind Hinweise, keine Anlageempfehlung."

settings.load_dotenv()
DB = settings.db_path()
SYMBOLS = settings.watchlist()
TIMEFRAMES = settings.timeframes()
# Für Tests/ohne Netz: keine Live-Abfragen an Bitunix
OFFLINE = os.getenv("DASHBOARD_OFFLINE") == "1"
FUNDING_THRESHOLD = float(os.getenv("FUNDING_EXTREME_PER_8H", FUNDING_EXTREME_PER_8H))

st.set_page_config(page_title="Trading Assistant", page_icon="📈", layout="wide")


# ------------------------------------------------------------------ Daten (gecacht)

@st.cache_data(ttl=15, show_spinner=False)
def live(symbols: tuple[str, ...]) -> data.LiveData:
    if OFFLINE:
        return data.LiveData({}, {}, error="Offline-Modus")
    return data.fetch_live(list(symbols))


@st.cache_data(ttl=30, show_spinner=False)
def symbol_cards(symbols: tuple[str, ...], tfs: tuple[str, ...]) -> list[dict]:
    return data.symbol_cards(DB, list(symbols), list(tfs), live(symbols), signals(),
                             funding_extreme_per_8h=FUNDING_THRESHOLD)


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

SHORT_TFS = {"1m", "3m", "5m"}


def _arrow_md(a: str) -> str:
    return {"▲": ":blue[▲]", "▼": ":red[▼]"}.get(a, "–")


def _word_md(word: str) -> str:
    return {"aufwärts": ":blue[**aufwärts**]", "abwärts": ":red[**abwärts**]",
            "gemischt": "**gemischt**"}.get(word, "–")


with tab_overview:
    lv = live(tuple(SYMBOLS))
    if lv.error and not OFFLINE:
        st.warning(f"Live-Daten nicht verfügbar ({lv.error}) – Preise aus der Datenbank.")
    cards = symbol_cards(tuple(SYMBOLS), tuple(TIMEFRAMES))
    cols = st.columns(min(3, max(1, len(cards))))
    for i, c in enumerate(cards):
        with cols[i % len(cols)].container(border=True):
            chg = c["change_24h"]
            st.metric(
                c["symbol"],
                "–" if math.isnan(c["price"]) else f"{c['price']:.6g} USDT",
                None if math.isnan(chg) else f"{chg:+.2f} % (24h)",
                delta_color="normal",
            )
            st.markdown("**Trend** (Kurs über/unter EMA 200)")
            for g in c["groups"]:
                arrows = " ".join(f"{tf} {_arrow_md(c['arrows'][tf])}" for tf in g["tfs"])
                st.markdown(f"{g['name'].capitalize()}: {_word_md(g['word'])}  \n"
                            f":gray[{arrows}]")
            rsi = "–" if math.isnan(c["rsi"]) else f"{c['rsi']:.0f}"
            fund = "–" if math.isnan(c["funding_pct"]) else f"{c['funding_pct']:+.4f} %"
            st.markdown(f"**RSI {c['rsi_timeframe']}:** {rsi} · {c['rsi_word']}  \n"
                        f"**Funding:** {fund} / {c['funding_interval_h']}h · {c['funding_word']}")
            if c["signals"]:
                st.markdown("**Letzte wichtige Signale**")
                for sg in c["signals"]:
                    st.markdown(f"{sg['Richtung']} · {sg['Timeframe']} · "
                                f"{sg['Zeit']:%d.%m. %H:%M}  \n:gray[{sg['Begründung']}]")
            else:
                st.caption("Keine Signale ab Stärke „mittel“.")
    st.caption("Trend: kurzfristig = 1m/3m/5m, mittelfristig = 15m/1h, langfristig = 4h/1d. "
               "RSI über 70 = überkauft, unter 30 = überverkauft. Funding normal < 0,01 %. "
               "Preis und 24h-Änderung live von Bitunix.")

# ----------------------------------------------------------------------------- Chart

with tab_chart:
    c1, c2, c3 = st.columns([1, 1, 2])
    symbol = c1.selectbox("Symbol", SYMBOLS)
    tf = c2.selectbox("Timeframe", TIMEFRAMES,
                      index=TIMEFRAMES.index("1h") if "1h" in TIMEFRAMES else 0)
    n = c3.slider("Kerzen", min_value=50, max_value=500, value=120, step=10)
    t1, t2, t3, t4 = st.columns(4)
    show_ema20 = t1.toggle("EMA 20", value=False)
    show_bb = t2.toggle("Bollinger Bands", value=False)
    show_weak = t3.toggle("Schwache Signale", value=False)
    show_macd = t4.toggle("MACD", value=False)
    full = candles(symbol, tf)
    if full.empty:
        st.info("Keine Kerzen in der Datenbank – links auf „Daten aktualisieren“ klicken.")
    else:
        view = full.iloc[-n:]
        sig = signals()
        sig = sig[(sig["Symbol"] == symbol) & (sig["Timeframe"] == tf)]
        fig = detail_chart(view, sig, theme, f"{symbol} · {tf}", show_ema20=show_ema20,
                           show_bb=show_bb, show_weak=show_weak, show_macd=show_macd)
        st.plotly_chart(fig, use_container_width=True, theme=None,
                        config={"displaylogo": False, "scrollZoom": True})
        st.caption(f"Kerzen in UTC, nur abgeschlossene. ▲/▼ = Signale ab „mittel“ (Größe = Stärke, "
                   f"Begründung beim Darüberfahren). {DISCLAIMER}")

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
        min_strength = f5.selectbox("Stärke ab", [1, 2, 3], index=1,
                                    format_func=lambda v: {1: "schwach", 2: "mittel",
                                                           3: "stark"}[v])
        hide_short = st.toggle("1m, 3m und 5m ausblenden", value=True,
                               help="Auf kleinen Timeframes gibt es sehr viele Signale.")
        mask = feed["Stärke_n"] >= min_strength
        if hide_short and not sel_tf:
            mask &= ~feed["Timeframe"].isin(SHORT_TFS)
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
            shown[["Zeit", "Symbol", "Timeframe", "Richtung", "Stärke", "Begründung"]],
            hide_index=True, use_container_width=True, height=600,
            column_config={
                "Zeit": st.column_config.DatetimeColumn(format="DD.MM. HH:mm",
                                                        help="Kerze (UTC); bei Funding: Abrechnung"),
                "Begründung": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(DISCLAIMER)
