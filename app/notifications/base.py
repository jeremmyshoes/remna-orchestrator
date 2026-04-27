"""Abstract notifier — sends rotation / health events to external channels."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Literal


@dataclass(slots=True)
class NotificationEvent:
    kind: Literal[
        "rotation_started",
        "rotation_success",
        "rotation_failed",
        "node_spawned",
        "node_destroyed",
        "probe_failed",
    ]
    message: str
    reason: str | None = None
    from_node: str | None = None
    to_node: str | None = None
    extra: dict | None = None
    created_at: datetime | None = None

    def to_markdown(self) -> str:
        lines = [f"**{self.kind}**: {self.message}"]
        if self.reason:
            lines.append(f"reason: `{self.reason}`")
        if self.from_node:
            lines.append(f"from: `{self.from_node}`")
        if self.to_node:
            lines.append(f"to: `{self.to_node}`")
        if self.extra:
            for k, v in self.extra.items():
                lines.append(f"{k}: `{v}`")
        return "\n".join(lines)


class Notifier(ABC):
    name: str = "base"
    enabled: bool = True

    @abstractmethod
    async def send(self, event: NotificationEvent) -> None: ...

    async def aclose(self) -> None:
        return None
