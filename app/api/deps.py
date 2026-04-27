"""FastAPI dependencies: auth, engine singleton."""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from app.core.config import get_settings
from app.rotation.engine import RotationEngine

_engine_singleton: RotationEngine | None = None


def get_engine() -> RotationEngine:
    global _engine_singleton
    if _engine_singleton is None:
        _engine_singleton = RotationEngine()
    return _engine_singleton


async def close_engine() -> None:
    global _engine_singleton
    if _engine_singleton is not None:
        await _engine_singleton.aclose()
        _engine_singleton = None


def require_token(authorization: str | None = Header(default=None)) -> None:
    s = get_settings()
    expected = s.api_token.get_secret_value()
    if not expected:
        raise HTTPException(status_code=500, detail="API token not configured")
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    if token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


AuthDep = Depends(require_token)
EngineDep = Depends(get_engine)
