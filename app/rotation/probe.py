"""RKN / blacklist reachability probe.

Uses the public check-host.net HTTP API to verify a node IP is reachable from
Russian network vantage points. If it fails from >= N out of M Russian nodes,
we mark the node as BLOCKED and trigger rotation.

check-host.net API:
    https://check-host.net/check-tcp?host=IP:PORT&node=ru1.node.check-host.net&max_nodes=4
    returns { "request_id": "..." }
    then poll /check-result/<id> for verdicts.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

log = logging.getLogger(__name__)

CHECK_HOST_API = "https://check-host.net"


class RknProbe:
    def __init__(
        self,
        nodes: list[str],
        timeout: float = 10.0,
        verdict_poll_seconds: float = 5.0,
        verdict_poll_attempts: int = 8,
    ) -> None:
        self.nodes = nodes
        self.timeout = timeout
        self.verdict_poll_seconds = verdict_poll_seconds
        self.verdict_poll_attempts = verdict_poll_attempts

    async def is_reachable(self, ipv4: str, port: int = 443) -> bool:
        """Return True if the IP:port is reachable from at least one RU node."""
        async with httpx.AsyncClient(
            base_url=CHECK_HOST_API,
            headers={"Accept": "application/json", "User-Agent": "remna-orchestrator/0.1"},
            timeout=self.timeout,
        ) as client:
            params = [("host", f"{ipv4}:{port}"), ("max_nodes", str(len(self.nodes)))]
            for n in self.nodes:
                params.append(("node", n))
            try:
                resp = await client.get("/check-tcp", params=params)
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                log.warning("probe: check-host init failed: %s", exc)
                return True  # fail open — don't rotate on network glitch

            data = resp.json()
            request_id = data.get("request_id")
            if not request_id:
                log.warning("probe: no request_id in response: %s", data)
                return True

            for _ in range(self.verdict_poll_attempts):
                await asyncio.sleep(self.verdict_poll_seconds)
                try:
                    verdict_resp = await client.get(f"/check-result/{request_id}")
                    verdict_resp.raise_for_status()
                except httpx.HTTPError:
                    continue
                results: dict = verdict_resp.json() or {}
                # Each value is either null (pending) or [[ok_ms_or_false, extra]]
                if any(v is None for v in results.values()):
                    continue
                reachable_count = 0
                for _node, res in results.items():
                    if not res:
                        continue
                    try:
                        first = res[0]
                        if first and isinstance(first, list) and first[0] not in (None, False, 0):
                            reachable_count += 1
                    except (IndexError, TypeError):
                        continue
                log.info(
                    "probe: ip=%s port=%s reachable_from=%d/%d RU nodes",
                    ipv4, port, reachable_count, len(results),
                )
                return reachable_count > 0

            log.warning("probe: timed out waiting for check-host verdict ip=%s", ipv4)
            return True  # fail open
