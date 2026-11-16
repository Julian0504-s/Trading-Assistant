"""Tests für Risk Manager, simuliertes Konto, Strategien und Backtest."""

import math

import numpy as np
import pandas as pd
import pytest

from risk import RiskConfig, RiskManager, RiskViolation, kill_switch_active
from signals import Signal
from strategy import STRATEGIES, SimAccount, export_orders_csv, run_backtest
from strategy import backtest as bt_module

H = 3_600_000
DAY = 24 * H


# ----------------------------------------------------------------- Risk Manager

def test_harte_limits_nicht_lockerbar():
    with pytest.raises(ValueError):
        RiskConfig(max_leverage=5)
    with pytest.raises(ValueError):
        RiskConfig(risk_per_trade=0.02)
    with pytest.raises(ValueError):
        RiskConfig(daily_loss_limit=0.10)
    RiskConfig(max_leverage=2, risk_per_trade=0.005)  # verschärfen geht


def test_positionsgroesse_nach_risiko_und_hebel():
    rm = RiskManager()
    # 1 % von 1000 = 10 USDT Risiko, Stop 2 USDT entfernt -> 5 Stück
    assert rm.size_position(1000, 100, 98) == pytest.approx(5)
    # sehr enger Stop -> Hebel-Limit 3x: max. 3000 USDT Volumen -> 30 Stück
    assert rm.size_position(1000, 100, 99.9) == pytest.approx(30)
    # bereits 2500 USDT offen -> nur noch 500 USDT Platz
    assert rm.size_position(1000, 100, 99.9, open_notional=2500) == pytest.approx(5)
    with pytest.raises(RiskViolation):
        rm.size_position(1000, 100, 99.9, open_notional=3000)


def test_stop_loss_pflicht():
    rm = RiskManager()
    for stop in (None, 0, 100):
        with pytest.raises(RiskViolation):
            rm.size_position(1000, 100, stop)


def test_tagesverlust_limit():
    rm = RiskManager(RiskConfig(daily_loss_limit=0.03))
    assert not rm.daily_limit_hit(0, 1000)          # Tagesbeginn 1000
    assert not rm.daily_limit_hit(H, 975)           # -2,5 %
    assert rm.daily_limit_hit(2 * H, 970)           # -3 % -> Schluss für heute
    assert not rm.daily_limit_hit(DAY + H, 970)     # neuer Tag, neuer Start


def test_kill_switch(tmp_path):
    f = tmp_path / "KILL_SWITCH"
    assert not kill_switch_active(str(f))
    f.write_text("stop")
    assert kill_switch_active(str(f))


# ---------------------------------------------------------------------- Konto

def account() -> SimAccount:
    return SimAccount(1000.0, fee_rate=0.001, slippage=0.0)


def test_long_bis_ziel_mit_gebuehren():
    acc = account()
    pos = acc.open(symbol="BTC", timeframe="1h", strategy="t", direction="long", price=100,
                   time=0, stop=98, target=104, reason="Test")
    assert pos.qty == pytest.approx(5)
    assert acc.check_exit(pos, 101, 103, 99, H) is None           # nichts berührt
    trade = acc.check_exit(pos, 102, 105, 101, 2 * H)
    assert trade.exit_reason == "Kursziel" and trade.exit_price == 104
    # Gewinn 5 * 4 = 20, Gebühren 0,1 % von 500 und 520
    assert trade.pnl == pytest.approx(20 - 0.5 - 0.52)
    assert trade.r_multiple == pytest.approx(trade.pnl / 10)
    assert acc.cash == pytest.approx(1000 + trade.pnl)
    assert [o.action for o in acc.orders] == ["Eröffnung", "Schließung"]


def test_short_stop_und_konservative_reihenfolge():
    acc = account()
    pos = acc.open(symbol="BTC", timeframe="1h", strategy="t", direction="short", price=100,
                   time=0, stop=102, target=96, reason="Test")
    # Kerze berührt Stop UND Ziel -> Stop zählt
    trade = acc.check_exit(pos, 100, 103, 95, H)
    assert trade.exit_reason == "Stop-Loss" and trade.exit_price == 102
    assert trade.pnl < 0 and trade.r_multiple == pytest.approx(-1.0, abs=0.12)


