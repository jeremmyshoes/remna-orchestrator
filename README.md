# remna-orchestrator

Auto-deploy and IP-rotation orchestrator for [Remnawave](https://github.com/remnawave/backend) VPN nodes.

This is a sidecar service that runs **next to** your Remnawave Panel and automates the boring parts:

- **Auto-spawn** new proxy nodes on Hetzner Cloud (or h2.nexus) via API + cloud-init.
- **Register** them with Remnawave Panel via the official REST API. Inherits active inbounds + config profile from an existing peer node so user subscriptions never lose inbound coverage.
- **Publish** a Cloudflare A record so your subscription URL always points to a healthy node.
- **Cross-region balancing:** each spawn picks the least-populated Hetzner location from `HETZNER_ALLOWED_LOCATIONS`.
- **Rotate** IPs on three triggers:
  1. Cron schedule (`ROTATION_SCHEDULE_CRON`, default every 6 h).
  2. Automatic probe: every 5 min we check each node's IP from 4 Russian vantage points via [check-host.net](https://check-host.net). Three consecutive failures → rotate.
  3. Manual via REST API, Web UI, or `remna-ctl rotate <id>`.
- **Graceful drain** — during rotation the old node is first disabled in Remnawave (so new clients no longer land on it), then we wait `NODE_DRAIN_SECONDS` for existing connections to finish before destroying.
- **Decommission** old nodes (panel delete + DNS delete + VPS destroy) once the replacement is serving traffic.
- **Notifications:** every rotation / spawn / destroy fires Telegram and/or Discord webhook events.
- **Web UI** (`/ui`) — HTMX dashboard: view nodes, trigger spawn/rotate/destroy, browse recent rotation audit log.
- **PostgreSQL** or SQLite backend (`DATABASE_URL`). `docker-compose.yml` ships an optional Postgres 16 service.

> **Target use case:** you already run a Remnawave panel, you sell VLESS/Reality/Trojan subscriptions, and Roskomnadzor keeps blacklisting your server IPs. Instead of manually rebuilding VPS every time an IP goes dark, this daemon does it for you.

## Why "just add rotation to Remnawave"?

- Remnawave's multi-node model assumes you manually add nodes in the UI.
- Panel doesn't know which clouds you use, doesn't call the Hetzner API, doesn't touch DNS.
- This orchestrator is a **thin automation layer** — all your users, subscriptions, inbounds etc. stay in Remnawave.

## Architecture

```
 ┌───────────────────────┐     ┌──────────────────┐
 │ Remnawave Panel       │◀───▶│ remna-orchestrator│
 │ (users, subs, stats)  │ API │ (this repo)       │
 └───────────────────────┘     │                   │
                                │ ┌───────────────┐ │
                                │ │ Scheduler     │ │
                                │ │  cron + probe │ │
                                │ └─────┬─────────┘ │
                                │       ▼           │
                                │ ┌───────────────┐ │
                                │ │ Engine        │ │
                                │ └──┬──────┬──┬───┘ │
                                └────┼──────┼──┼─────┘
                                     ▼      ▼  ▼
                              ┌─────────┐ ┌────┐ ┌───────────┐
                              │ Hetzner │ │CF  │ │ Remnawave │
                              │ Cloud   │ │DNS │ │ Panel API │
                              └────┬────┘ └────┘ └───────────┘
                                   ▼
                          ┌────────────────┐
                          │ Remnawave Node │ × N
                          │ (Xray on VPS)  │
                          └────────────────┘
```

## Prerequisites

1. **Running Remnawave Panel** (self-hosted). Installation: https://docs.rw/docs/install/remnawave-panel/
2. **Remnawave API token** from `Settings → API Keys` in the panel.
3. **Hetzner Cloud project** with an API token (Read+Write). Create it at https://console.hetzner.cloud/ → project → Security → API Tokens.
4. **Cloudflare account** managing your domain, plus an API token with `Zone.DNS:Edit` permission scoped to that zone (https://dash.cloudflare.com/profile/api-tokens).
5. **Domain** added to Cloudflare (e.g. `example.com`). The orchestrator will publish one A-record per node (e.g. `rw-20260424-3f1a.example.com`).

## Quick start (Docker)

```bash
git clone https://github.com/<you>/remna-orchestrator.git
cd remna-orchestrator
cp .env.example .env
# edit .env — set API_TOKEN, REMNAWAVE_*, HETZNER_TOKEN, CLOUDFLARE_*
docker compose up -d --build
docker compose logs -f orchestrator
```

Open:
- http://localhost:8080/ui — Web dashboard (login with your `API_TOKEN`)
- http://localhost:8080/docs — Swagger UI with all REST endpoints

### Using PostgreSQL instead of SQLite

`docker-compose.yml` includes an optional `postgres` service. To use it:

```bash
# In your .env
DATABASE_URL=postgresql+asyncpg://remna:remna@postgres:5432/remna
docker compose up -d --build
```

SQLite is the default; swap the URL as shown to switch.

## Quick start (bare-metal Python)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env
# edit .env

remna-ctl init-db
uvicorn app.api.app:app --host 0.0.0.0 --port 8080
```

## CLI

```
remna-ctl init-db          # create schema
remna-ctl list             # list tracked nodes
remna-ctl spawn            # provision + register + DNS for one new node
remna-ctl rotate 3         # replace node with id=3
remna-ctl ensure-pool      # spawn nodes until pool reaches NODE_POOL_MIN_SIZE
```

## REST API

All endpoints except `/health` and `/` require:
```
Authorization: Bearer <API_TOKEN>
```

| method | path | description |
|---|---|---|
| GET | `/health` | liveness probe |
| GET | `/api/nodes` | list tracked nodes |
| POST | `/api/nodes` | spawn new node |
| DELETE | `/api/nodes/{id}` | decommission a node |
| POST | `/api/rotation/{node_id}` | rotate that node (manual trigger) |
| POST | `/api/rotation/ensure-pool` | top up the pool |

Example:
```bash
curl -X POST http://localhost:8080/api/rotation/ensure-pool \
     -H "Authorization: Bearer $API_TOKEN"
```

## How rotation works

When a rotation is triggered for node `N`:

1. Orchestrator calls `hetzner.servers.create(..., user_data=<cloud-init>)`. Cloud-init installs Docker and brings up `remnawave/node:latest`.
2. After ~2 minutes, the orchestrator adds the new node to Remnawave Panel via `POST /api/nodes`.
3. A new Cloudflare A record is created (`<node-label>.example.com` → new IP, TTL 60 s).
4. The old node is disabled and deleted from the panel, its Cloudflare record is removed, and the Hetzner server is destroyed.
5. Audit entry is written to `rotation_events` with reason (`scheduled`, `probe_failed`, or `manual`).

Throughout the switch, all **other** nodes in the pool continue serving traffic; clients connected to the retired node experience a single reconnect.

## RKN probe

The probe uses check-host.net's public API to test `node_ip:443` from multiple Russian vantage points (`ru1`–`ru4.node.check-host.net`). If all of them fail `PROBE_FAILURE_THRESHOLD` times in a row (default 3 × 5 min = 15 min), the node is considered blacklisted and rotated automatically.

The probe fails **open** — if check-host itself is down or returns no verdict, we don't trigger rotation.

## Cloud provider support

| provider | dynamic spawn | IP rotation | notes |
|---|---|---|---|
| **Hetzner Cloud** | ✅ | ✅ (replace primary IP) | default, tested |
| **h2.nexus** | ❌ (billing-gated) | ✅ (virtual geo-IP) | pre-provision your pool, orchestrator rotates IPs via BillManager API |

Adding a new provider: implement `app/cloud_adapters/base.py::CloudAdapter` and register it in `factory.py`.

## Security notes

- The orchestrator holds production credentials (Hetzner, Cloudflare, Remnawave). Never expose port 8080 to the public internet without a reverse proxy + HTTPS + firewall allow-list.
- Rotate `API_TOKEN` periodically.
- The cloud-init template only opens port 22 + port 443 + the Remnawave-panel-only internal port. Make sure `REMNAWAVE_BASE_URL`'s host is correct — it's used for the UFW allow-from rule on each node.

## Notifications

Set any of the following in `.env` to enable channels:

```
TELEGRAM_BOT_TOKEN=123456:...   # create via @BotFather
TELEGRAM_CHAT_ID=123456789      # your chat/group ID
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Both are optional and independent. When any channel is configured, every `rotation_started`, `rotation_success`, `rotation_failed`, `node_spawned`, and `node_destroyed` event is forwarded. Each channel fails open — if Telegram is down we still send to Discord and vice versa; the orchestrator never blocks on notification failures.

## Web UI

Enabled by default (`UI_ENABLED=true`). Dashboard is at http://localhost:8080/ui.

Auth: session cookie set by `/ui/login` — enter the same `API_TOKEN` you use for the REST API. The cookie is signed with `UI_SESSION_SECRET` (set it to something long and random) and lasts 24 h.

Features: list nodes (live status, probe failures, region), trigger spawn / rotate / destroy via HTMX buttons, see the last 20 rotations with reason + success/failure.

## Not implemented (contributions welcome)

- Migration of users' subscription URLs to a stable subdomain (currently each node gets its own FQDN — fine if clients use panel-provided subscription links which already abstract this).
- HA orchestrator (two orchestrator instances against the same Postgres, with leader election).
- Per-user traffic routing policies (beyond Remnawave's built-in support).

## License

MIT.
