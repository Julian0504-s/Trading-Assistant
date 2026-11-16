"""Tests für Funding-Historie und Abrechnung im Backtest."""

import asyncio

import httpx
import numpy as np
import pandas as pd
import pytest

from bitunix.client import BitunixPublicClient
from data.funding import FUNDING_INTERVAL_MS as F8
from data.funding import FundingStore, funding_schedule, sync_funding
from strategy import SimAccount

H = 3_600_000


def test_client_und_sync_blaettert_zurueck():
    times = [i * F8 for i in range(1, 451)]

    def handler(req: httpx.Request):
        assert req.url.path.endswith("/get_funding_rate_history")
        end = req.url.params.get("endTime")
        avail = [t for t in times if end is None or t <= int(end)]
        page = avail[-int(req.url.params["limit"]):]
        data = [{"fundingRate": "0.0001", "fundingTime": str(t), "markPrice": "100"}
                for t in reversed(page)]
        return httpx.Response(200, json={"code": 0, "msg": "Success", "data": data})

    async def go():
        client = BitunixPublicClient(transport=httpx.MockTransport(handler), backoff_base=0,
                                     rate_limit_per_sec=1000)
        with FundingStore(":memory:") as fs:
            async with client:
                await sync_funding(client, fs, "BTC")
            return fs.bounds("BTC"), len(fs.load("BTC"))

    (lo, hi), n = asyncio.run(go())
    assert (lo, hi, n) == (F8, 450 * F8, 450)


def test_schedule_fuellt_luecken_mit_median():
    with FundingStore(":memory:") as fs:
        # echte Werte bei 10..12 und 15 (Lücke 13, 14), davor nichts
        fs.upsert("X", [(t * F8, r, 100.0) for t, r in [(10, 0.0002), (11, 0.0001),
                                                        (12, 0.0003), (15, -0.0001)]])
        sch = funding_schedule(fs, "X", start=8 * F8, end=15 * F8)
        assert sch["time"].tolist() == [t * F8 for t in range(8, 16)]
        assert sch["estimated"].tolist() == [True, True, False, False, False, True, True, False]
        assert sch.loc[sch["estimated"], "rate"].unique().tolist() == [pytest.approx(0.00015)]
        assert sch.loc[~sch["estimated"], "rate"].tolist() == [0.0002, 0.0001, 0.0003, -0.0001]
        assert funding_schedule(fs, "Y", 0, F8)["rate"].tolist() == [0.0001, 0.0001]


def test_konto_funding_long_zahlt_short_bekommt():
    acc = SimAccount(1000.0, fee_rate=0.0, slippage=0.0)
    lng = acc.open(symbol="BTC", timeframe="1d", strategy="t", direction="long", price=100,
                   time=0, stop=98, target=110, reason="x")         # 5 Stück = 500 USDT
    acc.open(symbol="BTC", timeframe="4h", strategy="t", direction="short", price=100,
             time=0, stop=102, target=90, reason="x")               # 5 Stück
    acc.apply_funding("BTC", 0.001, 100.0, F8)                      # 0,1 %
    assert acc.positions[("BTC", "1d")].funding == pytest.approx(-0.5)
    assert acc.positions[("BTC", "4h")].funding == pytest.approx(+0.5)
    assert acc.cash == pytest.approx(1000.0)
    trade = acc.close(lng, 100, 2 * F8, "x")
    assert trade.pnl == pytest.approx(-0.5)                         # Funding im Ergebnis
    assert acc.funding_total == pytest.approx(0.0)
    assert [o.action for o in acc.orders].count("Funding") == 2


def test_backtest_rechnet_funding_ab():
    from strategy import STRATEGIES
    from strategy.backtest import Stream, run_streams

    D = 24 * H
    idx = pd.to_datetime([i * D for i in range(260)], unit="ms", utc=True)
    c = np.full(260, 100.0)
    df = pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c,
                       "atr_14": 2.0, "ema_200": 90.0}, index=idx)
    from signals import Signal
    sig = {220 * D: [Signal("BTC", "1d", "ema_cross", "long", 2, "x", 100.0, 220 * D)]}
    sched = pd.DataFrame({"time": [t for t in range(221 * D, 259 * D, F8)]})
    sched["rate"], sched["mark"], sched["estimated"] = 0.001, 100.0, False
    res = run_streams([Stream("BTC", "1d", df, STRATEGIES["trend_trail"], sig)],
                      funding={"BTC": sched})
    no = run_streams([Stream("BTC", "1d", df, STRATEGIES["trend_trail"], sig)])
    (t,) = res.trades
    paid = res.metrics["Funding"]
    assert paid > 0 and res.end_equity == pytest.approx(no.end_equity - paid)
    # Position ab Eröffnung Tag 221 bis Ende (Tag 259): Abrechnungen ab 221 + 8 h
    n = len([x for x in sched["time"] if 221 * D < x < 259 * D + D])
    assert paid == pytest.approx(n * 0.001 * 100 * t.qty, rel=0.01)
