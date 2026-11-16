"""Welche Signale werden als Alert verschickt? Filter + Cooldown gegen Spam."""

from __future__ import annotations

import html
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from signals.models import DIRECTION_LABEL, STRENGTH_LABEL, Signal, now_ms
from signals.rules import fmt_price


@dataclass(frozen=True)
class AlertPolicy:
    """Regeln für den Versand.

    min_strength: nur Signale ab dieser Stärke (1–3)
    timeframes:   nur diese Timeframes (None = alle); Funding heißt "funding"
    cooldown_min: gleiche Kombination (Symbol, Timeframe, Regel, Richtung)
                  frühestens nach so vielen Minuten erneut melden
    """

    min_strength: int = 2
    timeframes: frozenset[str] | None = None
    cooldown_min: int = 60

    def matches(self, s: Signal) -> bool:
        if s.strength < self.min_strength:
            return False
        return self.timeframes is None or s.timeframe in self.timeframes


_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts_sent (
    symbol     TEXT    NOT NULL,
    timeframe  TEXT    NOT NULL,
    rule       TEXT    NOT NULL,
    direction  TEXT    NOT NULL,
    sent_at    INTEGER NOT NULL,   -- ms UTC
    PRIMARY KEY (symbol, timeframe, rule, direction)
);
"""


class CooldownStore:
    """Merkt sich in SQLite, wann welche Kombination zuletzt gemeldet wurde.

    Dadurch gilt der Cooldown auch über Neustarts hinweg.
    """

    def __init__(self, path: str | Path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> CooldownStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def last_sent(self, s: Signal) -> int | None:
        row = self._conn.execute(
            "SELECT sent_at FROM alerts_sent WHERE symbol=? AND timeframe=? AND rule=? AND direction=?",
            (s.symbol, s.timeframe, s.rule, s.direction),
        ).fetchone()
        return row[0] if row else None

    def mark_sent(self, signals: list[Signal], at: int) -> None:
        with self._conn:
            self._conn.executemany(
                "INSERT INTO alerts_sent (symbol, timeframe, rule, direction, sent_at) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT(symbol, timeframe, rule, direction) "
                "DO UPDATE SET sent_at = excluded.sent_at",
                [(s.symbol, s.timeframe, s.rule, s.direction, at) for s in signals],
            )


def select_alerts(
    signals: list[Signal],
    policy: AlertPolicy,
    cooldowns: CooldownStore,
    now: int | None = None,
) -> list[Signal]:
    """Filtert nach Policy und Cooldown. Pro Kombination höchstens ein Alert,
    bei mehreren gewinnt das stärkste/neueste Signal."""
    now = now if now is not None else now_ms()
    cooldown_ms = policy.cooldown_min * 60_000
    best: dict[tuple, Signal] = {}
    for s in signals:
        if not policy.matches(s):
            continue
        last = cooldowns.last_sent(s)
        if last is not None and now - last < cooldown_ms:
            continue
        k = (s.symbol, s.timeframe, s.rule, s.direction)
        if k not in best or (s.strength, s.candle_time) > (best[k].strength, best[k].candle_time):
            best[k] = s
    return sorted(best.values(), key=lambda s: (-s.strength, s.symbol, s.timeframe))


def format_alert(signals: list[Signal]) -> str:
    """Eine Telegram-Nachricht (HTML) für alle Signale eines Durchlaufs."""
    lines = [f"<b>📊 {len(signals)} neue{'s' if len(signals) == 1 else ''} Signal"
             f"{'' if len(signals) == 1 else 'e'}</b>", ""]
    for s in signals:
        arrow = "🟢 ▲" if s.direction == "long" else "🔴 ▼"
        t = datetime.fromtimestamp(s.candle_time / 1000, tz=UTC).strftime("%d.%m. %H:%M UTC")
        when = f"Abrechnung {t}" if s.timeframe == "funding" else f"Kerze {t}"
        lines.append(
            f"{arrow} <b>{html.escape(s.symbol)}</b> {html.escape(s.timeframe)} · "
            f"{DIRECTION_LABEL[s.direction]} · {STRENGTH_LABEL[s.strength]}"
        )
        lines.append(html.escape(s.reason))
        lines.append(f"<i>Preis {fmt_price(s.price)} · {when}</i>")
        lines.append("")
    lines.append("<i>Hinweis, keine Anlageempfehlung.</i>")
    return "\n".join(lines)


async def dispatch(
    signals: list[Signal],
    policy: AlertPolicy,
    cooldowns: CooldownStore,
    notifier=None,
    *,
    now: int | None = None,
) -> list[Signal]:
    """Wählt die zu meldenden Signale aus und verschickt sie als EINE Nachricht.

    Ohne `notifier` (kein Telegram konfiguriert) wird nichts verschickt und
    kein Cooldown gesetzt – die Auswahl wird nur zurückgegeben.
    Der Cooldown wird erst nach erfolgreichem Versand gesetzt.
    """
    now = now if now is not None else now_ms()
    chosen = select_alerts(signals, policy, cooldowns, now)
    if chosen and notifier is not None:
        await notifier.send(format_alert(chosen))
        cooldowns.mark_sent(chosen, now)
    return chosen
