"""Telegram bot notifier (sendMessage via Bot API)."""

from __future__ import annotations

import logging

import httpx

from app.notifications.base import NotificationEvent, Notifier

log = logging.getLogger(__name__)


class TelegramNotifier(Notifier):
    name = "telegram"

    def __init__(self, bot_token: str, chat_id: str, timeout: float = 10.0) -> None:
        self.bot_token = bot_token
        self.chat_id = chat_id
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send(self, event: NotificationEvent) -> None:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        try:
            resp = await self._client.post(
                url,
                json={
                    "chat_id": self.chat_id,
                    # Telegram's legacy Markdown would mis-parse our
                    # snake_case identifiers (e.g. `rotation_success`,
                    # `new_ip`) as italic. HTML is safer — only `<`, `>`, `&`
                    # need escaping, and `to_html()` handles that.
                    "text": event.to_html(),
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            if resp.status_code >= 400:
                log.warning("telegram notify failed: %s %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            log.warning("telegram notify transport error: %s", exc)

    async def aclose(self) -> None:
        await self._client.aclose()
