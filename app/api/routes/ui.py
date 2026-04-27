"""Web UI (Jinja + HTMX).

Cookie-based session auth (signed with itsdangerous via SessionMiddleware).
Users log in with the same API_TOKEN that the REST API uses.
"""

from __future__ import annotations

import contextlib
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

router = APIRouter()

TEMPLATES_DIR = Path(__file__).resolve().parents[3] / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _require_session(request: Request) -> None:
    if not request.session.get("authed"):
        raise HTTPException(status_code=307, headers={"Location": "/ui/login"})


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
        {"nodes": nodes, "events": events, "cfg": get_settings()},
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
@router.post("/ui/nodes/spawn", response_class=HTMLResponse)
async def ui_spawn(
    request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    with contextlib.suppress(Exception):
        await engine.spawn_node()
    # Re-render just the nodes table (HTMX swaps it in)
    async with session_scope() as s:
        nodes = (
            await s.execute(
                select(Node)
                .where(Node.status != NodeStatus.DESTROYED)
                .order_by(Node.id.asc())
            )
        ).scalars().all()
    return templates.TemplateResponse(
        request, "ui/_nodes_table.html",
        {"nodes": nodes, "cfg": get_settings()},
    )


@router.post("/ui/pool/ensure", response_class=HTMLResponse)
async def ui_ensure_pool(
    request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    with contextlib.suppress(Exception):
        await engine.ensure_pool()
    async with session_scope() as s:
        nodes = (
            await s.execute(
                select(Node)
                .where(Node.status != NodeStatus.DESTROYED)
                .order_by(Node.id.asc())
            )
        ).scalars().all()
    return templates.TemplateResponse(
        request, "ui/_nodes_table.html",
        {"nodes": nodes, "cfg": get_settings()},
    )


@router.post("/ui/nodes/{node_id}/rotate", response_class=HTMLResponse)
async def ui_rotate(
    node_id: int, request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    with contextlib.suppress(Exception):
        await engine.rotate_node(node_id, RotationReason.MANUAL)
    async with session_scope() as s:
        nodes = (
            await s.execute(
                select(Node)
                .where(Node.status != NodeStatus.DESTROYED)
                .order_by(Node.id.asc())
            )
        ).scalars().all()
    return templates.TemplateResponse(
        request, "ui/_nodes_table.html",
        {"nodes": nodes, "cfg": get_settings()},
    )


@router.delete("/ui/nodes/{node_id}", response_class=HTMLResponse)
async def ui_destroy(
    node_id: int, request: Request, engine: RotationEngine = EngineDep
) -> HTMLResponse:
    if not request.session.get("authed"):
        raise HTTPException(401)
    async with session_scope() as s:
        node = (
            await s.execute(select(Node).where(Node.id == node_id))
        ).scalar_one_or_none()
    if node:
        # Each external call gets its own suppress block so a failure in one
        # step doesn't skip the rest (otherwise we leave orphaned resources).
        if node.remnawave_uuid:
            with contextlib.suppress(Exception):
                await engine.rw.disable_node(node.remnawave_uuid)
            with contextlib.suppress(Exception):
                await engine.rw.delete_node(node.remnawave_uuid)
        if node.fqdn:
            short = node.fqdn.replace(f".{get_settings().cloudflare_root_domain}", "")
            with contextlib.suppress(Exception):
                await engine.dns.delete_a_record(short)
        if node.cloud_id:
            with contextlib.suppress(Exception):
                await engine.cloud.destroy_instance(node.cloud_id)
        async with session_scope() as s:
            row = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one()
            row.status = NodeStatus.DESTROYED
            s.add(RotationEvent(
                reason=RotationReason.MANUAL, from_node=node.name, to_node=None,
                success=True, details="ui destroy",
            ))
        with contextlib.suppress(Exception):
            await engine.notifier.send(NotificationEvent(
                kind="node_destroyed",
                message=f"Destroyed node {node.name}",
                reason=RotationReason.MANUAL.value,
                from_node=node.name,
                extra={"ip": node.ipv4, "region": node.location},
            ))
    async with session_scope() as s:
        nodes = (
            await s.execute(
                select(Node)
                .where(Node.status != NodeStatus.DESTROYED)
                .order_by(Node.id.asc())
            )
        ).scalars().all()
    return templates.TemplateResponse(
        request, "ui/_nodes_table.html",
        {"nodes": nodes, "cfg": get_settings()},
    )
