"""Indikator-Tests mit bekannten Beispieldaten."""

import math

import numpy as np
import pandas as pd
import pytest

from indicators import add_all, atr, bollinger, ema, macd, rsi, sma, true_range, volume_ratio


def frame(close, high=None, low=None, volume=None) -> pd.DataFrame:
    close = list(map(float, close))
    return pd.DataFrame({
        "open": close,
        "high": high if high is not None else close,
        "low": low if low is not None else close,
        "close": close,
        "volume": volume if volume is not None else [1.0] * len(close),
    })


# --------------------------------------------------------------------- EMA/SMA

def test_sma():
    s = sma(frame([1, 2, 3, 4, 5]), 3)
    assert s.isna().tolist() == [True, True, False, False, False]
    assert s.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_ema_sma_start_und_rekursion():
    # period 3 -> alpha 0.5; Start = SMA(1,2,3) = 2
    # danach: 0.5*4 + 0.5*2 = 3, 0.5*5 + 0.5*3 = 4, ...
    e = ema(frame(range(1, 11)), 3)
    assert e.iloc[:2].isna().all()
    assert e.iloc[2:].tolist() == pytest.approx([2, 3, 4, 5, 6, 7, 8, 9])
    assert e.name == "ema_3"


def test_ema_linearer_trend_hat_konstanten_abstand():
    # Bei linearem Anstieg (+1 je Kerze) liegt die EMA genau (n-1)/2 unter dem Kurs
    df = frame(range(300))
    e = ema(df, 20)
    assert (df["close"] - e).iloc[19:].to_numpy() == pytest.approx(9.5)


def test_ema_zu_wenig_daten():
    assert ema(frame([1, 2]), 3).isna().all()


# ------------------------------------------------------------------------- RSI

# Klassisches Wilder-RSI-Beispiel (StockCharts "ChartSchool", RSI-Tabelle).
# Die Tabelle zeigt 2 Nachkommastellen, rechnet aber mit diesen 4 –
# mit den gerundeten Kursen weicht der RSI um ~0.07 ab.
RSI_CLOSES = [
    44.3389, 44.0902, 44.1497, 43.6124, 44.3278, 44.8264, 45.0955, 45.4245, 45.8433, 46.0826,
    45.8931, 46.0328, 45.6140, 46.2820, 46.2820, 46.0028, 46.0328, 46.4116, 46.2222, 45.6439,
]
RSI_EXPECTED = [70.53, 66.32, 66.55, 69.41, 66.36, 57.97]  # ab Index 14


def test_rsi_bekanntes_beispiel():
    r = rsi(frame(RSI_CLOSES), 14)
    assert r.iloc[:14].isna().all()
    assert r.iloc[14:].round(2).tolist() == pytest.approx(RSI_EXPECTED, abs=0.01)


def test_rsi_grenzfaelle():
    assert rsi(frame(range(30)), 14).iloc[14:].eq(100).all()          # nur Gewinne
    assert rsi(frame(range(30, 0, -1)), 14).iloc[14:].eq(0).all()     # nur Verluste
    assert rsi(frame([5] * 30), 14).iloc[14:].eq(50).all()            # keine Bewegung


# ------------------------------------------------------------------------ MACD

def test_macd_linearer_trend():
    # Linearer Trend: EMA12 liegt 5.5, EMA26 12.5 unter dem Kurs -> MACD = 7
    m = macd(frame(range(200)))
    assert m["macd"].iloc[:25].isna().all()
    assert m["macd"].iloc[25:].to_numpy() == pytest.approx(7.0)
    # Signal = EMA9 der MACD, erste Werte ab Index 25 + 8
    assert m["macd_signal"].iloc[:33].isna().all()
    assert m["macd_signal"].iloc[33:].to_numpy() == pytest.approx(7.0)
    assert m["macd_hist"].iloc[33:].to_numpy() == pytest.approx(0.0)


def test_macd_konstant_ist_null():
    m = macd(frame([100] * 60))
    assert m.iloc[33:].abs().max().max() == pytest.approx(0.0)


# ------------------------------------------------------------------------- ATR

def test_true_range_mit_gap():
    df = frame(
        close=[10, 15, 12],
        high=[11, 16, 13],
        low=[9, 14, 8],
    )
    # Index 1: Gap nach oben: max(16-14, |16-10|, |14-10|) = 6
    # Index 2: max(13-8, |13-15|, |8-15|) = 7
    assert true_range(df).tolist() == [2, 6, 7]


def test_atr_konstante_spanne():
    n = 40
    close = [100.0] * n
    df = frame(close, high=[101.0] * n, low=[99.0] * n)
    a = atr(df, 14)
    assert a.iloc[:13].isna().all()
    assert a.iloc[13:].to_numpy() == pytest.approx(2.0)


def test_atr_wilder_glaettung():
    # 14 Kerzen mit TR 2, dann eine mit TR 16 -> (13*2 + 16) / 14 = 3
    n = 15
    high = [101.0] * 14 + [108.0]
    low = [99.0] * 14 + [92.0]
    a = atr(frame([100.0] * n, high=high, low=low), 14)
    assert a.iloc[13] == pytest.approx(2.0)
    assert a.iloc[14] == pytest.approx(3.0)


# ------------------------------------------------------------------- Bollinger

def test_bollinger_bekannte_werte():
    bb = bollinger(frame([1, 2, 3, 4, 5]), period=5, num_std=2)
    last = bb.iloc[-1]
    std = math.sqrt(2)  # Populations-Std von 1..5
    assert last["bb_mid"] == pytest.approx(3)
    assert last["bb_upper"] == pytest.approx(3 + 2 * std)
    assert last["bb_lower"] == pytest.approx(3 - 2 * std)
    assert last["bb_pct_b"] == pytest.approx((5 - (3 - 2 * std)) / (4 * std))
    assert bb["bb_mid"].iloc[:4].isna().all()


def test_bollinger_konstant():
    bb = bollinger(frame([7] * 25))
    assert bb["bb_upper"].iloc[-1] == bb["bb_lower"].iloc[-1] == 7
    assert np.isnan(bb["bb_pct_b"].iloc[-1])


# --------------------------------------------------------------------- Volumen

def test_volume_ratio_ohne_aktuelle_kerze():
    vol = [10.0] * 20 + [30.0, 10.0]
    r = volume_ratio(frame([1] * 22, volume=vol), period=20)
    assert r.iloc[:20].isna().all()
    assert r.iloc[20] == pytest.approx(3.0)
    # Die Spike-Kerze fließt jetzt in den Schnitt ein: (19*10 + 30) / 20 = 11
    assert r.iloc[21] == pytest.approx(10 / 11)


# ----------------------------------------------------------------------- Gesamt

def test_add_all_spalten():
    df = frame(np.linspace(100, 200, 250), volume=[5.0] * 250)
    out = add_all(df)
    for col in ["ema_20", "ema_50", "ema_200", "rsi_14", "macd", "macd_signal", "macd_hist",
                "atr_14", "bb_mid", "bb_upper", "bb_lower", "bb_bandwidth", "bb_pct_b",
                "volume_ratio", "volume_spike"]:
        assert col in out.columns
    assert out.iloc[-1].drop("volume_spike").notna().all()
    assert len(out) == len(df) and "volume" in out.columns  # Original bleibt erhalten
