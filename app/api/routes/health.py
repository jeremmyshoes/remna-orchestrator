"""Public health endpoint."""

from fastapi import APIRouter

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@router.get("/")
async def root() -> dict:
    return {
        "service": "remna-orchestrator",
        "docs": "/docs",
        "health": "/health",
    }
