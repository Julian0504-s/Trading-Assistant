"""Tests für Signal-Regeln und Signal-Speicher mit konstruierten Beispieldaten."""

import numpy as np
import pandas as pd
import pytest

from bitunix.models import FundingRate
from indicators import add_all
from signals import Signal, SignalStore, evaluate_candles, funding_extreme
from signals.rules import (
    bb_breakout,
    cross_above,
    cross_below,
    ema200_trend,
    ema_cross,
    fmt_price,
    rsi_extreme,
)

H = 3_600_000


def ind_frame(n: int, **cols) -> pd.DataFrame:
    """DataFrame mit Indikatorspalten; nicht angegebene Spalten sind neutral."""
    idx = pd.to_datetime([i * H for i in range(n)], unit="ms", utc=True)
    base = {
        "close": [100.0] * n,
        "ema_20": [100.0] * n, "ema_50": [100.0] * n, "ema_200": [np.nan] * n,
        "rsi_14": [50.0] * n, "atr_14": [1.0] * n,
        "bb_upper": [110.0] * n, "bb_lower": [90.0] * n,
        "volume_ratio": [1.0] * n,
    }
    base.update({k: list(map(float, v)) for k, v in cols.items()})
    return pd.DataFrame(base, index=idx)


# ------------------------------------------------------------------ Hilfsfunktionen

def test_cross_above_und_below():
    a = pd.Series([1, 2, 3, 2, 1, np.nan, 3])
    assert cross_above(a, 2.5).tolist() == [False, False, True, False, False, False, False]
    assert cross_below(a, 1.5).tolist() == [False, False, False, False, True, False, False]
    # Berühren ist noch kein Kreuzen, erst das Überschreiten
    assert cross_above(pd.Series([1.0, 2.0, 2.0, 3.0]), 2.0).tolist() == [False, False, False, True]


def test_fmt_price():
    assert fmt_price(64511.8) == "64511.8"
    assert fmt_price(1805.27) == "1805.27"
    assert fmt_price(71.3712) == "71.3712"
    assert fmt_price(0.00001234) == "0.00001234"
    assert fmt_price(100.0) == "100"


# ---------------------------------------------------------------------- EMA-Cross

def test_ema_cross_golden_mit_trend_und_volumen():
    df = ind_frame(
        4,
        ema_20=[99, 99.5, 101, 102], ema_50=[100, 100, 100, 100],
        ema_200=[90, 90, 90, 90], close=[100, 100, 103, 104],
        volume_ratio=[1, 1, 1.8, 1],
    )
    (s,) = ema_cross(df, "BTCUSDT", "1h")
    assert (s.direction, s.strength, s.candle_time) == ("long", 3, 2 * H)
    assert "Golden Cross" in s.reason and "passt zum Trend" in s.reason and "1.8×" in s.reason
    assert s.price == 103.0


def test_ema_cross_death_gegen_trend():
    df = ind_frame(3, ema_20=[101, 100.5, 99], ema_50=[100, 100, 100], ema_200=[90, 90, 90])
    (s,) = ema_cross(df, "BTCUSDT", "1h")
    assert (s.direction, s.strength) == ("short", 1)
    assert "gegen den übergeordneten Trend" in s.reason


def test_ema_cross_nicht_in_aufwaermphase():
    df = ind_frame(3, ema_20=[np.nan, 99, 101], ema_50=[np.nan, np.nan, 100])
    assert ema_cross(df, "X", "1h") == []


# ------------------------------------------------------------------- EMA-200-Trend

def test_ema200_trend_cross_mit_abstand():
    df = ind_frame(3, close=[95, 98, 102], ema_200=[100, 100, 100], atr_14=[2, 2, 2])
    (s,) = ema200_trend(df, "ETHUSDT", "4h")
    assert (s.direction, s.strength) == ("long", 2)  # 2 > 0.5 * 2 ATR
    assert "über der EMA 200" in s.reason and "1.0 ATR" in s.reason


