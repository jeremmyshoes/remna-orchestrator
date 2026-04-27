"""Fan-out notifier — sends to all configured channels concurrently."""

from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.notifications.base import NotificationEvent, Notifier

log = logging.getLogger(__name__)


class MultiNotifier(Notifier):
    name = "multi"

    def __init__(self, notifiers: list[Notifier]) -> None:
        self.notifiers = notifiers

    async def send(self, event: NotificationEvent) -> None:
        if not self.notifiers:
            return
        results = await asyncio.gather(
            *(n.send(event) for n in self.notifiers), return_exceptions=True
        )
        for n, r in zip(self.notifiers, results, strict=False):
            if isinstance(r, Exception):
                log.warning("notifier %s failed: %r", n.name, r)

    async def aclose(self) -> None:
        await asyncio.gather(*(n.aclose() for n in self.notifiers), return_exceptions=True)


def build_notifier() -> MultiNotifier:
    s = get_settings()
    channels: list[Notifier] = []

    tg_token = s.telegram_bot_token.get_secret_value()
    if tg_token and s.telegram_chat_id:
        from app.notifications.telegram import TelegramNotifier
        channels.append(TelegramNotifier(bot_token=tg_token, chat_id=s.telegram_chat_id))

    dc_url = s.discord_webhook_url.get_secret_value()
    if dc_url:
        from app.notifications.discord import DiscordNotifier
        channels.append(DiscordNotifier(webhook_url=dc_url))

    if not channels:
        log.info("notifications: no channels configured")
    else:
        log.info("notifications: enabled channels=%s", [c.name for c in channels])
    return MultiNotifier(channels)
