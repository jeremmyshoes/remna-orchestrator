"""Runtime configuration loaded from environment variables / .env file."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Service ---
    service_name: str = "remna-orchestrator"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    api_token: SecretStr = Field(
        default=SecretStr("change-me"),
        description="Bearer token clients must send to the orchestrator API.",
    )

    # --- Database ---
    database_url: str = "sqlite+aiosqlite:///./data/remna-orchestrator.db"

    # --- Remnawave ---
    remnawave_base_url: str = Field(
        default="https://panel.example.com",
        description="Base URL of the Remnawave panel (without trailing slash).",
    )
    remnawave_api_token: SecretStr = Field(
        default=SecretStr(""),
        description="Remnawave API token (generated in panel settings).",
    )

    # --- Cloud provider ---
    cloud_provider: Literal["h2nexus", "hetzner", "none"] = "h2nexus"

    # h2.nexus (BillManager)
    h2nexus_base_url: str = "https://my.h2.nexus/billmgr"
    h2nexus_username: str = ""
    h2nexus_password: SecretStr = SecretStr("")
    h2nexus_verify_ssl: bool = True

    # Hetzner (optional, if you want actual dynamic spawn)
    hetzner_token: SecretStr = SecretStr("")
    hetzner_default_location: str = "fsn1"
    hetzner_default_image: str = "ubuntu-24.04"
    hetzner_default_server_type: str = "cx22"
    hetzner_ssh_key_name: str = ""

    # --- DNS ---
    dns_provider: Literal["cloudflare", "none"] = "cloudflare"
    cloudflare_api_token: SecretStr = SecretStr("")
    cloudflare_zone_id: str = ""
    cloudflare_subscription_host: str = "sub"  # e.g. sub.example.com
    cloudflare_root_domain: str = "example.com"

    # --- Rotation triggers ---
    rotation_schedule_enabled: bool = True
    rotation_schedule_cron: str = "0 */6 * * *"  # every 6 hours
    rotation_probe_enabled: bool = True
    rotation_probe_interval_seconds: int = 300  # 5 min
    rotation_manual_enabled: bool = True

    # --- Probe settings ---
    # Public endpoints we use to check whether a node IP is reachable
    # from Russian AS (network). 2ip.io / ping.pe / check-host.net style.
    probe_checkhost_nodes: list[str] = Field(
        default_factory=lambda: [
            "ru1.node.check-host.net",
            "ru2.node.check-host.net",
            "ru3.node.check-host.net",
            "ru4.node.check-host.net",
        ]
    )
    probe_failure_threshold: int = 3  # consecutive failures before rotation
    probe_success_threshold: int = 2  # consecutive successes to mark recovered
    probe_timeout_seconds: float = 10.0

    # --- Pool sizing ---
    node_pool_min_size: int = 2
    node_pool_max_size: int = 5
    # SSH connection defaults for node bootstrap / maintenance
    node_ssh_user: str = "root"
    node_ssh_port: int = 22


@lru_cache
def get_settings() -> Settings:
    return Settings()
