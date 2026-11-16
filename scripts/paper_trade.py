"""Paper Trading: Strategien laufen live mit – Orders werden NUR simuliert und geloggt.

Aufruf:
    uv run python scripts/paper_trade.py                         # Standard-Konten (PAPER_RUNS)
    uv run python scripts/paper_trade.py --runs trend@1h,trend@4h
    uv run python scripts/paper_trade.py --status                # Kontostände anzeigen
    uv run python scripts/paper_trade.py --export orders.csv     # Order-Log als CSV

Jeder Eintrag "strategie@timeframe" ist ein eigenes Paper-Konto (Standard
1.000 USDT), damit sich Varianten vergleichen lassen. Der Zustand liegt in der
SQLite-DB und übersteht Neustarts. Beim ersten Start wird nicht rückwirkend
gehandelt – der Bot beobachtet ab dann.

Kill-Switch: Datei KILL_SWITCH im Projektordner anlegen -> alle Paper-Positionen
werden geschlossen und der Bot hält an.

Phase 3: DRY_RUN=true ist Pflicht. Es gibt keinen Code, der echte Orders sendet.
Keine Anlageempfehlung.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time

from alerts import TelegramError
from bitunix.client import BitunixError, BitunixPublicClient
from data.store import CandleStore
from data.sync import needs_sync, sync_candles
from indicators import add_all
from risk import RiskConfig, kill_switch_active
from signals.rules import fmt_price
from strategy import STRATEGIES, export_orders_csv
from strategy.paper import PaperStore, PaperTrader

from common import (
    DEFAULT_DB_PATH,
    DEFAULT_WATCHLIST,
    load_dotenv,
    notifier_from_env,
    setup_logging,
    split_list,
)

log = logging.getLogger("paper")

DEFAULT_RUNS = "trend@1h,trend@4h,trend@1d"
WINDOW = 400  # Kerzen pro Auswertung (EMA 200 braucht Vorlauf)


def parse_runs(value: str) -> list[tuple[str, str]]:
    runs = []
    for item in split_list(value):
        name, _, tf = item.partition("@")
        if name not in STRATEGIES or not tf:
            raise SystemExit(f"Ungültiger Eintrag {item!r} – Format strategie@timeframe, "
                             f"Strategien: {', '.join(STRATEGIES)}")
        runs.append((name, tf))
    return runs


def print_status(db: str) -> None:
    with PaperStore(db) as ps:
        accounts = ps.accounts()
        if accounts.empty:
            print("Noch keine Paper-Konten.")
            return
        print(f"{'Konto':<14}{'Kapital':>11}{'Rendite':>9}{'Trades':>8}{'Treffer':>9}{'offen':>7}")
        for _, a in accounts.iterrows():
            trades = ps.trades(a["name"])
            eq = ps.equity(a["name"])
            last = eq.iloc[-1] if len(eq) else a["cash"]
            wins = (trades["pnl"] > 0).sum() if len(trades) else 0
            hit = f"{100 * wins / len(trades):.0f}%" if len(trades) else "–"
            print(f"{a['name']:<14}{last:>11.2f}{(last / a['start_equity'] - 1) * 100:>+8.2f}%"
                  f"{len(trades):>8}{hit:>9}{len(ps.positions(a['name'])):>7}")


async def run(args: argparse.Namespace) -> None:
    symbols = split_list(args.symbols, upper=True)
    runs = parse_runs(args.runs)
    tfs = sorted({tf for _, tf in runs})
    notifier = notifier_from_env()
    risk = RiskConfig()

    with CandleStore(args.db) as candles, PaperStore(args.db) as ps:
        now = int(time.time() * 1000)
        traders = [PaperTrader(ps, f"{name}@{tf}", STRATEGIES[name], [tf],
                               start_equity=args.capital, risk=risk, now=now)
                   for name, tf in runs]
        log.info("Paper Trading (DRY_RUN) – Konten: %s · Symbole: %s · Telegram %s",
                 ", ".join(t.name for t in traders), ", ".join(symbols),
                 "aktiv" if notifier else "aus")
        async with BitunixPublicClient() as client:
            if notifier:
                await notifier.__aenter__()
            first = True
            try:
                while True:
                    started = time.monotonic()
                    try:
                        tickers = {t.symbol: t.last_price for t in await client.get_tickers(symbols)}
                        now = int(time.time() * 1000)
                        if kill_switch_active():
                            for tr in traders:
                                for trade in tr.close_all(tickers, now, "Kill-Switch"):
                                    log.warning("Kill-Switch: %s %s geschlossen, %+.2f USDT",
                                                tr.name, trade.symbol, trade.pnl)
                            log.warning("Kill-Switch aktiv – Bot angehalten.")
                            return
                        synced = False
                        for sym in symbols:
                            for tf in tfs:
                                if needs_sync(candles, sym, tf, now):
                                    await sync_candles(client, candles, sym, tf)
                                    synced = True
                        if synced or first:
                            first = False
                            frames = {(sym, tf): add_all(candles.load_df(sym, tf, limit=WINDOW))
                                      for sym in symbols for tf in tfs}
                            for tr in traders:
                                res = tr.step(frames, tickers, now)
                                await _report(tr, res, notifier)
                    except (BitunixError, TelegramError) as e:
                        log.error("Fehler im Durchlauf: %s", e)
                    except Exception:
                        log.exception("Fehler im Durchlauf")
                    await asyncio.sleep(max(1.0, args.interval - (time.monotonic() - started)))
            finally:
                if notifier:
                    await notifier.__aexit__(None, None, None)


async def _report(tr: PaperTrader, res, notifier) -> None:
    lines = []
    for p in res.opened:
        side = "Long" if p.direction == "long" else "Short"
        msg = (f"{tr.name}: {side} {p.symbol} @ {fmt_price(p.entry_price)} · Menge {p.qty:.6g} · "
               f"Stop {fmt_price(p.stop)} · Ziel {fmt_price(p.target)} – {p.reason}")
        log.info("📝 Eröffnet %s", msg)
        lines.append("📝 <b>Paper</b> eröffnet: " + msg)
    for t in res.closed:
        msg = (f"{tr.name}: {t.symbol} geschlossen @ {fmt_price(t.exit_price)} · "
               f"{t.pnl:+.2f} USDT ({t.r_multiple:+.2f} R) – {t.exit_reason}")
        log.info("📝 Geschlossen %s", msg)
        lines.append("📝 <b>Paper</b> " + msg)
    if lines:
        log.info("%s: Kontostand %.2f USDT", tr.name, tr.acc.equity())
        if notifier:
            await notifier.send("\n\n".join(lines) + "\n\n<i>Simulation, kein Echtgeld.</i>")


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Paper Trading (nur Simulation)")
    p.add_argument("--symbols", default=os.getenv("WATCHLIST", DEFAULT_WATCHLIST))
    p.add_argument("--runs", default=os.getenv("PAPER_RUNS", DEFAULT_RUNS),
                   help="Konten als strategie@timeframe, Komma-getrennt")
    p.add_argument("--capital", type=float, default=float(os.getenv("PAPER_CAPITAL", "1000")))
    p.add_argument("--db", default=os.getenv("DB_PATH", DEFAULT_DB_PATH))
    p.add_argument("--interval", type=int, default=60, help="Sekunden zwischen Durchläufen")
    p.add_argument("--status", action="store_true", help="Kontostände anzeigen und beenden")
    p.add_argument("--export", metavar="CSV", help="Order-Log aller Paper-Konten exportieren")
    args = p.parse_args()

    if os.getenv("DRY_RUN", "true").strip().lower() != "true":
        print("DRY_RUN ist nicht 'true'. Live-Trading gibt es erst in Phase 4 – abgebrochen.")
        return 2
    if args.status:
        print_status(args.db)
        return 0
    if args.export:
        with PaperStore(args.db) as ps:
            path = export_orders_csv(ps.orders(), args.export)
        print(f"Order-Log exportiert: {path}")
        return 0
    setup_logging()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        log.info("Beendet.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
