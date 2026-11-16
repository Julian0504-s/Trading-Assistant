"""Plotly-Detailchart: Kerzen + Indikatoren + Signal-Marker.

Aufbau in Zeilen mit gemeinsamer Zeitachse (keine zweite y-Achse):
Kurs · Volumen · RSI 14 · optional MACD.

Standardmäßig bewusst schlicht: Kerzen, EMA 50/200 und Signale ab "mittel".
EMA 20, Bollinger Bands und schwache Signale sind als Traces enthalten, aber
ausgeblendet (per Schalter / Klick auf die Legende einblendbar).
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .theme import Palette

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Trace-Namen, über die sich Elemente ein-/ausblenden lassen
TOGGLE_TRACES = {
    "ema20": ["EMA 20"],
    "bb": ["Bollinger oben", "Bollinger (20, 2)"],
    "weak": ["Schwache Signale"],
}


def detail_chart(
    df: pd.DataFrame,
    signals: pd.DataFrame,
    p: Palette,
    title: str,
    *,
    show_ema20: bool = False,
    show_bb: bool = False,
    show_weak: bool = False,
    show_macd: bool = False,
) -> go.Figure:
    """`df`: Kerzen mit Indikatorspalten (bereits auf den sichtbaren Bereich gekürzt).
    `signals`: Zeilen aus `queries.signals_frame` für dieses Symbol/Timeframe."""
    if show_macd:
        rows, heights = 4, [0.58, 0.12, 0.13, 0.17]
        titles = (title, "Volumen", "RSI 14", "MACD (12, 26, 9)")
    else:
        rows, heights = 3, [0.68, 0.14, 0.18]
        titles = (title, "Volumen", "RSI 14 (über 70 überkauft, unter 30 überverkauft)")
    fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.035,
                        row_heights=heights, subplot_titles=titles)
    x = df.index

    # --- Kurs: Bollinger-Band als Fläche hinter den Kerzen (standardmäßig aus)
    fig.add_trace(go.Scatter(x=x, y=df["bb_upper"], line=dict(color=p.band, width=1),
                             name="Bollinger oben", legendgroup="bb", showlegend=False,
                             visible=show_bb,
                             hovertemplate="BB oben %{y:.6g}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["bb_lower"], line=dict(color=p.band, width=1),
                             fill="tonexty", fillcolor=p.band_fill, name="Bollinger (20, 2)",
                             legendgroup="bb", showlegend=show_bb, visible=show_bb,
                             hovertemplate="BB unten %{y:.6g}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Candlestick(
        x=x, open=df["open"], high=df["high"], low=df["low"], close=df["close"], name="Kurs",
        increasing=dict(line=dict(color=p.up, width=1), fillcolor=p.up),
        decreasing=dict(line=dict(color=p.down, width=1), fillcolor=p.down),
        showlegend=False,
    ), row=1, col=1)
    for col, color, label, on in (("ema_20", p.ema20, "EMA 20", show_ema20),
                                  ("ema_50", p.ema50, "EMA 50", True),
                                  ("ema_200", p.ema200, "EMA 200", True)):
        fig.add_trace(go.Scatter(x=x, y=df[col], line=dict(color=color, width=2), name=label,
                                 visible=on, showlegend=on,
                                 hovertemplate=f"{label} %{{y:.6g}}<extra></extra>"),
                      row=1, col=1)

    # --- Signal-Marker: ▲ unter dem Tief (bullisch), ▼ über dem Hoch (bärisch)
    if not signals.empty:
        sig = signals[(signals["Zeit"] >= x[0]) & (signals["Zeit"] <= x[-1])]
        offset = (df["high"] - df["low"]).median() * 0.8

        def marker_y(s: pd.DataFrame) -> list[float]:
            bars = df.reindex(s["Zeit"])
            ys = [lo - offset if d == "long" else hi + offset
                  for d, lo, hi in zip(s["direction"], bars["low"], bars["high"])]
            return ys

        def hover(s: pd.DataFrame) -> list[str]:
            return (s["Richtung"] + " · " + s["Regel"] + " · " + s["Stärke"] + "<br>"
                    + s["Begründung"]).tolist()

        strong = sig[sig["Stärke_n"] >= 2]
        for direction, symbol, color, label in (("long", "triangle-up", p.up, "Signal bullisch"),
                                                ("short", "triangle-down", p.down, "Signal bärisch")):
            s = strong[strong["direction"] == direction]
            if s.empty:
                continue
            fig.add_trace(go.Scatter(
                x=s["Zeit"], y=marker_y(s), mode="markers", name=label,
                marker=dict(symbol=symbol, size=s["Stärke_n"].map({2: 13, 3: 17}).tolist(),
                            color=color, line=dict(color=p.surface, width=2)),
                text=hover(s), hovertemplate="%{text}<extra></extra>",
            ), row=1, col=1)
        weak = sig[sig["Stärke_n"] < 2]
        if not weak.empty:
            fig.add_trace(go.Scatter(
                x=weak["Zeit"], y=marker_y(weak), mode="markers", name="Schwache Signale",
                visible=show_weak, showlegend=show_weak,
                marker=dict(symbol=["triangle-up" if d == "long" else "triangle-down"
                                    for d in weak["direction"]],
                            size=9, color=p.muted, line=dict(color=p.surface, width=1)),
                text=hover(weak), hovertemplate="%{text}<extra></extra>",
            ), row=1, col=1)

    # --- Volumen (Farbe nach Kerzenrichtung, abgeschwächt)
    vol_colors = [p.up if c >= o else p.down for o, c in zip(df["open"], df["close"])]
    fig.add_trace(go.Bar(x=x, y=df["volume"], marker=dict(color=vol_colors, opacity=0.55,
                                                          line=dict(width=0)),
                         name="Volumen", showlegend=False,
                         hovertemplate="Volumen %{y:.4g}<extra></extra>"), row=2, col=1)

    # --- RSI mit 70/30-Linien
    fig.add_trace(go.Scatter(x=x, y=df["rsi_14"], line=dict(color=p.rsi, width=2), name="RSI 14",
                             showlegend=False, hovertemplate="RSI %{y:.1f}<extra></extra>"),
                  row=3, col=1)
    for level in (70, 30):
        fig.add_hline(y=level, line=dict(color=p.muted, width=1, dash="dot"), row=3, col=1)

    # --- MACD: Histogramm (Polarität) + Linien (optional)
    if show_macd:
        _add_macd(fig, df, p)

    _style(fig, p, show_macd)
    return fig


def _add_macd(fig: go.Figure, df: pd.DataFrame, p: Palette) -> None:
    x = df.index
    hist_colors = [p.up if v >= 0 else p.down for v in df["macd_hist"].fillna(0)]
    fig.add_trace(go.Bar(x=x, y=df["macd_hist"], marker=dict(color=hist_colors, opacity=0.6,
                                                             line=dict(width=0)),
                         name="Histogramm", showlegend=False,
                         hovertemplate="Hist %{y:.4g}<extra></extra>"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["macd"], line=dict(color=p.macd, width=2), name="MACD",
                             hovertemplate="MACD %{y:.4g}<extra></extra>"), row=4, col=1)
    fig.add_trace(go.Scatter(x=x, y=df["macd_signal"], line=dict(color=p.macd_signal, width=2),
                             name="MACD-Signal",
                             hovertemplate="Signal %{y:.4g}<extra></extra>"), row=4, col=1)


def _style(fig: go.Figure, p: Palette, show_macd: bool) -> None:
    """Zurückhaltendes Raster, Crosshair, Legende oben."""
    fig.update_layout(
        height=900 if show_macd else 760, margin=dict(l=10, r=10, t=70, b=50),
        paper_bgcolor=p.surface, plot_bgcolor=p.surface,
        font=dict(family=FONT, color=p.text_secondary, size=12),
        hovermode="x unified", hoverlabel=dict(bgcolor=p.surface, font=dict(color=p.text)),
        legend=dict(orientation="h", yanchor="bottom", y=1.03, xanchor="right", x=1,
                    font=dict(color=p.text_secondary)),
        xaxis_rangeslider_visible=False, bargap=0.15,
    )
    fig.update_xaxes(showgrid=False, linecolor=p.axis, tickcolor=p.axis,
                     showspikes=True, spikemode="across", spikethickness=1,
                     spikecolor=p.muted, spikedash="dot")
    fig.update_yaxes(gridcolor=p.grid, zeroline=False, linecolor=p.axis, side="right",
                     automargin=True, tickformat="~s")
    fig.update_yaxes(tickformat=",.6~g", row=1, col=1)
    fig.update_yaxes(range=[0, 100], tickvals=[30, 50, 70], tickformat="d", row=3, col=1)
    if show_macd:
        fig.update_yaxes(tickformat=".3~g", row=4, col=1)
    for ann in fig.layout.annotations:  # Untertitel der Zeilen
        ann.font = dict(family=FONT, size=12, color=p.text_secondary)
        ann.x, ann.xanchor = 0, "left"
    fig.layout.annotations[0].font = dict(family=FONT, size=15, color=p.text)
