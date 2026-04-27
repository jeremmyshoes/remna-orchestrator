"""Node model — represents a Remnawave proxy node (VPS running Xray)."""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class NodeStatus(enum.StrEnum):
    PROVISIONING = "provisioning"   # VPS being created / node being installed
    ACTIVE = "active"                # node is serving traffic
    STANDBY = "standby"              # healthy, in pool, but not current active
    BLOCKED = "blocked"              # IP detected as blacklisted by probe
    DISABLED = "disabled"            # operator-disabled
    DESTROYED = "destroyed"          # decommissioned


class Node(Base):
    __tablename__ = "nodes"
    __table_args__ = (UniqueConstraint("remnawave_uuid", name="uq_nodes_remna_uuid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Label used in UI/logs
    name: Mapped[str] = mapped_column(String(128), unique=True)
    # Cloud-side identifier (h2.nexus service id OR hetzner server id)
    cloud_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    cloud_provider: Mapped[str] = mapped_column(String(32))
    # Where the VPS is (country code / DC code)
    location: Mapped[str] = mapped_column(String(32), default="unknown")
    # Current public IPv4 used by the node (also what we push to Cloudflare)
    ipv4: Mapped[str] = mapped_column(String(64))
    # Internal port used by Remnawave Panel <-> Node (XRAY API/stats)
    panel_port: Mapped[int] = mapped_column(Integer, default=2222)
    # FQDN managed in Cloudflare for this node (e.g. node-3.example.com)
    fqdn: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # UUID assigned by Remnawave panel once the node is registered
    remnawave_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)

    status: Mapped[NodeStatus] = mapped_column(
        Enum(NodeStatus, name="node_status"), default=NodeStatus.PROVISIONING
    )

    # Probe tracking
    consecutive_probe_failures: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_probe_successes: Mapped[int] = mapped_column(Integer, default=0)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.utcnow()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.utcnow(),
        onupdate=lambda: datetime.utcnow(),
    )

    def __repr__(self) -> str:
        return f"<Node {self.name} ip={self.ipv4} status={self.status.value}>"
