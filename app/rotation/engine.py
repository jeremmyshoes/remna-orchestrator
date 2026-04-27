"""Rotation engine — orchestrates node pool lifecycle.

Responsibilities:
  * `ensure_pool()` — keep at least N active/standby nodes in the pool.
  * `rotate_node(node_id, reason)` — replace one node: spawn new VPS, register
    it with Remnawave, update Cloudflare DNS, decomission the old one.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from datetime import datetime

from jinja2 import Environment, FileSystemLoader, StrictUndefined
from sqlalchemy import select

from app.cloud_adapters import get_cloud_adapter
from app.core.config import get_settings
from app.dns_adapters import get_dns_adapter
from app.models import Node, NodeStatus, RotationEvent, RotationReason
from app.models.base import session_scope
from app.remnawave import RemnawaveClient

log = logging.getLogger(__name__)


def _render_cloud_init(**kwargs: str) -> str:
    env = Environment(
        loader=FileSystemLoader("templates/cloud_init"),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template("remnawave_node.yaml").render(**kwargs)


class RotationEngine:
    def __init__(self) -> None:
        self.s = get_settings()
        self.cloud = get_cloud_adapter()
        self.dns = get_dns_adapter()
        self.rw = RemnawaveClient(
            base_url=self.s.remnawave_base_url,
            api_token=self.s.remnawave_api_token.get_secret_value(),
        )

    async def aclose(self) -> None:
        await self.rw.aclose()
        close = getattr(self.cloud, "aclose", None)
        if close:
            await close()

    # ----------------------------------------------------------- provisioning
    async def spawn_node(self, label: str | None = None) -> Node:
        """Provision a new VPS, install Remnawave Node, register it with the
        panel, publish a Cloudflare A record, and persist state."""
        label = label or f"rw-{datetime.utcnow():%Y%m%d-%H%M%S}-{secrets.token_hex(3)}"
        port = 2222  # default internal port for Remnawave Node
        panel_host = self.s.remnawave_base_url.replace("https://", "").replace("http://", "")
        panel_host = panel_host.split("/")[0]

        user_data = _render_cloud_init(
            remnawave_panel_ip=panel_host,
            node_port=str(port),
            ssl_cert_pem="",  # Remnawave 2.x registers via panel-initiated token, no pre-shared cert
            ssh_authorized_key="",
            node_hostname=label,
        )

        log.info("engine: spawning cloud instance %s", label)
        instance = await self.cloud.create_instance(
            name=label,
            location=None,
            image=None,
            user_data=user_data,
        )

        # Wait for bootstrap to finish (cloud-init takes ~60-120s)
        log.info("engine: waiting for cloud-init on %s (ip=%s)...", label, instance.ipv4)
        await asyncio.sleep(120)

        # Publish DNS: node.<root_domain>
        fqdn = f"{label}.{self.s.cloudflare_root_domain}"
        await self.dns.upsert_a_record(label, instance.ipv4, proxied=False)

        # Register with Remnawave
        profile_uuid = await self.rw.get_default_config_profile_uuid()
        rw_node = await self.rw.create_node(
            name=label,
            address=instance.ipv4,
            port=port,
            country_code=instance.location[:2].upper() or "DE",
            config_profile_uuid=profile_uuid,
        )

        async with session_scope() as s:
            node = Node(
                name=label,
                cloud_id=instance.cloud_id,
                cloud_provider=self.cloud.name,
                location=instance.location,
                ipv4=instance.ipv4,
                panel_port=port,
                fqdn=fqdn,
                remnawave_uuid=rw_node.get("uuid"),
                status=NodeStatus.ACTIVE,
            )
            s.add(node)
            await s.flush()
            node_id = node.id
        log.info("engine: spawned node id=%s label=%s ip=%s", node_id, label, instance.ipv4)
        return await self._get_node(node_id)

    async def _get_node(self, node_id: int) -> Node:
        async with session_scope() as s:
            return (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()

    # ------------------------------------------------------------- pool mgmt
    async def ensure_pool(self) -> None:
        async with session_scope() as s:
            nodes = (
                await s.execute(
                    select(Node).where(
                        Node.status.in_([NodeStatus.ACTIVE, NodeStatus.STANDBY, NodeStatus.PROVISIONING])
                    )
                )
            ).scalars().all()
            current = len(nodes)
        if current >= self.s.node_pool_min_size:
            return
        missing = self.s.node_pool_min_size - current
        log.info("engine: pool under min (%d/%d), spawning %d nodes",
                 current, self.s.node_pool_min_size, missing)
        for _ in range(missing):
            try:
                await self.spawn_node()
            except Exception:
                log.exception("engine: spawn failed during ensure_pool")

    # --------------------------------------------------------------- rotate
    async def rotate_node(self, node_id: int, reason: RotationReason) -> Node:
        """Replace node_id with a freshly provisioned one. Returns the new Node."""
        async with session_scope() as s:
            old = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()
            old_name = old.name
            old_remna_uuid = old.remnawave_uuid
            old_cloud_id = old.cloud_id
            old_fqdn = old.fqdn

        log.info("engine: rotating node %s reason=%s", old_name, reason.value)

        # 1. spawn replacement first
        try:
            new = await self.spawn_node()
        except Exception as exc:
            async with session_scope() as s:
                s.add(RotationEvent(
                    reason=reason, from_node=old_name, to_node=None,
                    success=False, details=f"spawn failed: {exc!r}",
                ))
            raise

        # 2. disable and remove the old one in Remnawave
        try:
            if old_remna_uuid:
                await self.rw.disable_node(old_remna_uuid)
                await self.rw.delete_node(old_remna_uuid)
        except Exception:
            log.exception("engine: failed to remove old node %s from panel", old_name)

        # 3. delete old Cloudflare record
        try:
            if old_fqdn:
                # old_fqdn already includes root_domain
                short = old_fqdn.replace(f".{self.s.cloudflare_root_domain}", "")
                await self.dns.delete_a_record(short)
        except Exception:
            log.exception("engine: failed to delete old DNS record for %s", old_name)

        # 4. destroy old VPS
        try:
            if old_cloud_id:
                await self.cloud.destroy_instance(old_cloud_id)
        except Exception:
            log.exception("engine: failed to destroy cloud instance %s", old_cloud_id)

        # 5. mark old in DB
        async with session_scope() as s:
            old = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()
            old.status = NodeStatus.DESTROYED
            s.add(RotationEvent(
                reason=reason, from_node=old_name, to_node=new.name,
                success=True, details=None,
            ))
        return new
