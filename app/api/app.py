"""FastAPI app factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.api.deps import close_engine, get_engine
from app.api.routes import health, nodes, rotation, ui
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
    s = get_settings()
    app = FastAPI(
        title="Remna Orchestrator",
        version="0.2.0",
        description="Auto-deploy and IP rotation for Remnawave VPN nodes",
        lifespan=lifespan,
    )
    app.add_middleware(
        SessionMiddleware,
        secret_key=s.ui_session_secret.get_secret_value(),
        session_cookie="remna_session",
        max_age=86400,
        same_site="lax",
    )
    app.include_router(health.router)
    app.include_router(nodes.router, prefix="/api/nodes", tags=["nodes"])
    app.include_router(rotation.router, prefix="/api/rotation", tags=["rotation"])
    if s.ui_enabled:
        app.include_router(ui.router, tags=["ui"])
    return app


app = create_app()
