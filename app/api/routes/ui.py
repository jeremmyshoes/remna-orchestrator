"""Web UI (Jinja + HTMX).

Cookie-based session auth (signed with itsdangerous via SessionMiddleware).
Users log in with the same API_TOKEN that the REST API uses.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from app.api.deps import EngineDep
from app.core.config import get_settings
from app.models import Node, NodeStatus, RotationEvent, RotationReason
from app.models.base import session_scope
from app.notifications.base import NotificationEvent
from app.rotation.engine import RotationEngine

log = logging.getLogger(__name__)

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


async def _list_nodes() -> list[Node]:
    async with session_scope() as s:
        return (
            await s.execute(
                select(Node)
                .where(Node.status != NodeStatus.DESTROYED)
                .order_by(Node.id.asc())
            )
        ).scalars().all()


async def _render_nodes_table(
    request: Request, *, error: str | None = None
) -> HTMLResponse:
    nodes = await _list_nodes()
    return templates.TemplateResponse(
        request, "ui/_nodes_table.html",
        {"nodes": nodes, "cfg": get_settings(), "error": error},
    )


async def _render_dashboard(request: Request) -> HTMLResponse:
    async with session_scope() as s:
        nodes = (
            await s.execute(
                select(Node)
                .where(Node.status != NodeStatus.DESTROYED)
                .order_by(Node.id.asc())
            )
        ).scalars().all()
        events = (
            await s.execute(
                select(RotationEvent).order_by(desc(RotationEvent.created_at)).limit(20)
            )
        ).scalars().all()
    return templates.TemplateResponse(
        request, "ui/dashboard.html",
        {"nodes": nodes, "events": events, "cfg": get_settings(), "error": None},
    )


# --------------------------------------------------------------------- auth
@router.get("/ui/login", response_class=HTMLResponse)
async def login_form(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "ui/login.html", {"error": error}
    )


@router.post("/ui/login")
async def login_submit(request: Request, token: str = Form(...)) -> RedirectResponse:
    expected = get_settings().api_token.get_secret_value()
    if not expected or token != expected:
        return RedirectResponse(url="/ui/login?error=Invalid+token", status_code=303)
    request.session["authed"] = True
    return RedirectResponse(url="/ui/", status_code=303)


@router.get("/ui/logout")
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/ui/login", status_code=303)


# ---------------------------------------------------------------- dashboard
@router.get("/ui", response_class=HTMLResponse, include_in_schema=False)
@router.get("/ui/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard(request: Request) -> HTMLResponse:
    if not request.session.get("authed"):
        return RedirectResponse(url="/ui/login", status_code=303)
    return await _render_dashboard(request)


# ---------------------------------------------------------------- HTMX bits
# Failures in these handlers are caught and surfaced to the user as a banner
# in the returned partial, rather than silently swallowed — the engine calls
# can create billable cloud resources, so the user MUST see errors.

@router.post("/ui/nodes/spawn", response_class=HTMLResponse)
async def ui_spawn(
    request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    error = None
    try:
        await engine.spawn_node()
    except Exception as exc:
        log.exception("ui: spawn_node failed")
        error = f"Spawn failed: {exc!r}"
    return await _render_nodes_table(request, error=error)


@router.post("/ui/pool/ensure", response_class=HTMLResponse)
async def ui_ensure_pool(
    request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    error = None
    try:
        await engine.ensure_pool()
    except Exception as exc:
        log.exception("ui: ensure_pool failed")
        error = f"Ensure pool failed: {exc!r}"
    return await _render_nodes_table(request, error=error)


@router.post("/ui/nodes/{node_id}/rotate", response_class=HTMLResponse)
async def ui_rotate(
    node_id: int, request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    error = None
    try:
        await engine.rotate_node(node_id, RotationReason.MANUAL)
    except Exception as exc:
        log.exception("ui: rotate_node failed id=%s", node_id)
        error = f"Rotate failed: {exc!r}"
    return await _render_nodes_table(request, error=error)


@router.delete("/ui/nodes/{node_id}", response_class=HTMLResponse)
async def ui_destroy(
    node_id: int, request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    errors: list[str] = []
    async with session_scope() as s:
        node = (
            await s.execute(select(Node).where(Node.id == node_id))
        ).scalar_one_or_none()
    if node:
        # Each external call gets its own try-block so a failure in one step
        # doesn't skip the rest (otherwise we'd leave orphaned resources).
        if node.remnawave_uuid:
            try:
                await engine.rw.disable_node(node.remnawave_uuid)
            except Exception as exc:
                log.exception("ui_destroy: disable_node failed")
                errors.append(f"disable_node: {exc!r}")
            try:
                await engine.rw.delete_node(node.remnawave_uuid)
            except Exception as exc:
                log.exception("ui_destroy: delete_node failed")
                errors.append(f"delete_node: {exc!r}")
        if node.fqdn:
            short = node.fqdn.replace(f".{get_settings().cloudflare_root_domain}", "")
            try:
                await engine.dns.delete_a_record(short)
            except Exception as exc:
                log.exception("ui_destroy: delete_a_record failed")
                errors.append(f"dns: {exc!r}")
        if node.cloud_id:
            try:
                await engine.cloud.destroy_instance(node.cloud_id)
            except Exception as exc:
                log.exception("ui_destroy: destroy_instance failed")
                errors.append(f"cloud: {exc!r}")
        async with session_scope() as s:
            row = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()
            row.status = NodeStatus.DESTROYED
            s.add(RotationEvent(
                reason=RotationReason.MANUAL, from_node=node.name, to_node=None,
                success=not errors,
                details="; ".join(errors) if errors else "ui destroy",
            ))
        with contextlib.suppress(Exception):
            await engine.notifier.send(NotificationEvent(
                kind="node_destroyed",
                message=f"Destroyed node {node.name}",
                reason=RotationReason.MANUAL.value,
                from_node=node.name,
                extra={"ip": node.ipv4, "region": node.location},
            ))
    error_msg = "; ".join(errors) if errors else None
    return await _render_nodes_table(request, error=error_msg)