def test_kurs_luecke_wird_zum_eroeffnungskurs_ausgefuehrt():
    acc = account()
    pos = acc.open(symbol="BTC", timeframe="1h", strategy="t", direction="long", price=100,
                   time=0, stop=98, target=104, reason="Test")
    trade = acc.check_exit(pos, 95, 96, 94, H)
    assert trade.exit_reason == "Stop-Loss (Kurslücke)" and trade.exit_price == 95


def test_keine_zweite_position_pro_symbol_und_timeframe():
    acc = account()
    kw = dict(symbol="BTC", timeframe="1h", strategy="t", direction="long", price=100,
              time=0, stop=98, target=104, reason="x")
    assert acc.open(**kw) is not None
    assert acc.open(**kw) is None


def test_csv_export_deutsches_format(tmp_path):
    acc = account()
    pos = acc.open(symbol="BTC", timeframe="1h", strategy="t", direction="long", price=100.5,
                   time=0, stop=98, target=104, reason="Golden Cross")
    acc.close(pos, 101, H, "Test")
    path = export_orders_csv(acc.orders, tmp_path / "orders.csv")
    text = path.read_text(encoding="utf-8-sig").splitlines()
    assert text[0].startswith("Zeit (UTC);Modus;Strategie")
    assert "1970-01-01 00:00:00;backtest;t;BTC;1h;Eröffnung;Kauf;100,5;" in text[1]
    assert "Golden Cross" in text[1] and len(text) == 3


# ------------------------------------------------------------------ Strategien

def row(close=100.0, ema200=90.0) -> pd.Series:
    return pd.Series({"close": close, "ema_200": ema200})


def sig(rule="ema_cross", direction="long", strength=2) -> Signal:
    return Signal("BTC", "1h", rule, direction, strength, "x", 100.0, 0)


def test_trendfolge_nur_mit_dem_trend():
    s = STRATEGIES["trend"]
    assert s.pick([sig()], row()) is not None
    assert s.pick([sig(direction="short")], row()) is None           # gegen Trend
    assert s.pick([sig(rule="rsi_extreme")], row()) is None          # Regel nicht dabei
    assert s.pick([sig(strength=1)], row()) is None                  # zu schwach
    assert s.pick([sig()], row(ema200=float("nan"))) is None         # kein Trend bekannt


def test_widerspruechliche_signale_werden_ignoriert():
    s = STRATEGIES["all_medium"]
    assert s.pick([sig(), sig(rule="rsi_extreme", direction="short")], row()) is None
    assert s.pick([sig(), sig(rule="bb_breakout", strength=3)], row()).rule == "bb_breakout"


def test_stop_und_ziel_aus_atr():
    s = STRATEGIES["trend"]
    assert s.stop_and_target("long", 100, 2) == (97, 106)
    assert s.stop_and_target("short", 100, 2) == (103, 94)


# -------------------------------------------------------------------- Backtest

def frame(n=260) -> pd.DataFrame:
    idx = pd.to_datetime([i * H for i in range(n)], unit="ms", utc=True)
    close = np.full(n, 100.0)
    df = pd.DataFrame({"open": close, "high": close + 0.5, "low": close - 0.5, "close": close,
                       "volume": 1.0, "atr_14": 2.0, "ema_200": 90.0}, index=idx)
    return df


