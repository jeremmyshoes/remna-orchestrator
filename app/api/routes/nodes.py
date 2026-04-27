"""Node CRUD + pool inspection."""

from __future__ import annotations

import contextlib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import AuthDep, EngineDep
from app.models import Node, NodeStatus
from app.models.base import session_scope
from app.rotation.engine import RotationEngine

router = APIRouter(dependencies=[AuthDep])


class NodeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    cloud_id: str | None
    cloud_provider: str
    location: str
    ipv4: str
    fqdn: str | None
    remnawave_uuid: str | None
    status: NodeStatus


@router.get("", response_model=list[NodeOut])
async def list_nodes() -> list[NodeOut]:
    async with session_scope() as s:
        rows = (await s.execute(select(Node).order_by(Node.id.asc()))).scalars().all()
        return [NodeOut.model_validate(r) for r in rows]


@router.post("", response_model=NodeOut, status_code=201)
async def create_node(engine: RotationEngine = EngineDep) -> NodeOut:
    node = await engine.spawn_node()
    return NodeOut.model_validate(node)


@router.delete("/{node_id}", status_code=204)
async def delete_node(node_id: int, engine: RotationEngine = EngineDep) -> None:
    async with session_scope() as s:
        node = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
        if not node:
            raise HTTPException(404, "Node not found")
        # Use rotation.rotate_node paths to disable+delete both sides.
        if node.remnawave_uuid:
            with contextlib.suppress(Exception):
                await engine.rw.disable_node(node.remnawave_uuid)
                await engine.rw.delete_node(node.remnawave_uuid)
        if node.fqdn:
            short = node.fqdn.rsplit(".", 2)[0]
            with contextlib.suppress(Exception):
                await engine.dns.delete_a_record(short)
        if node.cloud_id:
            with contextlib.suppress(Exception):
                await engine.cloud.destroy_instance(node.cloud_id)
        node.status = NodeStatus.DESTROYED
