"""Tests für das Dashboard: Datenaufbereitung, Chart und ein Offline-Lauf der App."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bitunix.models import FundingRate, Kline, Ticker
from dashboard import queries
from dashboard.charts import detail_chart
from dashboard.theme import DARK, LIGHT, palette
from data.store import CandleStore
from indicators import add_all
from signals import Signal, SignalStore

H = 3_600_000
APP = Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py"


@pytest.fixture
def db(tmp_path) -> str:
    """DB mit 260 1h-Kerzen BTCUSDT (Aufwärtstrend) und zwei Signalen."""
    path = str(tmp_path / "market.db")
    closes = np.linspace(100, 200, 260)
    klines = [Kline(i * H, c - 0.5, c + 1, c - 1, c, 10.0, 1000.0) for i, c in enumerate(closes)]
    with CandleStore(path) as cs:
        cs.upsert("BTCUSDT", "1h", klines)
    with SignalStore(path) as ss:
        ss.add([
            Signal("BTCUSDT", "1h", "ema_cross", "long", 3, "Golden Cross Test", 150.0, 200 * H),
            Signal("BTCUSDT", "funding", "funding_extreme", "short", 1, "Funding Test", 1.0, 300 * H),
        ])
    return path


def test_palette():
    assert palette("dark") is DARK and palette("light") is LIGHT and palette(None) is LIGHT


def test_overview_mit_live_daten(db):
    live = queries.LiveData(
        tickers={"BTCUSDT": Ticker("BTCUSDT", 210.0, 210.0, 200.0, 211.0, 199.0, 1.0, 1.0)},
        funding={"BTCUSDT": FundingRate("BTCUSDT", 0.0001, 1, 1, 1, 0, 8, 0.003, -0.003)},
    )
    df = queries.overview(db, ["BTCUSDT", "ETHUSDT"], ["1h", "4h"], "1h", live)
    btc, eth = df.iloc[0], df.iloc[1]
    assert btc["Preis"] == 210.0 and btc["24h %"] == pytest.approx(5.0)
    assert btc["Trend 1h"] == "▲" and btc["Trend 4h"] == "–"
    assert btc["RSI 1h"] == 100.0  # nur steigende Kurse
    assert btc["Funding %"] == pytest.approx(0.01)
    assert eth["Trend 1h"] == "–" and np.isnan(eth["Preis"])


def test_overview_ohne_live_daten_nimmt_db_preis(db):
    df = queries.overview(db, ["BTCUSDT"], ["1h"], "1h", queries.LiveData({}, {}, "offline"))
    assert df.iloc[0]["Preis"] == pytest.approx(200.0)
    assert np.isnan(df.iloc[0]["Funding %"])


def test_signals_frame(db):
    df = queries.signals_frame(db)
    assert list(df["Timeframe"]) == ["funding", "1h"]  # neueste zuerst
    assert df.iloc[1]["Richtung"] == "▲ bullisch" and df.iloc[1]["Regel"] == "EMA-Cross"
    assert df.iloc[1]["Stärke"] == "stark"


def test_signals_frame_leer(tmp_path):
    df = queries.signals_frame(str(tmp_path / "leer.db"))
    assert df.empty and "Zeit" in df.columns


def test_detail_chart_enthaelt_alle_teile(db):
    with CandleStore(db) as cs:
        df = add_all(cs.load_df("BTCUSDT", "1h")).iloc[-100:]
    sig = queries.signals_frame(db)
    sig = sig[sig["Timeframe"] == "1h"]
    fig = detail_chart(df, sig, DARK, "BTCUSDT · 1h", show_macd=True)
    names = {t.name for t in fig.data}
    assert {"Kurs", "EMA 20", "EMA 50", "EMA 200", "Bollinger (20, 2)", "Volumen", "RSI 14",
            "MACD", "MACD-Signal", "Signal bullisch"} <= names
    marker = next(t for t in fig.data if t.name == "Signal bullisch")
    assert len(marker.x) == 1 and "Golden Cross Test" in marker.text[0]
    assert fig.layout.paper_bgcolor == DARK.surface


def test_detail_chart_ohne_signale(db):
    with CandleStore(db) as cs:
        df = add_all(cs.load_df("BTCUSDT", "1h"))
    fig = detail_chart(df, queries.signals_frame(db).iloc[0:0], LIGHT, "x")
    assert not any(t.name and t.name.startswith("Signal") for t in fig.data)


def test_app_laeuft_offline(db, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("DB_PATH", db)
    monkeypatch.setenv("WATCHLIST", "BTCUSDT")
    monkeypatch.setenv("TIMEFRAMES", "1h,4h")
    monkeypatch.setenv("DASHBOARD_OFFLINE", "1")
    at = AppTest.from_file(str(APP), default_timeout=30).run()
    assert not at.exception, at.exception
    assert [t.label for t in at.tabs] == ["Marktübersicht", "Chart", "Signal-Feed", "Backtest",
                                         "Paper Trading"]
    assert at.metric[0].label == "BTCUSDT"
    assert any("aufwärts" in m.value for m in at.markdown)  # Trend in Worten
    assert len(at.toggle) >= 4  # Chart-Schalter
    assert any("keine Anlageempfehlung" in c.value for c in at.caption)


def test_einordnungen():
    assert queries.trend_word(["▲", "▲", "–"]) == "aufwärts"
    assert queries.trend_word(["▼", "▼"]) == "abwärts"
    assert queries.trend_word(["▲", "▼"]) == "gemischt"
    assert queries.trend_word(["–"]) == "–"
    assert [queries.rsi_word(v) for v in (75, 50, 25, float("nan"))] == \
        ["überkauft", "neutral", "überverkauft", "–"]
    assert queries.funding_word(0.00005, 0.0005) == "normal"
    assert queries.funding_word(-0.0002, 0.0005) == "erhöht"
    assert queries.funding_word(0.0006, 0.0005) == "extrem"
    assert queries.funding_word(0.0003, 0.0005, interval_h=4) == "extrem"
    assert queries.funding_word(None, 0.0005) == "–"


def test_symbol_cards(db):
    live = queries.LiveData(
        tickers={"BTCUSDT": Ticker("BTCUSDT", 210.0, 210.0, 200.0, 211.0, 199.0, 1.0, 1.0)},
        funding={"BTCUSDT": FundingRate("BTCUSDT", 0.00002, 1, 1, 1, 0, 8, 0.003, -0.003)},
    )
    (card,) = queries.symbol_cards(db, ["BTCUSDT"], ["1h", "4h"], live, queries.signals_frame(db))
    assert card["price"] == 210.0 and card["change_24h"] == pytest.approx(5.0)
    assert card["groups"] == [{"name": "mittelfristig", "tfs": ["1h"], "word": "aufwärts"},
                              {"name": "langfristig", "tfs": ["4h"], "word": "–"}]
    assert card["rsi_word"] == "überkauft" and card["funding_word"] == "normal"
    # nur Signale ab "mittel": das schwache Funding-Signal fehlt
    assert [s["Regel"] for s in card["signals"]] == ["EMA-Cross"]


def test_detail_chart_standard_schlicht_und_optionen(db):
    with CandleStore(db) as cs:
        df = add_all(cs.load_df("BTCUSDT", "1h")).iloc[-100:]
    sig = queries.signals_frame(db)
    fig = detail_chart(df, sig[sig["Timeframe"] == "1h"], LIGHT, "x")
    vis = {t.name: t.visible for t in fig.data}
    assert vis["EMA 20"] is False and vis["Bollinger (20, 2)"] is False
    assert "MACD" not in vis
    fig = detail_chart(df, sig, LIGHT, "x", show_ema20=True, show_bb=True, show_macd=True)
    vis = {t.name: t.visible for t in fig.data}
    assert vis["EMA 20"] is True and vis["Bollinger (20, 2)"] is True and "MACD" in vis


def test_latest_backtest_und_paper_overview(tmp_path, db):
    import json

    from strategy import STRATEGIES
    from strategy.paper import PaperStore, PaperTrader

    assert queries.latest_backtest(str(tmp_path / "nix")) == (None, [])
    run_dir = tmp_path / "bt" / "20260101-000000" / "trend_1h"
    run_dir.mkdir(parents=True)
    pd.Series([1000.0, 1010.0], index=pd.to_datetime([0, H], unit="ms", utc=True),
              name="Kontostand").to_csv(run_dir / "equity.csv", index_label="Zeit (UTC)")
    metrics = {"Tage": 1.0, "Trades": 2, "Trefferquote %": 50.0, "Rendite %": 1.0,
               "Max. Drawdown %": -0.5, "Profitfaktor": 1.5, "Ø R pro Trade": 0.2,
               "Gebühren": 1.2, "Endkapital": 1010.0}
    (run_dir.parent / "summary.json").write_text(json.dumps(
        [{"strategy": "trend", "label": "Trendfolge", "timeframe": "1h", "metrics": metrics}]))
    (tmp_path / "bt" / "latest.txt").write_text("20260101-000000")
    stamp, runs = queries.latest_backtest(str(tmp_path / "bt"))
    assert stamp == "20260101-000000" and len(runs[0]["equity"]) == 2
    assert queries.backtest_table(runs).iloc[0]["Rendite %"] == 1.0

    with PaperStore(db) as ps:
        PaperTrader(ps, "trend@1h", STRATEGIES["trend"], ["1h"], now=5 * H)
    (acc,) = queries.paper_overview(db)
    assert acc["name"] == "trend@1h" and acc["equity_now"] == 1000 and acc["trades"] == 0
