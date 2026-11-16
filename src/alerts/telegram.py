"""Telegram-Versand über die Bot-API (sendMessage).

Der Bot-Token steckt in der URL – deshalb wird er in allen Fehlermeldungen
geschwärzt und httpx sollte nicht auf INFO loggen (die Skripte setzen den
httpx-Logger auf WARNING).
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org"
MAX_MESSAGE_LEN = 4096  # Limit der Telegram-API


class TelegramError(Exception):
    """Senden an Telegram fehlgeschlagen (Token ist geschwärzt)."""


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: str,
        *,
        timeout: float = 10.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        if not token or not chat_id:
            raise ValueError("TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID müssen gesetzt sein")
        self._token = token
        self._chat_id = chat_id
        self._max_retries = max_retries
        self._client = httpx.AsyncClient(base_url=API_URL, timeout=timeout, transport=transport)

    async def __aenter__(self) -> TelegramNotifier:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.aclose()

    def _redact(self, text: str) -> str:
        return text.replace(self._token, "***")

    async def send(self, text: str) -> None:
        """Sendet eine Nachricht (HTML-Format). Lange Texte werden aufgeteilt."""
        for chunk in split_message(text):
            await self._send_one(chunk)

    async def _send_one(self, text: str) -> None:
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        for attempt in range(1, self._max_retries + 2):
            try:
                resp = await self._client.post(f"/bot{self._token}/sendMessage", json=payload)
            except httpx.TransportError as e:
                err = f"Netzwerkfehler: {type(e).__name__}"
                wait = 2.0 ** attempt
            else:
                if resp.status_code == 200 and resp.json().get("ok"):
                    return
                body = _safe_json(resp)
                desc = body.get("description", resp.text[:200])
                err = f"HTTP {resp.status_code}: {desc}"
                if resp.status_code == 429:
                    wait = float(body.get("parameters", {}).get("retry_after", 5))
                elif resp.status_code >= 500:
                    wait = 2.0 ** attempt
                else:
                    raise TelegramError(self._redact(err))  # z. B. falscher Token/Chat
            if attempt > self._max_retries:
                raise TelegramError(self._redact(err))
            log.warning("Telegram: %s – neuer Versuch in %.0fs", self._redact(err), wait)
            await asyncio.sleep(wait)


def _safe_json(resp: httpx.Response) -> dict:
    try:
        data = resp.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def split_message(text: str, limit: int = MAX_MESSAGE_LEN) -> list[str]:
    """Teilt an Zeilenumbrüchen in Stücke <= limit Zeichen."""
    if len(text) <= limit:
        return [text]
    chunks, current = [], ""
    for line in text.split("\n"):
        while len(line) > limit:  # einzelne überlange Zeile hart teilen
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks
