"""No-op cloud adapter for local development and tests.

Set ``CLOUD_PROVIDER=null`` (or ``none``) in .env to boot the app without a
real Hetzner/h2.nexus account. Real-resource actions raise informative errors.
"""

from __future__ import annotations

import logging

from app.cloud_adapters.base import CloudAdapter, CloudInstance

log = logging.getLogger(__name__)


class NullCloudAdapter(CloudAdapter):
    name = "null"

    async def create_instance(
        self,
        name: str,
        location: str | None,
        image: str | None,
        user_data: str | None,
    ) -> CloudInstance:
        raise RuntimeError(
            "NullCloudAdapter: cannot create instances. "
            "Set CLOUD_PROVIDER=hetzner|h2nexus in .env and provide credentials."
        )

    async def destroy_instance(self, cloud_id: str) -> None:
        log.info("null cloud: pretending to destroy %s", cloud_id)

    async def list_instances(self) -> list[CloudInstance]:
        return []

    async def get_instance(self, cloud_id: str) -> CloudInstance:
        raise RuntimeError(f"NullCloudAdapter: no instance {cloud_id}")

    async def rotate_ipv4(self, cloud_id: str) -> str:
        raise RuntimeError(
            "NullCloudAdapter: cannot rotate IPv4. Configure a real cloud provider."
        )