def test_ema200_trend_nach_unten_knapp():
    # Band = 0.25 * ATR 2 = 0.5 -> 101 ist "über", 99.4 ist "unter"
    df = ind_frame(2, close=[101, 99.4], ema_200=[100, 100], atr_14=[2, 2])
    (s,) = ema200_trend(df, "ETHUSDT", "4h")
    assert (s.direction, s.strength) == ("short", 1)


def test_ema200_trend_hysterese_gegen_hin_und_her():
    # Kurs pendelt eng um die EMA 200 (innerhalb ±0.5) -> keine Signale,
    # erst der klare Bruch nach unten zählt
    df = ind_frame(
        7,
        close=[101, 99.8, 100.3, 99.7, 100.2, 99.9, 98.0],
        ema_200=[100] * 7, atr_14=[2] * 7,
    )
    sigs = ema200_trend(df, "BTCUSDT", "1m")
    assert [(s.candle_time // H, s.direction) for s in sigs] == [(6, "short")]


# ---------------------------------------------------------------------------- RSI

def test_rsi_extreme_nur_beim_eintritt():
    df = ind_frame(6, rsi_14=[60, 71, 75, 72, 65, 82])
    sigs = rsi_extreme(df, "SOLUSDT", "15m")
    # Eintritt bei 71 (schwach) und erneut bei 82 (mittel), nicht bei 75/72
    assert [(s.candle_time // H, s.direction, s.strength) for s in sigs] == \
        [(1, "short", 1), (5, "short", 2)]
    assert "überkauft" in sigs[0].reason


def test_rsi_extreme_ueberverkauft_stark():
    df = ind_frame(2, rsi_14=[35, 14])
    (s,) = rsi_extreme(df, "SOLUSDT", "15m")
    assert (s.direction, s.strength) == ("long", 3)
    assert "überverkauft" in s.reason and "14.0" in s.reason


# ---------------------------------------------------------------------- Bollinger

def test_bb_breakout_braucht_volumen():
    df = ind_frame(3, close=[100, 105, 112], volume_ratio=[1, 1, 1.9])
    assert bb_breakout(df, "X", "1h") == []
    df = ind_frame(3, close=[100, 105, 112], volume_ratio=[1, 1, 3.2], ema_200=[95, 95, 95])
    (s,) = bb_breakout(df, "X", "1h")
    assert (s.direction, s.strength) == ("long", 3)
    assert "über das obere Bollinger-Band (110)" in s.reason and "3.2×" in s.reason


def test_bb_breakout_nach_unten():
    df = ind_frame(2, close=[95, 88], volume_ratio=[1, 2.5])
    (s,) = bb_breakout(df, "X", "1h")
    assert (s.direction, s.strength) == ("short", 1)
    assert "unter das untere Bollinger-Band (90)" in s.reason


# ------------------------------------------------------------------- Gesamtauswertung

def test_evaluate_candles_mit_echten_indikatoren():
    # Abwärtstrend, dann starker Anstieg -> mindestens ein Golden Cross und RSI überkauft
    close = list(np.linspace(200, 100, 250)) + list(np.linspace(100, 180, 60))
    df = pd.DataFrame(
        {"open": close, "high": [c + 1 for c in close], "low": [c - 1 for c in close],
         "close": close, "volume": [10.0] * len(close)},
        index=pd.to_datetime([i * H for i in range(len(close))], unit="ms", utc=True),
    )
    sigs = evaluate_candles(add_all(df), "BTCUSDT", "1h")
    rules = {(s.rule, s.direction) for s in sigs}
    assert ("ema_cross", "long") in rules
    assert ("rsi_extreme", "short") in rules
    assert ("ema200_trend", "long") in rules
    assert [s.candle_time for s in sigs] == sorted(s.candle_time for s in sigs)


# ---------------------------------------------------------------------- Funding

def fr(rate: float, interval_h: int = 8) -> FundingRate:
    return FundingRate("BTCUSDT", rate, 64000.0, 64010.0, 64005.0, 1794816000000,
                       interval_h, 0.003, -0.003)


def test_funding_normal_kein_signal():
    assert funding_extreme(fr(0.0001)) is None
    assert funding_extreme(fr(-0.00049)) is None


def test_funding_extrem_positiv_und_negativ():
    s = funding_extreme(fr(0.0012))
    assert (s.direction, s.strength, s.timeframe) == ("short", 2, "funding")
    assert s.candle_time == 1794816000000
    assert "+0.1200 %" in s.reason and "einseitig long" in s.reason
    s = funding_extreme(fr(-0.0025))
    assert (s.direction, s.strength) == ("long", 3)


def test_funding_schwelle_wird_aufs_intervall_umgerechnet():
    # 4h-Intervall: Schwelle 0,025 % statt 0,05 %
    assert funding_extreme(fr(0.0003, interval_h=4)).strength == 1
    assert funding_extreme(fr(0.0003, interval_h=8)) is None


# ------------------------------------------------------------------------ Store

def sig(t: int, rule="rsi_extreme", sym="BTCUSDT", tf="1h", strength=1) -> Signal:
    return Signal(sym, tf, rule, "long", strength, "Test", 100.0, t, created_at=1)


def test_store_speichert_nur_neue():
    with SignalStore(":memory:") as st:
        assert len(st.add([sig(H), sig(2 * H)])) == 2
        new = st.add([sig(2 * H), sig(3 * H)])
        assert [s.candle_time for s in new] == [3 * H]
        assert st.count() == 3


def test_store_recent_filter_und_sortierung():
    with SignalStore(":memory:") as st:
        st.add([sig(H), sig(3 * H, rule="ema_cross", strength=3),
                sig(2 * H, sym="ETHUSDT", tf="4h", strength=2)])
        assert [s.candle_time for s in st.recent()] == [3 * H, 2 * H, H]
        assert [s.symbol for s in st.recent(symbol="ETHUSDT")] == ["ETHUSDT"]
        assert [s.rule for s in st.recent(min_strength=3)] == ["ema_cross"]
        assert st.recent(limit=1)[0].candle_time == 3 * H
        assert st.recent()[0] == sig(3 * H, rule="ema_cross", strength=3)


def test_signal_validierung():
    with pytest.raises(ValueError):
        Signal("X", "1h", "r", "up", 1, "", 1.0, 0)
    with pytest.raises(ValueError):
        Signal("X", "1h", "r", "long", 4, "", 1.0, 0)


def test_scan_is_fresh_letzte_drei_kerzen():
    from signals.scan import ScanResult

    r = ScanResult(latest_candle={("BTCUSDT", "1h"): 10 * H})
    fresh = [r.is_fresh(sig(t * H)) for t in (7, 8, 10)]
    assert fresh == [False, True, True]
    assert not r.is_fresh(sig(10 * H, sym="ETHUSDT"))  # keine Kerzen bekannt
    assert r.is_fresh(Signal("X", "funding", "funding_extreme", "long", 1, "", 1.0, 0))


def test_donchian_breakout():
    from signals.rules import donchian_breakout

    df = ind_frame(3, close=[100, 104, 112], ema_200=[95, 95, 95], volume_ratio=[1, 1, 2],
                   dc_high_20=[110, 110, 110], dc_low_20=[90, 90, 90])
    (s,) = donchian_breakout(df, "BTC", "4h")
    assert (s.direction, s.strength, s.rule) == ("long", 3, "donchian_breakout")
    assert "20-Kerzen-Hoch (110)" in s.reason
    df = ind_frame(2, close=[95, 85], dc_high_20=[110, 110], dc_low_20=[90, 90])
    (s,) = donchian_breakout(df, "BTC", "4h")
    assert (s.direction, s.strength) == ("short", 1)
    assert donchian_breakout(ind_frame(2), "BTC", "4h") == []   # ohne Kanal-Spalten