def test_backtest_einstieg_naechste_kerze_und_ziel(monkeypatch):
    df = frame()
    # Kerze 210: Signal am Schluss. 211 öffnet bei 101, 213 erreicht das Ziel (101 + 6)
    df.iloc[211, df.columns.get_loc("open")] = 101.0
    df.iloc[213, df.columns.get_loc("high")] = 108.0
    signal = Signal("BTC", "1h", "ema_cross", "long", 2, "Golden Cross", 100.0, 210 * H)
    monkeypatch.setattr(bt_module, "evaluate_candles", lambda d, s, tf: [signal])
    res = run_backtest({"BTC": df}, "1h", STRATEGIES["trend"], start_equity=1000)
    (trade,) = res.trades
    assert trade.entry_time == 211 * H
    assert trade.entry_price == pytest.approx(101 * 1.0002)          # Slippage
    assert trade.exit_reason == "Kursziel" and trade.exit_time == 214 * H   # Schluss von Kerze 213
    assert trade.r_multiple == pytest.approx(2.0, abs=0.1)            # Chance-Risiko 1:2
    assert res.metrics["Trades"] == 1 and res.metrics["Trefferquote %"] == 100
    assert res.end_equity == pytest.approx(1000 + trade.pnl)
    assert len(res.equity) == len(df) - 200   # ein Punkt je Kerzenschluss nach der Aufwärmphase


def test_backtest_kein_blick_in_die_zukunft(monkeypatch):
    df = frame()
    last = Signal("BTC", "1h", "ema_cross", "long", 2, "x", 100.0, 259 * H)   # letzte Kerze
    early = Signal("BTC", "1h", "ema_cross", "long", 3, "x", 100.0, 50 * H)   # in Aufwärmphase
    monkeypatch.setattr(bt_module, "evaluate_candles", lambda d, s, tf: [early, last])
    res = run_backtest({"BTC": df}, "1h", STRATEGIES["trend"])
    assert res.trades == [] and res.end_equity == 1000


def test_backtest_offene_position_am_ende_geschlossen(monkeypatch):
    df = frame()
    signal = Signal("BTC", "1h", "ema_cross", "long", 2, "x", 100.0, 220 * H)
    monkeypatch.setattr(bt_module, "evaluate_candles", lambda d, s, tf: [signal])
    res = run_backtest({"BTC": df}, "1h", STRATEGIES["trend"])
    (trade,) = res.trades
    assert trade.exit_reason == "Backtest-Ende"
    assert not math.isnan(res.metrics["Max. Drawdown %"])


def test_backtest_mit_echten_regeln_laeuft():
    from indicators import add_all

    rng = np.random.default_rng(1)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, 1500)))
    idx = pd.to_datetime([i * H for i in range(1500)], unit="ms", utc=True)
    df = pd.DataFrame({"open": np.r_[close[0], close[:-1]], "close": close,
                       "high": close * 1.004, "low": close * 0.996,
                       "volume": rng.uniform(5, 15, 1500)}, index=idx)
    df["high"] = df[["open", "close", "high"]].max(axis=1)
    df["low"] = df[["open", "close", "low"]].min(axis=1)
    for name in STRATEGIES:
        res = run_backtest({"X": add_all(df)}, "1h", STRATEGIES[name])
        assert res.metrics["Trades"] > 0
        # Risiko pro Trade nie über 1 % (+ Gebühren/Slippage)
        assert min(t.pnl for t in res.trades) > -0.0125 * res.equity.max()


# ------------------------------------------------- Trailing-Stop, ADX, Zeitfenster

def test_trailing_stop_zieht_nach_und_sichert_gewinn():
    acc = account()
    pos = acc.open(symbol="BTC", timeframe="1h", strategy="t", direction="long", price=100,
                   time=0, stop=96, target=math.inf, reason="x", trail_atr=3)
    assert acc.check_exit(pos, 100, 105, 99, H, atr=1) is None
    assert pos.stop == 102                                   # 105 - 3 * 1
    assert acc.check_exit(pos, 104, 104.5, 103, 2 * H, atr=1) is None
    assert pos.stop == 102                                   # zieht nie zurück
    trade = acc.check_exit(pos, 103, 103, 101, 3 * H, atr=1)
    assert trade.exit_reason == "Trailing-Stop" and trade.exit_price == 102
    # R bezogen auf den Anfangs-Stop: +2 je Stück bei 4 Risiko = 0,5 R, abzüglich Gebühren
    assert trade.r_multiple == pytest.approx(0.4495, abs=0.001)


