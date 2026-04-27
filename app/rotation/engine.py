"""Rotation engine — orchestrates node pool lifecycle.

Responsibilities:
  * ``ensure_pool()`` — keep at least ``NODE_POOL_MIN_SIZE`` healthy nodes,
    spread across ``HETZNER_ALLOWED_LOCATIONS``.
  * ``spawn_node()`` — provision + bootstrap + register + DNS publish.
  * ``rotate_node()`` — replace one node with graceful drain + audit + notify.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from collections import Counter
from datetime import UTC, datetime

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select

from app.cloud_adapters import get_cloud_adapter
from app.core.config import get_settings
from app.dns_adapters import get_dns_adapter
from app.models import Node, NodeStatus, RotationEvent, RotationReason
from app.models.base import session_scope
from app.notifications import build_notifier
from app.notifications.base import NotificationEvent
from app.remnawave import RemnawaveClient

log = logging.getLogger(__name__)


def _render_cloud_init(**kwargs: str) -> str:
    env = Environment(
        loader=FileSystemLoader("templates/cloud_init"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template("remnawave_node.yaml").render(**kwargs)


def _now() -> datetime:
    return datetime.now(UTC)


class RotationEngine:
    def __init__(self) -> None:
        self.s = get_settings()
        self.cloud = get_cloud_adapter()
        self.dns = get_dns_adapter()
        self.rw = RemnawaveClient(
            base_url=self.s.remnawave_base_url,
            api_token=self.s.remnawave_api_token.get_secret_value(),
        )
        self.notifier = build_notifier()

    async def aclose(self) -> None:
        await self.rw.aclose()
        await self.notifier.aclose()
        close = getattr(self.cloud, "aclose", None)
        if close:
            await close()

    # ------------------------------------------------------ region balancing
    async def _pick_region(self) -> str:
        """Return the least-populated Hetzner location from the allowed list."""
        allowed = self.s.hetzner_allowed_locations or [self.s.hetzner_default_location]
        async with session_scope() as s:
            rows = (
                await s.execute(
                    select(Node.location).where(
                        Node.status.in_([NodeStatus.ACTIVE, NodeStatus.STANDBY])
                    )
                )
            ).all()
        counts: Counter[str] = Counter(r[0] for r in rows)
        # Pick the first allowed location with minimum count
        best = min(allowed, key=lambda loc: counts.get(loc, 0))
        log.info("balancer: picked region=%s (current distribution=%s)", best, dict(counts))
        return best

    # ----------------------------------------------------------- provisioning
    async def spawn_node(self, label: str | None = None, region: str | None = None) -> Node:
        label = label or f"rw-{_now():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        region = region or await self._pick_region()
        port = 2222
        panel_host = self.s.remnawave_base_url.replace("https://", "").replace("http://", "")
        panel_host = panel_host.split("/")[0]

        user_data = _render_cloud_init(
            remnawave_panel_ip=panel_host,
            node_port=str(port),
            ssl_cert_pem="",
            ssh_authorized_key="",
            node_hostname=label,
        )

        log.info("engine: spawning cloud instance %s in %s", label, region)
        instance = await self.cloud.create_instance(
            name=label,
            location=region,
            image=None,
            user_data=user_data,
        )

        # Wait for bootstrap to finish before cutting traffic over.
        log.info("engine: waiting for cloud-init on %s (ip=%s)...", label, instance.ipv4)
        await asyncio.sleep(self.s.node_bootstrap_wait_seconds)

        # Publish DNS: node.<root_domain>
        fqdn = f"{label}.{self.s.cloudflare_root_domain}"
        await self.dns.upsert_a_record(label, instance.ipv4, proxied=False)

        # --- User migration (iter 4): inherit active inbounds from the most
        # recent active node so existing subscriptions keep routing. ---
        active_inbounds, profile_uuid = await self._inherit_active_inbounds()

        rw_node = await self.rw.create_node(
            name=label,
            address=instance.ipv4,
            port=port,
            country_code=instance.location[:2].upper() or "DE",
            config_profile_uuid=profile_uuid,
            active_inbounds=active_inbounds,
        )

        async with session_scope() as s:
            node = Node(
                name=label,
                cloud_id=instance.cloud_id,
                cloud_provider=self.cloud.name,
                location=region,
                ipv4=instance.ipv4,
                panel_port=port,
                fqdn=fqdn,
                remnawave_uuid=rw_node.get("uuid"),
                status=NodeStatus.ACTIVE,
            )
            s.add(node)
            await s.flush()
            node_id = node.id

        await self.notifier.send(NotificationEvent(
            kind="node_spawned",
            message=f"Spawned node {label}",
            to_node=label,
            extra={"ip": instance.ipv4, "region": region},
        ))

        log.info("engine: spawned node id=%s label=%s ip=%s", node_id, label, instance.ipv4)
        return await self._get_node(node_id)

    async def _inherit_active_inbounds(self) -> tuple[list[str] | None, str | None]:
        """Return (active_inbound_uuids, config_profile_uuid) from an existing
        active node. If no active nodes exist yet, fall back to the first
        config profile in the panel."""
        async with session_scope() as s:
            peers = (
                await s.execute(
                    select(Node).where(
                        Node.status == NodeStatus.ACTIVE,
                        Node.remnawave_uuid.is_not(None),
                    ).limit(1)
                )
            ).scalars().all()

        if peers:
            try:
                peer = await self.rw.get_node(peers[0].remnawave_uuid)
                inbounds = [i.get("uuid") for i in (peer.get("activeInbounds") or [])]
                inbounds = [i for i in inbounds if i]
                profile = (peer.get("configProfile") or {}).get("uuid") or peer.get(
                    "configProfileUuid"
                )
                if inbounds or profile:
                    log.info(
                        "engine: inheriting %d inbounds from peer %s profile=%s",
                        len(inbounds), peers[0].name, profile,
                    )
                    return inbounds or None, profile
            except Exception:
                log.exception("engine: failed to inherit inbounds from peer, using defaults")

        return None, await self.rw.get_default_config_profile_uuid()

    async def _get_node(self, node_id: int) -> Node:
        async with session_scope() as s:
            return (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()

    # ------------------------------------------------------------- pool mgmt
    async def ensure_pool(self) -> None:
        async with session_scope() as s:
            nodes = (
                await s.execute(
                    select(Node).where(
                        Node.status.in_(
                            [NodeStatus.ACTIVE, NodeStatus.STANDBY, NodeStatus.PROVISIONING]
                        )
                    )
                )
            ).scalars().all()
            current = len(nodes)
        if current >= self.s.node_pool_min_size:
            return
        missing = self.s.node_pool_min_size - current
        log.info(
            "engine: pool under min (%d/%d), spawning %d nodes",
            current, self.s.node_pool_min_size, missing,
        )
        for _ in range(missing):
            try:
                await self.spawn_node()
            except Exception:
                log.exception("engine: spawn failed during ensure_pool")

    # --------------------------------------------------------------- rotate
    async def rotate_node(self, node_id: int, reason: RotationReason) -> Node:
        """Replace node_id with a freshly provisioned one.

        Flow (with graceful drain):
          1. Notify "rotation_started"
          2. Spawn new node (already waits for bootstrap)
          3. Disable the OLD node in Remnawave → clients reconnect to siblings
          4. Sleep ``NODE_DRAIN_SECONDS``
          5. Delete OLD node from panel, DNS, cloud
          6. Notify "rotation_success"
        """
        async with session_scope() as s:
            old = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()
            old_name = old.name
            old_remna_uuid = old.remnawave_uuid
            old_cloud_id = old.cloud_id
            old_fqdn = old.fqdn
            old_ipv4 = old.ipv4

        await self.notifier.send(NotificationEvent(
            kind="rotation_started",
            message=f"Rotating node {old_name} (ip={old_ipv4})",
            reason=reason.value,
            from_node=old_name,
        ))
        log.info("engine: rotating node %s reason=%s", old_name, reason.value)

        # 1. spawn replacement first (picks a region != old)
        new: Node
        try:
            new = await self.spawn_node()
        except Exception as exc:
            async with session_scope() as s:
                s.add(RotationEvent(
                    reason=reason, from_node=old_name, to_node=None,
                    success=False, details=f"spawn failed: {exc!r}",
                ))
            await self.notifier.send(NotificationEvent(
                kind="rotation_failed",
                message=f"spawn failed: {exc!r}",
                reason=reason.value,
                from_node=old_name,
            ))
            raise

        # 2. disable old node in panel → Remnawave stops routing new clients there
        if old_remna_uuid:
            try:
                await self.rw.disable_node(old_remna_uuid)
                log.info("engine: disabled old node in panel, draining for %ds",
                         self.s.node_drain_seconds)
            except Exception:
                log.exception("engine: failed to disable old node (continuing)")

        # 3. drain window — existing connections are allowed to finish
        await asyncio.sleep(self.s.node_drain_seconds)

        # 4. delete from panel
        if old_remna_uuid:
            try:
                await self.rw.delete_node(old_remna_uuid)
            except Exception:
                log.exception("engine: failed to delete old node from panel")

        # 5. delete DNS
        if old_fqdn:
            short = old_fqdn.replace(f".{self.s.cloudflare_root_domain}", "")
            try:
                await self.dns.delete_a_record(short)
            except Exception:
                log.exception("engine: failed to delete DNS record for %s", old_name)

        # 6. destroy VPS
        if old_cloud_id:
            try:
                await self.cloud.destroy_instance(old_cloud_id)
            except Exception:
                log.exception("engine: failed to destroy cloud instance %s", old_cloud_id)

        # 7. mark DB + audit
        async with session_scope() as s:
            old_row = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()
            old_row.status = NodeStatus.DESTROYED
            s.add(RotationEvent(
                reason=reason, from_node=old_name, to_node=new.name,
                success=True, details=None,
            ))

        await self.notifier.send(NotificationEvent(
            kind="rotation_success",
            message=f"Replaced {old_name} (ip={old_ipv4}) with {new.name} (ip={new.ipv4})",
            reason=reason.value,
            from_node=old_name,
            to_node=new.name,
            extra={"new_ip": new.ipv4, "new_region": new.location},
        ))
        return new
