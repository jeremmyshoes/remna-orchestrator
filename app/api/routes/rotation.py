"""Manual rotation endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from app.api.deps import AuthDep, EngineDep
from app.models import Node, RotationReason
from app.models.base import session_scope
from app.rotation.engine import RotationEngine

router = APIRouter(dependencies=[AuthDep])


@router.post("/{node_id}")
async def rotate_node(
    node_id: int,
    engine: RotationEngine = EngineDep,
) -> dict:
    async with session_scope() as s:
        node = (await s.execute(select(Node).where(Node.id == node_id))).scalar_one_or_none()
        if not node:
            raise HTTPException(404, "Node not found")
    new = await engine.rotate_node(node_id, RotationReason.MANUAL)
    return {"status": "ok", "new_node": {"id": new.id, "name": new.name, "ipv4": new.ipv4}}


@router.post("/ensure-pool")
async def ensure_pool(engine: RotationEngine = EngineDep) -> dict:
    await engine.ensure_pool()
    return {"status": "ok"}
