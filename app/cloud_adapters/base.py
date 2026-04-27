"""Abstract cloud adapter. Concrete implementations: h2nexus, hetzner."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(slots=True)
class CloudInstance:
    """Normalized representation of a VPS managed by this orchestrator."""

    cloud_id: str            # provider-side identifier
    name: str                # human label
    ipv4: str                # public IPv4
    location: str            # DC / region / country code
    status: str              # provider-reported status


class CloudAdapter(ABC):
    """Interface every cloud/hoster implementation must satisfy.

    The MVP does **not** require dynamic spawn; adapters that can't spawn VMs
    (classic shared hosting, BillManager-based providers) should raise
    ``NotImplementedError`` from :meth:`create_instance` and only implement the
    pool-management + IP-rotation methods.
    """

    name: str = "base"

    # --- Lifecycle (optional for providers that pre-provision) ---
    @abstractmethod
    async def create_instance(
        self,
        name: str,
        location: str | None,
        image: str | None,
        user_data: str | None,
    ) -> CloudInstance:
        """Provision a new VPS. Raise NotImplementedError for providers that
        don't support programmatic creation (e.g. BillManager-based)."""

    @abstractmethod
    async def destroy_instance(self, cloud_id: str) -> None: ...

    # --- Required for any adapter ---
    @abstractmethod
    async def list_instances(self) -> list[CloudInstance]: ...

    @abstractmethod
    async def get_instance(self, cloud_id: str) -> CloudInstance: ...

    @abstractmethod
    async def rotate_ipv4(self, cloud_id: str) -> str:
        """Change the external IPv4 of the instance (or return the same one if
        the provider doesn't support rotation). Must return the new IPv4."""
