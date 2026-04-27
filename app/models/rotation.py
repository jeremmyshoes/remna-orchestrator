"""RotationEvent model — audit trail of every rotation decision."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class RotationReason(enum.StrEnum):
    SCHEDULED = "scheduled"
    PROBE_FAILED = "probe_failed"
    MANUAL = "manual"
    HEALTH_CHECK = "health_check"


class RotationEvent(Base):
    __tablename__ = "rotation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reason: Mapped[RotationReason] = mapped_column(Enum(RotationReason, name="rotation_reason"))
    from_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    to_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    success: Mapped[bool] = mapped_column(default=False)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
