"""Discord webhook notifier."""

from __future__ import annotations

import logging

import httpx

from app.notifications.base import NotificationEvent, Notifier

log = logging.getLogger(__name__)


class DiscordNotifier(Notifier):
    name = "discord"

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        self.webhook_url = webhook_url
        self._client = httpx.AsyncClient(timeout=timeout)

    async def send(self, event: NotificationEvent) -> None:
        colour = {
            "rotation_success": 0x00C853,
            "node_spawned": 0x00C853,
            "rotation_started": 0x2196F3,
            "probe_failed": 0xFFC107,
            "rotation_failed": 0xD32F2F,
            "node_destroyed": 0x616161,
        }.get(event.kind, 0x9E9E9E)

        embed = {
            "title": event.kind.replace("_", " ").title(),
            "description": event.message,
            "color": colour,
            "fields": [],
        }
        if event.reason:
            embed["fields"].append({"name": "reason", "value": f"`{event.reason}`", "inline": True})
        if event.from_node:
            embed["fields"].append({"name": "from", "value": f"`{event.from_node}`", "inline": True})
        if event.to_node:
            embed["fields"].append({"name": "to", "value": f"`{event.to_node}`", "inline": True})
        for k, v in (event.extra or {}).items():
            embed["fields"].append({"name": str(k), "value": f"`{v}`", "inline": True})

        try:
            resp = await self._client.post(self.webhook_url, json={"embeds": [embed]})
            if resp.status_code >= 400:
                log.warning("discord notify failed: %s %s", resp.status_code, resp.text[:200])
        except httpx.HTTPError as exc:
            log.warning("discord notify transport error: %s", exc)

    async def aclose(self) -> None:
        await self._client.aclose()
