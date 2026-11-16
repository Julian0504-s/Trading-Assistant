"""Tests für Telegram-Alerts: Filter, Cooldown, Formatierung, Versand (gemockt)."""

import asyncio

import httpx
import pytest

from alerts import (
    AlertPolicy,
    CooldownStore,
    TelegramError,
    TelegramNotifier,
    dispatch,
    format_alert,
    select_alerts,
    split_message,
)
from signals import Signal

MIN = 60_000
TOKEN = "123456:GEHEIMER-TOKEN"


def sig(sym="BTCUSDT", tf="1h", rule="ema_cross", direction="long", strength=2, t=0,
        reason="Golden Cross <EMA>") -> Signal:
    return Signal(sym, tf, rule, direction, strength, reason, 64511.8, t, created_at=0)


# ------------------------------------------------------------------------ Policy

def test_policy_filtert_staerke_und_timeframes():
    p = AlertPolicy(min_strength=2, timeframes=frozenset({"1h", "funding"}))
    assert p.matches(sig(strength=2))
    assert not p.matches(sig(strength=1))
    assert not p.matches(sig(tf="1m"))
    assert p.matches(sig(tf="funding", rule="funding_extreme"))
    assert AlertPolicy(min_strength=1).matches(sig(tf="3m", strength=1))


def test_cooldown_blockiert_wiederholung():
    p = AlertPolicy(min_strength=1, cooldown_min=60)
    with CooldownStore(":memory:") as cd:
        first = select_alerts([sig()], p, cd, now=0)
        assert len(first) == 1
        cd.mark_sent(first, at=0)
        assert select_alerts([sig(t=1)], p, cd, now=59 * MIN) == []        # noch im Cooldown
        assert len(select_alerts([sig(t=2)], p, cd, now=60 * MIN)) == 1    # Cooldown vorbei
        # andere Richtung / anderes Symbol / anderer Timeframe zählen getrennt
        assert len(select_alerts([sig(direction="short"), sig(sym="ETHUSDT"), sig(tf="4h")],
                                 p, cd, now=MIN)) == 3


def test_select_nimmt_staerkstes_pro_kombination():
    p = AlertPolicy(min_strength=1)
    with CooldownStore(":memory:") as cd:
        (s,) = select_alerts([sig(strength=1, t=1), sig(strength=3, t=0), sig(strength=2, t=2)],
                             p, cd, now=0)
        assert s.strength == 3


def test_cooldown_ueberlebt_neustart(tmp_path):
    db = tmp_path / "x.db"
    with CooldownStore(db) as cd:
        cd.mark_sent([sig()], at=1000)
    with CooldownStore(db) as cd:
        assert cd.last_sent(sig()) == 1000


# ------------------------------------------------------------------- Formatierung

def test_format_alert_escaped_html_und_hinweis():
    text = format_alert([sig(), sig(sym="ETHUSDT", direction="short", strength=3)])
    assert "2 neue Signale" in text
    assert "&lt;EMA&gt;" in text and "<EMA>" not in text  # Begründung wird escaped
    assert "<b>BTCUSDT</b> 1h · bullisch · mittel" in text
    assert "<b>ETHUSDT</b> 1h · bärisch · stark" in text
    assert "Preis 64511.8" in text
    assert "keine Anlageempfehlung" in text
    assert "1 neues Signal" in format_alert([sig()])


def test_split_message():
    assert split_message("kurz") == ["kurz"]
    lines = [f"Zeile {i:03d}" for i in range(100)]  # je 9 Zeichen
    parts = split_message("\n".join(lines), limit=100)
    assert all(len(p) <= 100 for p in parts)
    assert "\n".join(parts).split("\n") == lines
    assert split_message("x" * 250, limit=100) == ["x" * 100, "x" * 100, "x" * 50]


# ------------------------------------------------------------------ Telegram-Versand

def notifier(handler, **kw) -> TelegramNotifier:
    return TelegramNotifier(TOKEN, "42", transport=httpx.MockTransport(handler), **kw)


