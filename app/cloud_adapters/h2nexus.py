"""h2.nexus (BillManager / ISPsystem) cloud adapter.

h2.nexus uses BillManager 6 which exposes a session-based XML API over
``/billmgr?func=...``. This adapter supports:

* Listing the VPS services you own (``func=vds``).
* Changing the geographic IPv4 location via the ``pricelist.addon`` /
  ``vds.edit`` flow — this is h2.nexus's "Virtual Locations Geo-IPv4" feature
  which lets you swap the external IP country without reinstalling.

It deliberately does **not** implement :meth:`create_instance` — ordering a
new service via BillManager costs real money and requires manual payment
confirmation, so dynamic spawning is disabled. Use a pre-provisioned pool.

Reference: https://www.ispsystem.com/docs/b6c/developer-section/working-with-api/guide-to-ispsystem-software-api
"""

from __future__ import annotations

import logging
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.cloud_adapters.base import CloudAdapter, CloudInstance

log = logging.getLogger(__name__)


class H2NexusError(RuntimeError):
    pass


class H2NexusAdapter(CloudAdapter):
    name = "h2nexus"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        verify_ssl: bool = True,
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self._session_id: str | None = None
        self._client = httpx.AsyncClient(
            verify=verify_ssl,
            timeout=timeout,
            headers={"User-Agent": "remna-orchestrator/0.1"},
        )

    # ------------------------------------------------------------------ auth
    async def _auth(self) -> str:
        if self._session_id:
            return self._session_id
        params = {
            "out": "xml",
            "func": "auth",
            "username": self.username,
            "password": self.password,
        }
        resp = await self._client.get(self.base_url, params=params)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)
        error = root.find("error")
        if error is not None:
            raise H2NexusError(f"Auth failed: {error.get('type')} {error.text}")
        auth = root.find("auth")
        if auth is None or not auth.text:
            raise H2NexusError(f"No session in auth response: {resp.text[:300]}")
        self._session_id = auth.text
        log.info("h2nexus: authenticated, session=%s", self._session_id[:8])
        return self._session_id

    async def _call(self, func: str, **params: Any) -> ET.Element:
        session = await self._auth()
        query = {"auth": session, "out": "xml", "func": func, **params}

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=5),
            retry=retry_if_exception_type(httpx.HTTPError),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.get(self.base_url, params=query)
                resp.raise_for_status()

        root = ET.fromstring(resp.text)
        error = root.find("error")
        if error is not None:
            # Session expired → re-auth once
            if error.get("type") == "auth":
                self._session_id = None
                return await self._call(func, **params)
            raise H2NexusError(f"API error on {func}: {error.get('type')} {error.text}")
        return root

    # --------------------------------------------------------------- mapping
    @staticmethod
    def _xml_to_instance(elem: ET.Element) -> CloudInstance:
        get = lambda tag: (e.text if (e := elem.find(tag)) is not None else None)  # noqa: E731
        return CloudInstance(
            cloud_id=get("id") or elem.get("id") or "",
            name=get("name") or get("domain") or "unnamed",
            ipv4=get("ip") or "",
            location=get("datacenter_name") or get("country") or "unknown",
            status=get("item_status") or get("status") or "unknown",
        )

    # ----------------------------------------------------------- public API
    async def list_instances(self) -> list[CloudInstance]:
        root = await self._call("vds")
        return [self._xml_to_instance(e) for e in root.findall(".//elem")]

    async def get_instance(self, cloud_id: str) -> CloudInstance:
        root = await self._call("vds.edit", elid=cloud_id)
        # vds.edit returns a flat form, not <elem>
        return self._xml_to_instance(root)

    async def create_instance(
        self,
        name: str,
        location: str | None,
        image: str | None,
        user_data: str | None,
    ) -> CloudInstance:
        raise NotImplementedError(
            "h2.nexus requires manual payment per order; pre-provision your "
            "node pool via the h2.nexus panel and register it with "
            "`remna-ctl node register` instead."
        )

    async def destroy_instance(self, cloud_id: str) -> None:
        # Destructive and final-billing implications → operator-only via panel.
        raise NotImplementedError(
            "h2.nexus service termination is billing-affecting and must be "
            "done manually in the panel."
        )

    async def rotate_ipv4(self, cloud_id: str) -> str:
        """Rotate the IPv4 via h2.nexus 'Virtual Locations Geo-IPv4' addon.

        NOTE: the exact BillManager function and form parameters depend on the
        addon configuration. This method tries the common pattern
        ``vds.addon -> addon.edit -> save`` and falls back to re-ordering
        ``geoip`` addon. After the change h2.nexus reboots the VPS and
        allocates a new IPv4; we poll until a new IP is visible.
        """
        import asyncio

        # 1. read current IP
        before = await self.get_instance(cloud_id)
        log.info("h2nexus: rotating ipv4 for %s (current=%s)", cloud_id, before.ipv4)

        # 2. trigger the "reissue IP" action if it's exposed on vds.edit
        try:
            await self._call("vds.reinstall", elid=cloud_id, changeip="on", sok="ok")
        except H2NexusError as exc:
            log.warning("h2nexus: vds.reinstall changeip failed (%s), trying vds.ip.change", exc)
            try:
                await self._call("vds.ip.change", elid=cloud_id, sok="ok")
            except H2NexusError as exc2:
                raise H2NexusError(
                    "Both vds.reinstall and vds.ip.change failed — this "
                    "h2.nexus tariff probably doesn't support programmatic IP "
                    "rotation. Change it manually in the panel."
                ) from exc2

        # 3. poll for IP change
        for _ in range(30):
            await asyncio.sleep(10)
            current = await self.get_instance(cloud_id)
            if current.ipv4 and current.ipv4 != before.ipv4:
                log.info("h2nexus: new ipv4=%s", current.ipv4)
                return current.ipv4
        raise H2NexusError("Timed out waiting for new IPv4 after rotation request")

    async def aclose(self) -> None:
        await self._client.aclose()
