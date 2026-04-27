"""Factory that returns the correct CloudAdapter based on settings."""

from __future__ import annotations

from app.cloud_adapters.base import CloudAdapter
from app.core.config import get_settings


def get_cloud_adapter() -> CloudAdapter:
    s = get_settings()
    if s.cloud_provider == "h2nexus":
        from app.cloud_adapters.h2nexus import H2NexusAdapter
        return H2NexusAdapter(
            base_url=s.h2nexus_base_url,
            username=s.h2nexus_username,
            password=s.h2nexus_password.get_secret_value(),
            verify_ssl=s.h2nexus_verify_ssl,
        )
    if s.cloud_provider == "hetzner":
        from app.cloud_adapters.hetzner import HetznerAdapter
        return HetznerAdapter(
            token=s.hetzner_token.get_secret_value(),
            default_location=s.hetzner_default_location,
            default_image=s.hetzner_default_image,
            default_server_type=s.hetzner_default_server_type,
            ssh_key_name=s.hetzner_ssh_key_name,
        )
    raise RuntimeError(f"Unsupported cloud_provider: {s.cloud_provider}")
