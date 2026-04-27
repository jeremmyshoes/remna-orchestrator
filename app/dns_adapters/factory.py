from __future__ import annotations

from app.core.config import get_settings
from app.dns_adapters.base import DNSAdapter


def get_dns_adapter() -> DNSAdapter:
    s = get_settings()
    if s.dns_provider in ("null", "none"):
        from app.dns_adapters.null import NullDNSAdapter
        return NullDNSAdapter()
    if s.dns_provider == "cloudflare":
        from app.dns_adapters.cloudflare import CloudflareAdapter
        return CloudflareAdapter(
            api_token=s.cloudflare_api_token.get_secret_value(),
            zone_id=s.cloudflare_zone_id,
            root_domain=s.cloudflare_root_domain,
        )
    raise RuntimeError(f"Unsupported dns_provider: {s.dns_provider}")