def test_trailing_stop_short():
    acc = account()
    pos = acc.open(symbol="BTC", timeframe="1h", strategy="t", direction="short", price=100,
                   time=0, stop=104, target=-math.inf, reason="x", trail_atr=2)
    acc.check_exit(pos, 100, 100.5, 95, H, atr=1)
    assert pos.stop == 97
    assert acc.check_exit(pos, 96, 97.5, 96, 2 * H).exit_reason == "Trailing-Stop"


def test_adx_filter_und_ziel_ohne_grenze():
    from strategy import ExitConfig, Strategy

    s = Strategy("x", "x", "x", frozenset({"ema_cross"}), adx_min=25,
                 exits=ExitConfig(stop_atr=2, target_atr=None, trail_atr=3))
    r = pd.Series({"close": 100.0, "ema_200": 90.0, "adx_14": 30.0})
    assert s.pick([sig()], r) is not None
    r["adx_14"] = 20.0
    assert s.pick([sig()], r) is None
    stop, target = s.stop_and_target("long", 100, 2)
    assert stop == 96 and target == math.inf


def test_backtest_zeitfenster(monkeypatch):
    df = frame(400)
    signals = [Signal("BTC", "1h", "ema_cross", "long", 2, "x", 100.0, t * H) for t in (220, 330)]
    monkeypatch.setattr(bt_module, "evaluate_candles", lambda d, s, tf: signals)
    res = run_backtest({"BTC": df}, "1h", STRATEGIES["trend"], trade_from=300 * H,
                       trade_until=399 * H)
    # Signal bei 220 liegt vor dem Fenster und wird ignoriert, 330 wird gehandelt
    assert [t.entry_time for t in res.trades] == [331 * H]
    assert res.equity.index[0] == pd.Timestamp(301 * H, unit="ms", tz="UTC")  # erster Schluss
    assert "Wochen im Plus %" in res.metrics


def test_trend_trail_strategie():
    s = STRATEGIES["trend_trail"]
    assert s.exits.trail_atr == 4 and s.exits.target_atr is None
    stop, target = s.stop_and_target("long", 100, 2)
    assert stop == 94 and target == math.inf



# ---------------------------------------------- Ideen: Long-only, BTC-Filter, Nachkauf

def test_long_only_und_btc_filter():
    from strategy import Strategy

    s = Strategy("x", "x", "x", frozenset({"ema_cross"}), long_only=True)
    assert s.pick([sig(direction="short")], row(ema200=110)) is None
    b = Strategy("x", "x", "x", frozenset({"ema_cross"}), btc_filter=True)
    r = pd.Series({"close": 100.0, "ema_200": 90.0, "btc_up": True})
    assert b.pick([sig()], r) is not None
    assert b.pick([sig(direction="short")], r) is None
    r["btc_up"] = False
    assert b.pick([sig()], r) is None and b.pick([sig(direction="short")], r) is not None
    r["btc_up"] = None
    assert b.pick([sig()], r) is None


def test_btc_regime_spalte():
    from strategy.regime import add_btc_regime

    idx = pd.to_datetime([0, H, 2 * H], unit="ms", utc=True)
    btc = pd.DataFrame({"close": [100, 120, 80], "ema_200": [np.nan, 110, 110]}, index=idx)
    eth = pd.DataFrame({"close": [1, 2, 3]}, index=idx)
    out = add_btc_regime({"BTCUSDT": btc, "ETHUSDT": eth})
    assert out["ETHUSDT"]["btc_up"].tolist()[1:] == [True, False]
    assert pd.isna(out["ETHUSDT"]["btc_up"].iloc[0])
    assert "btc_up" not in eth  # Original unverändert


