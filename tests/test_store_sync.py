"""Tests für den SQLite-Speicher und das inkrementelle Laden."""

import asyncio

import httpx

from bitunix.client import BitunixPublicClient
from bitunix.models import Kline
from data.store import CandleStore
from data.sync import sync_candles

H = 3_600_000


def k(t: int, close: float = 100.0) -> Kline:
    return Kline(open_time=t, open=99, high=101, low=98, close=close, base_vol=10, quote_vol=1000)


def test_upsert_ueberschreibt_und_laedt_sortiert():
    with CandleStore(":memory:") as s:
        s.upsert("BTCUSDT", "1h", [k(2 * H), k(H)])
        s.upsert("BTCUSDT", "1h", [k(2 * H, close=200.0), k(3 * H)])
        s.upsert("ETHUSDT", "1h", [k(H)])
        assert s.count("BTCUSDT", "1h") == 3
        assert s.latest_open_time("BTCUSDT", "1h") == 3 * H
        assert s.latest_open_time("BTCUSDT", "4h") is None
        loaded = s.load("BTCUSDT", "1h")
        assert [x.open_time for x in loaded] == [H, 2 * H, 3 * H]
        assert loaded[1].close == 200.0
        assert [x.open_time for x in s.load("BTCUSDT", "1h", limit=2)] == [2 * H, 3 * H]


def test_store_legt_ordner_an(tmp_path):
    db = tmp_path / "sub" / "market.db"
    with CandleStore(db) as s:
        s.upsert("BTCUSDT", "1h", [k(H)])
    with CandleStore(db) as s:
        assert s.count("BTCUSDT", "1h") == 1


def test_sync_backfill_dann_inkrementell():
    available = {"times": [i * H for i in range(1, 1001)]}
    requested_ends = []

    def handler(req: httpx.Request):
        end = req.url.params.get("endTime")
        limit = int(req.url.params["limit"])
        requested_ends.append(end)
        avail = [t for t in available["times"] if end is None or t < int(end)]
        page = avail[-limit:]
        data = [{"open": "1", "high": "1", "low": "1", "close": "1", "quoteVol": "1",
                 "baseVol": "1", "time": str(t)} for t in reversed(page)]
        return httpx.Response(200, json={"code": 0, "msg": "Success", "data": data})

    async def go():
        client = BitunixPublicClient(transport=httpx.MockTransport(handler),
                                     backoff_base=0, rate_limit_per_sec=1000)
        with CandleStore(":memory:") as store:
            async with client:
                now = 1001 * H
                r1 = await sync_candles(client, store, "BTCUSDT", "1h", backfill=300, now_ms=now)
                # zwei neue Kerzen erscheinen
                available["times"] += [1001 * H, 1002 * H]
                requested_ends.clear()
                r2 = await sync_candles(client, store, "BTCUSDT", "1h", now_ms=now + 2 * H)
                return r1, r2, list(requested_ends)

    r1, r2, ends2 = asyncio.run(go())
    assert r1.total == 300 and r1.latest_open_time == 1000 * H
    # inkrementell: letzte gespeicherte Kerze + 2 neue, nur eine Seite nötig
    assert r2.fetched == 3 and r2.total == 302 and r2.latest_open_time == 1002 * H
    assert ends2 == [None]


def test_load_df_fuer_indikatoren():
    with CandleStore(":memory:") as s:
        s.upsert("BTCUSDT", "1h", [k(2 * H, close=101.0), k(H)])
        df = s.load_df("BTCUSDT", "1h")
        assert list(df.columns) == ["open", "high", "low", "close", "volume", "quote_volume"]
        assert str(df.index.tz) == "UTC"
        assert df.index[0].value // 1_000_000 == H  # ns -> ms
        assert df["close"].tolist() == [100.0, 101.0]
        assert df["volume"].tolist() == [10.0, 10.0]
        assert s.load_df("BTCUSDT", "4h").empty
