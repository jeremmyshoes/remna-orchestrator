"""Abstract DNS adapter."""

from __future__ import annotations

from abc import ABC, abstractmethod


class DNSAdapter(ABC):
    name: str = "base"

    @abstractmethod
    async def upsert_a_record(self, hostname: str, ipv4: str, proxied: bool = False) -> str:
        """Create or update an A record. Returns the record id."""

    @abstractmethod
    async def delete_a_record(self, hostname: str) -> None: ...

    @abstractmethod
    async def list_a_records(self) -> list[dict]: ...
