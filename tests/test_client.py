"""Tests für den öffentlichen Bitunix-Client mit gemockten HTTP-Antworten."""

import asyncio

import httpx
import pytest

from bitunix.client import BitunixAPIError, BitunixPublicClient

H = 3_600_000


def make_client(handler) -> BitunixPublicClient:
    return BitunixPublicClient(
        transport=httpx.MockTransport(handler),
        backoff_base=0.0,
        rate_limit_per_sec=1000,
    )


def ok(data) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "msg": "Success", "data": data})


def raw_kline(t: int, close: str = "100") -> dict:
    # Format wie die echte API (Zahlen als Strings, time als String)
    return {"open": "99", "high": "101", "low": "98", "close": close,
            "quoteVol": "1000.5", "baseVol": "10", "time": str(t)}


def run(coro):
    return asyncio.run(coro)


def test_get_klines_parst_und_sortiert_aufsteigend():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["params"] = dict(req.url.params)
        # API liefert neueste zuerst
        return ok([raw_kline(3 * H), raw_kline(2 * H), raw_kline(1 * H)])

    async def go():
        async with make_client(handler) as c:
            return await c.get_klines("BTCUSDT", "1h", limit=3)

    klines = run(go())
    assert seen["path"] == "/api/v1/futures/market/kline"
    assert seen["params"] == {"symbol": "BTCUSDT", "interval": "1h", "limit": "3", "type": "LAST_PRICE"}
    assert [k.open_time for k in klines] == [H, 2 * H, 3 * H]
    assert klines[0].close == 100.0 and klines[0].quote_vol == 1000.5


def test_get_klines_validiert_parameter():
    c = make_client(lambda r: ok([]))
    with pytest.raises(ValueError):
        run(c.get_klines("BTCUSDT", "7m"))
    with pytest.raises(ValueError):
        run(c.get_klines("BTCUSDT", "1h", limit=201))


def test_get_klines_since_blaettert_rueckwaerts():
    # 450 Kerzen verfügbar: open_time = 1..450 * H
    all_times = [i * H for i in range(1, 451)]
    calls = []

    def handler(req: httpx.Request):
        end = req.url.params.get("endTime")
        limit = int(req.url.params["limit"])
        calls.append(end)
        avail = [t for t in all_times if end is None or t < int(end)]  # endTime exklusiv
        page = avail[-limit:]
        return ok([raw_kline(t) for t in reversed(page)])

    async def go():
        async with make_client(handler) as c:
            return await c.get_klines_since("BTCUSDT", "1h", start_time=100 * H)

    klines = run(go())
    times = [k.open_time for k in klines]
    assert times == [i * H for i in range(100, 451)]
    assert len(calls) == 2  # 200 + 200 reicht bis 51*H < 100*H
    assert calls[0] is None and calls[1] == str(251 * H)


def test_retry_bei_rate_limit_code_dann_erfolg():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] < 3:
            return httpx.Response(200, json={"code": 10006, "msg": "Request too frequently"})
        return ok([raw_kline(H)])

    async def go():
        async with make_client(handler) as c:
            return await c.get_klines("BTCUSDT", "1h")

    assert len(run(go())) == 1
    assert n["calls"] == 3


def test_retry_bei_http_503_und_netzwerkfehler():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        if n["calls"] == 1:
            raise httpx.ConnectError("boom")
        if n["calls"] == 2:
            return httpx.Response(503, text="unavailable")
        return ok([])

    async def go():
        async with make_client(handler) as c:
            return await c.get_tickers()

    assert run(go()) == []
    assert n["calls"] == 3


def test_kein_retry_bei_parameterfehler():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(200, json={"code": 10002, "msg": "Parameter Error"})

    async def go():
        async with make_client(handler) as c:
            await c.get_funding_rate("XXX")

    with pytest.raises(BitunixAPIError) as ei:
        run(go())
    assert ei.value.code == 10002
    assert n["calls"] == 1


def test_gibt_nach_max_retries_auf():
    n = {"calls": 0}

    def handler(req):
        n["calls"] += 1
        return httpx.Response(429, text="slow down")

    async def go():
        async with make_client(handler) as c:
            await c.get_tickers()

    with pytest.raises(BitunixAPIError) as ei:
        run(go())
    assert ei.value.status == 429
    assert n["calls"] == 5  # 1 + 4 Retries


def test_get_tickers():
    def handler(req):
        assert req.url.params["symbols"] == "BTCUSDT,ETHUSDT"
        return ok([
            {"symbol": "BTCUSDT", "markPrice": "64472.7", "lastPrice": "66000", "open": "64000",
             "last": "66000", "quoteVol": "1643831028.8", "baseVol": "25304.9",
             "high": "66092.3", "low": "63738.9"},
        ])

    async def go():
        async with make_client(handler) as c:
            return await c.get_tickers(["BTCUSDT", "ETHUSDT"])

    (t,) = run(go())
    assert t.symbol == "BTCUSDT" and t.last_price == 66000.0
    assert t.change_24h_pct == pytest.approx(3.125)


def test_get_funding_rate_objekt_und_liste():
    payload = {"symbol": "BTCUSDT", "markPrice": "64472.7", "lastPrice": "64489.9",
               "indexPrice": "64511.9", "fundingRate": "0.000105", "fundingInterval": 8,
               "nextFundingTime": "1794816000000", "maxFundingRate": "0.3", "minFundingRate": "-0.3"}

    for data in (payload, [payload]):
        async def go(data=data):
            async with make_client(lambda r: ok(data)) as c:
                return await c.get_funding_rate("BTCUSDT")

        fr = run(go())
        assert fr.funding_rate == pytest.approx(0.000105)
        assert fr.funding_interval_h == 8
        assert fr.next_funding_time == 1794816000000
