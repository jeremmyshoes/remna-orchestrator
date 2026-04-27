"""Tests for the notifications module."""

from __future__ import annotations

import os

import pytest


@pytest.mark.asyncio
async def test_multi_notifier_empty_no_error() -> None:
    from app.notifications.base import NotificationEvent
    from app.notifications.multiplexer import MultiNotifier

    n = MultiNotifier([])
    await n.send(
        NotificationEvent(kind="rotation_success", message="ok"),
    )
    await n.aclose()


def test_event_to_markdown() -> None:
    from app.notifications.base import NotificationEvent

    e = NotificationEvent(
        kind="rotation_success",
        message="replaced node",
        reason="manual",
        from_node="node-a",
        to_node="node-b",
        extra={"ip": "1.2.3.4"},
    )
    md = e.to_markdown()
    assert "rotation_success" in md
    assert "manual" in md
    assert "node-a" in md
    assert "node-b" in md
    assert "1.2.3.4" in md


def test_build_notifier_no_creds_returns_empty() -> None:
    os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    os.environ.pop("TELEGRAM_CHAT_ID", None)
    os.environ.pop("DISCORD_WEBHOOK_URL", None)

    from app.core.config import get_settings
    get_settings.cache_clear()

    from app.notifications.multiplexer import build_notifier

    m = build_notifier()
    assert m.notifiers == []
