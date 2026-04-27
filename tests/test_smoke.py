"""Smoke tests: make sure the app starts and core modules import."""

from __future__ import annotations

import os

import pytest


def test_app_imports_and_creates() -> None:
    # Set required envs so Settings can load without blowing up
    os.environ.setdefault("CLOUD_PROVIDER", "none")
    os.environ.setdefault("DNS_PROVIDER", "none")
    os.environ.setdefault("API_TOKEN", "test-token")
    os.environ.setdefault("REMNAWAVE_API_TOKEN", "test")

    from app.api.app import create_app

    app = create_app()
    # At least / /health /docs /api/nodes /api/rotation/*
    assert len(app.routes) >= 5


def test_cloudinit_template_renders() -> None:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined

    env = Environment(loader=FileSystemLoader("templates/cloud_init"), undefined=StrictUndefined)
    rendered = env.get_template("remnawave_node.yaml").render(
        remnawave_panel_ip="1.2.3.4",
        node_port="2222",
        ssl_cert_pem="",
        ssh_authorized_key="",
        node_hostname="rw-test",
    )
    assert "#cloud-config" in rendered
    assert "docker compose up -d" in rendered
    assert "1.2.3.4" in rendered


def test_settings_load() -> None:
    os.environ["API_TOKEN"] = "x"
    os.environ["CLOUD_PROVIDER"] = "hetzner"
    os.environ["DNS_PROVIDER"] = "cloudflare"

    # fresh import to pick up env
    from app.core.config import Settings

    s = Settings()
    assert s.cloud_provider == "hetzner"
    assert s.dns_provider == "cloudflare"
    assert s.node_pool_min_size >= 1


def test_node_model_enum() -> None:
    from app.models import NodeStatus

    assert NodeStatus.ACTIVE == "active"
    assert NodeStatus("standby") is NodeStatus.STANDBY


@pytest.mark.parametrize("reason", ["scheduled", "probe_failed", "manual"])
def test_rotation_reason_values(reason: str) -> None:
    from app.models import RotationReason

    assert RotationReason(reason).value == reason
