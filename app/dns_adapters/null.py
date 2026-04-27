"""No-op DNS adapter for local development and tests."""

from __future__ import annotations

import logging

from app.dns_adapters.base import DNSAdapter

log = logging.getLogger(__name__)


class NullDNSAdapter(DNSAdapter):
    name = "null"

    async def upsert_a_record(self, hostname: str, ipv4: str, proxied: bool = False) -> str:
        log.info("null dns: pretending to upsert A %s -> %s", hostname, ipv4)
        return "null-record-id"

    async def delete_a_record(self, hostname: str) -> None:
        log.info("null dns: pretending to delete A %s", hostname)

    async def list_a_records(self) -> list[dict]:
        return []
