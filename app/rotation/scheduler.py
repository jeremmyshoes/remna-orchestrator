"""APScheduler setup: scheduled rotation + periodic probe."""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import select

from app.core.config import get_settings
from app.models import Node, NodeStatus, RotationReason
from app.models.base import session_scope
from app.rotation.engine import RotationEngine
from app.rotation.probe import RknProbe

log = logging.getLogger(__name__)


async def _scheduled_rotation_job(engine: RotationEngine) -> None:
    """Rotate the oldest ACTIVE node in the pool."""
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Node)
                .where(Node.status == NodeStatus.ACTIVE)
                .order_by(Node.created_at.asc())
                .limit(1)
            )
        ).scalars().all()
    if not rows:
        log.info("scheduler: no active nodes to rotate")
        return
    node = rows[0]
    try:
        await engine.rotate_node(node.id, RotationReason.SCHEDULED)
    except Exception:
        log.exception("scheduler: rotation failed")


async def _probe_job(engine: RotationEngine, probe: RknProbe) -> None:
    """Check every ACTIVE node; if it fails >= N times, rotate it."""
    s_cfg = get_settings()
    async with session_scope() as s:
        nodes = (
            await s.execute(select(Node).where(Node.status == NodeStatus.ACTIVE))
        ).scalars().all()
    for node in nodes:
        reachable = await probe.is_reachable(node.ipv4, port=443)
        async with session_scope() as s:
            refreshed = (await s.execute(select(Node).where(Node.id == node.id))).scalar_one()
            if reachable:
                refreshed.consecutive_probe_failures = 0
                refreshed.consecutive_probe_successes += 1
            else:
                refreshed.consecutive_probe_failures += 1
                refreshed.consecutive_probe_successes = 0
            refreshed.last_probe_at = refreshed.updated_at
            should_rotate = (
                refreshed.consecutive_probe_failures >= s_cfg.probe_failure_threshold
            )
            node_id = refreshed.id
        if should_rotate:
            log.warning("probe: node %s failed >= %d probes, rotating",
                        node.name, s_cfg.probe_failure_threshold)
            try:
                await engine.rotate_node(node_id, RotationReason.PROBE_FAILED)
            except Exception:
                log.exception("probe: rotation failed for %s", node.name)


def start_scheduler(engine: RotationEngine) -> AsyncIOScheduler:
    s = get_settings()
    scheduler = AsyncIOScheduler(timezone="UTC")

    if s.rotation_schedule_enabled:
        scheduler.add_job(
            _scheduled_rotation_job, args=[engine],
            trigger=CronTrigger.from_crontab(s.rotation_schedule_cron),
            id="scheduled_rotation",
            max_instances=1,
            coalesce=True,
        )
        log.info("scheduler: scheduled rotation enabled cron=%s", s.rotation_schedule_cron)

    if s.rotation_probe_enabled:
        probe = RknProbe(
            nodes=s.probe_checkhost_nodes,
            timeout=s.probe_timeout_seconds,
        )
        scheduler.add_job(
            _probe_job, args=[engine, probe],
            trigger=IntervalTrigger(seconds=s.rotation_probe_interval_seconds),
            id="rkn_probe",
            max_instances=1,
            coalesce=True,
        )
        log.info("scheduler: RKN probe enabled interval=%ds",
                 s.rotation_probe_interval_seconds)

    scheduler.start()
    return scheduler
