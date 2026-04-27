"""Cloudflare DNS adapter using the official `cloudflare` Python SDK (v4)."""

from __future__ import annotations

import asyncio
import logging

from cloudflare import Cloudflare

from app.dns_adapters.base import DNSAdapter

log = logging.getLogger(__name__)


class CloudflareAdapter(DNSAdapter):
    name = "cloudflare"

    def __init__(self, api_token: str, zone_id: str, root_domain: str) -> None:
        if not api_token:
            raise RuntimeError("CLOUDFLARE_API_TOKEN is empty")
        if not zone_id:
            raise RuntimeError("CLOUDFLARE_ZONE_ID is empty")
        self._cf = Cloudflare(api_token=api_token)
        self.zone_id = zone_id
        self.root_domain = root_domain

    def _fqdn(self, hostname: str) -> str:
        if hostname.endswith(self.root_domain):
            return hostname
        return f"{hostname}.{self.root_domain}"

    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    async def _find_record(self, fqdn: str) -> dict | None:
        page = await self._run(
            self._cf.dns.records.list,
            zone_id=self.zone_id,
            name=fqdn,
            type="A",
        )
        records = list(page.result or [])
        return records[0].model_dump() if records else None

    async def upsert_a_record(self, hostname: str, ipv4: str, proxied: bool = False) -> str:
        fqdn = self._fqdn(hostname)
        existing = await self._find_record(fqdn)
        if existing:
            rec = await self._run(
                self._cf.dns.records.update,
                dns_record_id=existing["id"],
                zone_id=self.zone_id,
                type="A",
                name=fqdn,
                content=ipv4,
                ttl=60,
                proxied=proxied,
            )
            log.info("cloudflare: updated A %s -> %s (proxied=%s)", fqdn, ipv4, proxied)
            return rec.id
        rec = await self._run(
            self._cf.dns.records.create,
            zone_id=self.zone_id,
            type="A",
            name=fqdn,
            content=ipv4,
            ttl=60,
            proxied=proxied,
        )
        log.info("cloudflare: created A %s -> %s (proxied=%s)", fqdn, ipv4, proxied)
        return rec.id

    async def delete_a_record(self, hostname: str) -> None:
        fqdn = self._fqdn(hostname)
        existing = await self._find_record(fqdn)
        if not existing:
            return
        await self._run(
            self._cf.dns.records.delete,
            dns_record_id=existing["id"],
            zone_id=self.zone_id,
        )
        log.info("cloudflare: deleted A %s", fqdn)

    async def list_a_records(self) -> list[dict]:
        page = await self._run(
            self._cf.dns.records.list,
            zone_id=self.zone_id,
            type="A",
        )
        return [r.model_dump() for r in (page.result or [])]
