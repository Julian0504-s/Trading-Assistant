"""Tests für den ECB-Forex-Import (ohne Netz)."""

import pytest

from data.ecb import pair_closes, parse_csv, to_klines

CSV = """KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2024-01-02,1.10
EXR.D.USD.EUR.SP00.A,D,USD,EUR,SP00,A,2024-01-03,1.12
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2024-01-02,0.86
EXR.D.GBP.EUR.SP00.A,D,GBP,EUR,SP00,A,2024-01-03,0.87
EXR.D.JPY.EUR.SP00.A,D,JPY,EUR,SP00,A,2024-01-02,155.0
EXR.D.JPY.EUR.SP00.A,D,JPY,EUR,SP00,A,2024-01-03,
"""


def test_paare_aus_euro_kursen():
    t = parse_csv(CSV)
    for c in ("CHF", "AUD", "CAD", "NZD"):
        t[c] = float("nan")
    p = pair_closes(t)
    assert p["EURUSD"].tolist() == [1.10, 1.12]
    assert p["GBPUSD"].iloc[0] == pytest.approx(1.10 / 0.86)        # USD je GBP
    assert p["USDJPY"].iloc[0] == pytest.approx(155.0 / 1.10)       # JPY je USD
    assert p["EURJPY"].tolist() == [155.0]                          # leerer Wert übersprungen
    assert p["GBPJPY"].iloc[0] == pytest.approx(155.0 / 0.86)


def test_kerzen_aus_tageskursen():
    t = parse_csv(CSV)
    for c in ("CHF", "AUD", "CAD", "NZD"):
        t[c] = float("nan")
    k = to_klines(pair_closes(t)["EURUSD"])
    assert (k[0].open, k[0].close) == (1.10, 1.10)
    assert (k[1].open, k[1].high, k[1].low, k[1].close) == (1.10, 1.12, 1.10, 1.12)
