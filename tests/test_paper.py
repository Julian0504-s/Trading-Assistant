"""Tests für Paper Trading: Live-Schritte, Persistenz, Kill-Switch-Schließung."""

import numpy as np
import pandas as pd
import pytest

from signals import Signal
from strategy import STRATEGIES, export_orders_csv
from strategy import paper as paper_module
from strategy.paper import PaperStore, PaperTrader

H = 3_600_000


def frame(n: int) -> pd.DataFrame:
    idx = pd.to_datetime([i * H for i in range(n)], unit="ms", utc=True)
    c = np.full(n, 100.0)
    return pd.DataFrame({"open": c, "high": c + 0.5, "low": c - 0.5, "close": c,
                         "volume": 1.0, "atr_14": 2.0, "ema_200": 90.0}, index=idx)


@pytest.fixture
def signals_at(monkeypatch):
    """Steuert, an welchen Kerzen die Regeln ein Signal liefern."""
    times: dict[int, str] = {}

    def fake(df, sym, tf):
        return [Signal(sym, tf, "ema_cross", d, 2, "Golden Cross", 100.0, t)
                for t, d in times.items() if df.index[0].value // 1_000_000 <= t]

    monkeypatch.setattr(paper_module, "evaluate_candles", fake)
    return times


def test_erster_lauf_handelt_nicht_rueckwirkend(tmp_path, signals_at):
    signals_at[5 * H] = "long"
    with PaperStore(tmp_path / "p.db") as ps:
        tr = PaperTrader(ps, "trend@1h", STRATEGIES["trend"], ["1h"], now=0)
        res = tr.step({("BTC", "1h"): frame(10)}, {"BTC": 100.0}, now=10 * H)
        assert res.opened == [] and tr.progress["BTC|1h"] == 9 * H


def test_signal_an_neuester_kerze_eroeffnet_zum_live_kurs_und_ziel(tmp_path, signals_at):
    with PaperStore(tmp_path / "p.db") as ps:
        tr = PaperTrader(ps, "trend@1h", STRATEGIES["trend"], ["1h"], now=0)
        tr.step({("BTC", "1h"): frame(10)}, {"BTC": 100.0}, now=10 * H)
        signals_at[10 * H] = "long"
        (pos,) = tr.step({("BTC", "1h"): frame(11)}, {"BTC": 101.0}, now=11 * H + 5000).opened
        assert pos.entry_price == pytest.approx(101 * 1.0002) and pos.entry_time == 11 * H + 5000
        assert pos.target == pytest.approx(pos.entry_price + 6)       # 3 ATR
        df = frame(12)
        df.iloc[11, df.columns.get_loc("high")] = 110.0
        (trade,) = tr.step({("BTC", "1h"): df}, {"BTC": 108.0}, now=12 * H).closed
        assert trade.exit_reason == "Kursziel" and trade.pnl > 0
        assert ps.trades("trend@1h").iloc[0]["exit_reason"] == "Kursziel"
        assert [o.action for o in ps.orders("trend@1h")] == ["Eröffnung", "Schließung"]
        assert ps.equity("trend@1h").iloc[-1] == pytest.approx(1000 + trade.pnl)


def test_zustand_uebersteht_neustart(tmp_path, signals_at):
    db = tmp_path / "p.db"
    with PaperStore(db) as ps:
        tr = PaperTrader(ps, "trend@1h", STRATEGIES["trend"], ["1h"], now=0)
        tr.step({("BTC", "1h"): frame(10)}, {"BTC": 100.0}, now=10 * H)
        signals_at[10 * H] = "long"
        tr.step({("BTC", "1h"): frame(11)}, {"BTC": 100.0}, now=11 * H)
        cash = tr.acc.cash
    with PaperStore(db) as ps:
        tr2 = PaperTrader(ps, "trend@1h", STRATEGIES["trend"], ["1h"], now=0)
        assert tr2.acc.cash == pytest.approx(cash)
        assert ("BTC", "1h") in tr2.acc.positions and tr2.progress["BTC|1h"] == 10 * H
        # Neue Position bekommt eine neue ID
        assert tr2.acc.next_id == 2
        closed = tr2.close_all({"BTC": 99.0}, 12 * H, "Kill-Switch")
        assert closed[0].exit_reason == "Kill-Switch" and tr2.acc.positions == {}
        path = export_orders_csv(ps.orders(), tmp_path / "o.csv")
        assert "Kill-Switch" in path.read_text(encoding="utf-8-sig")


def test_aufholen_nach_pause_nutzt_schlusskurs(tmp_path, signals_at):
    with PaperStore(tmp_path / "p.db") as ps:
        tr = PaperTrader(ps, "trend@1h", STRATEGIES["trend"], ["1h"], now=0)
        tr.step({("BTC", "1h"): frame(10)}, {"BTC": 100.0}, now=10 * H)
        signals_at[11 * H] = "long"                      # nicht die neueste Kerze (13)
        (pos,) = tr.step({("BTC", "1h"): frame(14)}, {"BTC": 150.0}, now=14 * H).opened
        assert pos.entry_price == pytest.approx(100 * 1.0002)   # Schluss, nicht Live-Kurs
        assert pos.entry_time == 12 * H


def test_mehrere_konten_getrennt(tmp_path, signals_at):
    with PaperStore(tmp_path / "p.db") as ps:
        a = PaperTrader(ps, "trend@1h", STRATEGIES["trend"], ["1h"], start_equity=1000)
        b = PaperTrader(ps, "strong@1h", STRATEGIES["strong"], ["1h"], start_equity=500)
        assert list(ps.accounts()["name"]) == ["trend@1h", "strong@1h"]
        assert a.acc.cash == 1000 and b.acc.cash == 500
