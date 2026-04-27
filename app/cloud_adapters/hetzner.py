"""Hetzner Cloud adapter — optional 'real' cloud for true dynamic spawn.

Uses the official hcloud-python SDK. Runs sync hcloud calls in a thread pool
so they don't block the asyncio event loop.
"""

from __future__ import annotations

import asyncio
import logging

from hcloud import Client as HCloudClient
from hcloud.images import Image
from hcloud.locations import Location
from hcloud.server_types import ServerType
from hcloud.ssh_keys import SSHKey

from app.cloud_adapters.base import CloudAdapter, CloudInstance

log = logging.getLogger(__name__)


class HetznerAdapter(CloudAdapter):
    name = "hetzner"

    def __init__(
        self,
        token: str,
        default_location: str = "fsn1",
        default_image: str = "ubuntu-24.04",
        default_server_type: str = "cx22",
        ssh_key_name: str = "",
    ) -> None:
        if not token:
            raise RuntimeError("HETZNER_TOKEN is empty")
        self._client = HCloudClient(token=token, application_name="remna-orchestrator")
        self.default_location = default_location
        self.default_image = default_image
        self.default_server_type = default_server_type
        self.ssh_key_name = ssh_key_name

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    @staticmethod
    def _server_to_instance(server) -> CloudInstance:
        ipv4 = server.public_net.ipv4.ip if server.public_net and server.public_net.ipv4 else ""
        return CloudInstance(
            cloud_id=str(server.id),
            name=server.name,
            ipv4=ipv4,
            location=server.datacenter.location.name if server.datacenter else "unknown",
            status=server.status or "unknown",
        )

    async def create_instance(
        self,
        name: str,
        location: str | None,
        image: str | None,
        user_data: str | None,
    ) -> CloudInstance:
        ssh_keys = []
        if self.ssh_key_name:
            ssh_keys.append(SSHKey(name=self.ssh_key_name))

        resp = await self._run(
            self._client.servers.create,
            name=name,
            server_type=ServerType(name=self.default_server_type),
            image=Image(name=image or self.default_image),
            location=Location(name=location or self.default_location),
            ssh_keys=ssh_keys or None,
            user_data=user_data,
        )
        server = resp.server
        log.info("hetzner: created server id=%s name=%s ip=%s", server.id, server.name,
                 server.public_net.ipv4.ip if server.public_net else None)
        return self._server_to_instance(server)

    async def destroy_instance(self, cloud_id: str) -> None:
        server = await self._run(self._client.servers.get_by_id, int(cloud_id))
        await self._run(self._client.servers.delete, server)
        log.info("hetzner: deleted server id=%s", cloud_id)

    async def list_instances(self) -> list[CloudInstance]:
        servers = await self._run(self._client.servers.get_all)
        return [self._server_to_instance(s) for s in servers]

    async def get_instance(self, cloud_id: str) -> CloudInstance:
        server = await self._run(self._client.servers.get_by_id, int(cloud_id))
        return self._server_to_instance(server)

    async def rotate_ipv4(self, cloud_id: str) -> str:
        """On Hetzner the only way to change an IPv4 is to delete the primary
        IP and assign a new one (or recreate the server). We re-create the
        primary IPv4 on the existing server."""
        server = await self._run(self._client.servers.get_by_id, int(cloud_id))
        old_ip = server.public_net.ipv4.ip if server.public_net else None

        # Remove old primary IPv4
        if server.public_net and server.public_net.ipv4:
            primary = server.public_net.ipv4
            await self._run(self._client.primary_ips.delete, primary)

        # Create a new one and assign it
        new_primary = await self._run(
            self._client.primary_ips.create,
            type="ipv4",
            name=f"{server.name}-primary",
            assignee_type="server",
            assignee_id=server.id,
            auto_delete=True,
            datacenter=server.datacenter.name,
        )
        log.info("hetzner: rotated ipv4 on server=%s old=%s new=%s",
                 cloud_id, old_ip, new_primary.primary_ip.ip)
        return new_primary.primary_ip.ip