def test_telegram_send_ok():
    seen = {}

    def handler(req: httpx.Request):
        seen["path"] = req.url.path
        seen["body"] = req.content
        return httpx.Response(200, json={"ok": True, "result": {}})

    async def go():
        async with notifier(handler) as n:
            await n.send("Hallo <b>Welt</b>")

    asyncio.run(go())
    assert seen["path"] == f"/bot{TOKEN}/sendMessage"
    assert b'"chat_id":"42"' in seen["body"].replace(b" ", b"")
    assert b'"parse_mode":"HTML"' in seen["body"].replace(b" ", b"")


def test_telegram_fehler_schwaerzt_token():
    def handler(req):
        return httpx.Response(401, json={"ok": False, "description": f"Unauthorized {TOKEN}"})

    async def go():
        async with notifier(handler) as n:
            await n.send("x")

    with pytest.raises(TelegramError) as ei:
        asyncio.run(go())
    assert TOKEN not in str(ei.value) and "***" in str(ei.value)


def test_telegram_429_wartet_retry_after(monkeypatch):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    monkeypatch.setattr("alerts.telegram.asyncio.sleep", fake_sleep)
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"ok": False, "description": "Too Many Requests",
                                             "parameters": {"retry_after": 7}})
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with notifier(handler) as n:
            await n.send("x")

    asyncio.run(go())
    assert calls["n"] == 2 and sleeps == [7.0]


def test_telegram_netzwerkfehler_gibt_irgendwann_auf(monkeypatch):
    async def fake_sleep(s):
        pass

    monkeypatch.setattr("alerts.telegram.asyncio.sleep", fake_sleep)

    def handler(req):
        raise httpx.ConnectError(f"kaputt {req.url}")

    async def go():
        async with notifier(handler, max_retries=2) as n:
            await n.send("x")

    with pytest.raises(TelegramError) as ei:
        asyncio.run(go())
    assert TOKEN not in str(ei.value)


def test_notifier_braucht_token_und_chat():
    with pytest.raises(ValueError):
        TelegramNotifier("", "42")


# ----------------------------------------------------------------------- dispatch

class FakeNotifier:
    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    async def send(self, text):
        if self.fail:
            raise TelegramError("kaputt")
        self.sent.append(text)


def test_dispatch_eine_nachricht_und_cooldown_nach_versand():
    p = AlertPolicy(min_strength=2)
    n = FakeNotifier()
    with CooldownStore(":memory:") as cd:
        chosen = asyncio.run(dispatch([sig(), sig(sym="ETHUSDT"), sig(strength=1, rule="x")],
                                      p, cd, n, now=0))
        assert len(chosen) == 2 and len(n.sent) == 1
        assert asyncio.run(dispatch([sig(t=5)], p, cd, n, now=MIN)) == []


def test_dispatch_ohne_erfolg_kein_cooldown():
    p = AlertPolicy(min_strength=1)
    with CooldownStore(":memory:") as cd:
        with pytest.raises(TelegramError):
            asyncio.run(dispatch([sig()], p, cd, FakeNotifier(fail=True), now=0))
        assert cd.last_sent(sig()) is None
        # ohne Notifier: nur Auswahl, kein Cooldown
        assert len(asyncio.run(dispatch([sig()], p, cd, None, now=0))) == 1
        assert cd.last_sent(sig()) is None


# ------------------------------------------------------------------ Nachlade-Prüfung

def test_needs_sync():
    from bitunix.models import Kline
    from data.store import CandleStore
    from data.sync import needs_sync

    H = 3_600_000
    now = 1000 * H + 30_000  # 30 s nach Beginn der Stunde 1000 -> Kerze 999 ist fertig
    with CandleStore(":memory:") as store:
        assert needs_sync(store, "BTC", "1h", now)                       # leer
        store.upsert("BTC", "1h", [Kline(998 * H, 1, 1, 1, 1, 1, 1)])
        assert needs_sync(store, "BTC", "1h", now)                       # 999 fehlt
        store.upsert("BTC", "1h", [Kline(999 * H, 1, 1, 1, 1, 1, 1)])
        assert not needs_sync(store, "BTC", "1h", now)                   # aktuell
        assert not needs_sync(store, "BTC", "1h", 1000 * H + 1_000)      # Kulanzzeit