def test_nachkauf_nur_ohne_verlustrisiko():
    acc = account()
    pos = acc.open(symbol="BTC", timeframe="1d", strategy="t", direction="long", price=100,
                   time=0, stop=94, target=math.inf, reason="x", trail_atr=4)
    assert not acc.add_to(pos, 104, H, "x")                 # Stop noch unter Einstieg
    acc.check_exit(pos, 110, 115, 109, H, atr=2)             # Trailing: 115 - 8 = 107
    assert pos.stop == 107 and pos.risk_free()
    risk_before = pos.risk_amount
    assert acc.add_to(pos, 112, 2 * H, "Nachkauf")
    # neuer Teil riskiert 1 % des Kontos bis zum Stop 107 -> ~10 USDT
    assert pos.risk_amount - risk_before == pytest.approx(0.01 * acc.equity(), rel=0.02)
    assert pos.units == 2 and 100 < pos.entry_price < 112
    assert [o.action for o in acc.orders] == ["Eröffnung", "Aufstockung"]


def test_backtest_mit_nachkauf(monkeypatch):
    from strategy import ExitConfig, Strategy

    df = frame(300)
    # stetiger Anstieg ab Kerze 211: +1 je Kerze
    for i in range(211, 300):
        for c in ("open", "high", "low", "close"):
            df.iloc[i, df.columns.get_loc(c)] = 100 + (i - 210) + {"open": 0, "high": 0.5,
                                                                    "low": -0.5, "close": 0.5}[c]
    signal = Signal("BTC", "1h", "ema_cross", "long", 2, "x", 100.0, 210 * H)
    monkeypatch.setattr(bt_module, "evaluate_candles", lambda d, s, tf: [signal])
    strat = Strategy("p", "p", "p", frozenset({"ema_cross"}), with_trend_only=True,
                     exits=ExitConfig(stop_atr=3, target_atr=None, trail_atr=4),
                     pyramid_max=2, pyramid_step_atr=2)
    res = run_backtest({"BTC": df}, "1h", strat)
    adds = [o for o in res.orders if o.action == "Aufstockung"]
    assert len(adds) == 2                                     # höchstens pyramid_max
    (trade,) = res.trades
    assert trade.pnl > 0


def test_kombiniertes_konto_kennt_tagesschluss_erst_am_tagesende(monkeypatch):
    from strategy import STRATEGIES
    from strategy.backtest import Stream, run_streams

    D = 24 * H
    n_days = 230
    idx_d = pd.to_datetime([i * D for i in range(n_days)], unit="ms", utc=True)
    daily = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
                          "atr_14": 2.0, "ema_200": 90.0}, index=idx_d)
    daily.iloc[221, daily.columns.get_loc("close")] = 200.0   # Tag 221 schließt bei 200
    daily.iloc[221, daily.columns.get_loc("high")] = 200.0
    four = 4 * H
    idx_4 = pd.to_datetime([i * four for i in range(n_days * 6)], unit="ms", utc=True)
    h4 = pd.DataFrame({"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.0,
                       "atr_14": 1.0, "ema_200": 90.0}, index=idx_4)
    last4 = 221 * 6 + 5                                       # letzte 4h-Kerze von Tag 221
    h4.iloc[last4, h4.columns.get_loc("close")] = 200.0      # konsistent mit dem Tagesschluss
    h4.iloc[last4, h4.columns.get_loc("high")] = 200.0
    sig_d = {220 * D: [Signal("BTC", "1d", "ema_cross", "long", 2, "x", 100.0, 220 * D)]}
    res = run_streams([Stream("BTC", "1d", daily, STRATEGIES["trend_trail"], sig_d),
                       Stream("BTC", "4h", h4, STRATEGIES["trend_trail"], {})])
    eq = res.equity
    during = eq[(eq.index > pd.Timestamp(221 * D, unit="ms", tz="UTC"))
                & (eq.index < pd.Timestamp(222 * D, unit="ms", tz="UTC"))]
    assert len(during) == 5 and (during < 1001).all()        # tagsüber: Kurs 100 bekannt
    assert eq[pd.Timestamp(222 * D, unit="ms", tz="UTC")] > 1050   # erst am Tagesende: 200
