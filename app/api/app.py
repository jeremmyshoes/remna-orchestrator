"""FastAPI app factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.deps import close_engine, get_engine
from app.api.routes import health, nodes, rotation
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.models.base import create_all
from app.rotation.scheduler import start_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    configure_logging(s.log_level)
    await create_all()
    engine = get_engine()
    scheduler = start_scheduler(engine)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await close_engine()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Remna Orchestrator",
        version="0.1.0",
        description="Auto-deploy and IP rotation for Remnawave VPN nodes",
        lifespan=lifespan,
    )
    app.include_router(health.router)
    app.include_router(nodes.router, prefix="/api/nodes", tags=["nodes"])
    app.include_router(rotation.router, prefix="/api/rotation", tags=["rotation"])
    return app


app = create_app()
