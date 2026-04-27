"""Remnawave Panel REST API client.

Endpoints (per https://docs.rw/api and rust docs.rs/remnawave):

    POST   /api/nodes                       create node
    GET    /api/nodes                       list nodes
    GET    /api/nodes/{uuid}                get node
    PATCH  /api/nodes                       update node
    DELETE /api/nodes/{uuid}                delete node
    POST   /api/nodes/{uuid}/actions/enable
    POST   /api/nodes/{uuid}/actions/disable
    POST   /api/nodes/{uuid}/actions/restart
    POST   /api/nodes/actions/restart-all
    GET    /api/system/health               panel & node health

Auth: Bearer token in Authorization header (token created in panel settings).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


class RemnawaveError(RuntimeError):
    pass


class RemnawaveClient:
    def __init__(self, base_url: str, api_token: str, timeout: float = 30.0) -> None:
        if not api_token:
            raise RuntimeError("REMNAWAVE_API_TOKEN is empty")
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
                "User-Agent": "remna-orchestrator/0.1",
            },
            timeout=timeout,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=1, min=1, max=5),
            retry=retry_if_exception_type(
                (httpx.TransportError, httpx.RemoteProtocolError, httpx.ReadTimeout)
            ),
            reraise=True,
        ):
            with attempt:
                resp = await self._client.request(method, path, **kwargs)

        if resp.status_code >= 400:
            raise RemnawaveError(f"{method} {path} -> {resp.status_code}: {resp.text[:300]}")
        if not resp.content:
            return None
        data = resp.json()
        # Remnawave wraps responses in {"response": ...}
        return data.get("response", data) if isinstance(data, dict) else data

    # ----------------------------------------------------------------- nodes
    async def list_nodes(self) -> list[dict]:
        return await self._request("GET", "/api/nodes") or []

    async def get_node(self, uuid: str) -> dict:
        return await self._request("GET", f"/api/nodes/{uuid}")

    async def create_node(
        self,
        name: str,
        address: str,
        port: int,
        country_code: str = "DE",
        config_profile_uuid: str | None = None,
        consumption_multiplier: float = 1.0,
        active_inbounds: list[str] | None = None,
    ) -> dict:
        """Create a node in Remnawave and return its full record (including uuid)."""
        payload: dict[str, Any] = {
            "name": name,
            "address": address,
            "port": port,
            "countryCode": country_code,
            "consumptionMultiplier": consumption_multiplier,
            "isTrafficTrackingActive": False,
        }
        if config_profile_uuid:
            payload["configProfileUuid"] = config_profile_uuid
        if active_inbounds:
            payload["activeInbounds"] = active_inbounds
        node = await self._request("POST", "/api/nodes", json=payload)
        log.info("remnawave: created node name=%s uuid=%s", name, node.get("uuid"))
        return node

    async def delete_node(self, uuid: str) -> None:
        await self._request("DELETE", f"/api/nodes/{uuid}")
        log.info("remnawave: deleted node uuid=%s", uuid)

    async def enable_node(self, uuid: str) -> None:
        await self._request("POST", f"/api/nodes/{uuid}/actions/enable")

    async def disable_node(self, uuid: str) -> None:
        await self._request("POST", f"/api/nodes/{uuid}/actions/disable")

    async def restart_node(self, uuid: str) -> None:
        await self._request("POST", f"/api/nodes/{uuid}/actions/restart")

    async def restart_all_nodes(self) -> None:
        await self._request("POST", "/api/nodes/actions/restart-all", json={})

    # ---------------------------------------------------- config profiles
    async def list_config_profiles(self) -> list[dict]:
        return await self._request("GET", "/api/config-profiles") or []

    async def get_default_config_profile_uuid(self) -> str | None:
        profiles = await self.list_config_profiles()
        if not profiles:
            return None
        # Remnawave auto-creates one profile on fresh install
        return profiles[0].get("uuid")

    # --------------------------------------------------------------- health
    async def system_health(self) -> dict:
        return await self._request("GET", "/api/system/health") or {}

    async def aclose(self) -> None:
        await self._client.aclose()
