"""Tests für das Einlesen des Binance-Archivs (ohne Netz)."""

import io
import zipfile

import pytest

from data.binance_archive import months, parse_funding, parse_klines


def zipped(text: str, name: str = "x.csv") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, text)
    return buf.getvalue()


def test_klines_mit_und_ohne_kopfzeile():
    row = "1577836800000,7189.43,7260.43,7170.15,7197.57,1000,1577923199999,7200000,10,1,1,0"
    for text in (row, "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
                      "taker_buy_volume,taker_buy_quote_volume,ignore\n" + row):
        (k,) = parse_klines(zipped(text))
        assert k.open_time == 1577836800000 and k.close == 7197.57 and k.quote_vol == 7200000


def test_funding():
    text = "calc_time,funding_interval_hours,last_funding_rate\n1577836800000,8,0.00010000\n"
    (row,) = parse_funding(zipped(text))
    assert row[0] == 1577836800000 and row[1] == pytest.approx(0.0001)


def test_unerwarteter_zip_inhalt():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.csv", "1,2")
        z.writestr("b.csv", "1,2")
    with pytest.raises(ValueError):
        parse_klines(buf.getvalue())


def test_months():
    assert months("2019-11", "2020-02") == ["2019-11", "2019-12", "2020-01", "2020-02"]
