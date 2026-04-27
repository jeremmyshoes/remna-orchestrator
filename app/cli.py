"""remna-ctl: command-line control for the orchestrator (bypasses HTTP API)."""

from __future__ import annotations

import asyncio

import typer
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from app.core.logging import configure_logging
from app.models import Node, RotationReason
from app.models.base import create_all, session_scope
from app.rotation.engine import RotationEngine

app = typer.Typer(help="Control Remna Orchestrator from the command line.")
console = Console()


def _run(coro):
    return asyncio.run(coro)


@app.command("init-db")
def init_db() -> None:
    """Create the SQLite schema if it does not exist."""
    configure_logging()
    _run(create_all())
    console.print("[green]database initialized[/]")


@app.command("list")
def list_nodes() -> None:
    """List all nodes tracked by the orchestrator."""
    configure_logging()

    async def _do():
        await create_all()
        async with session_scope() as s:
            return (await s.execute(select(Node).order_by(Node.id.asc()))).scalars().all()

    rows = _run(_do())
    table = Table(title="Nodes")
    for col in ("id", "name", "cloud", "ipv4", "location", "status", "remnawave_uuid"):
        table.add_column(col)
    for n in rows:
        table.add_row(
            str(n.id),
            n.name,
            n.cloud_provider,
            n.ipv4,
            n.location,
            n.status.value,
            (n.remnawave_uuid or "")[:8],
        )
    console.print(table)


@app.command("spawn")
def spawn(label: str | None = typer.Option(None)) -> None:
    """Provision a new Remnawave node."""
    configure_logging()

    async def _do():
        await create_all()
        engine = RotationEngine()
        try:
            return await engine.spawn_node(label)
        finally:
            await engine.aclose()

    node = _run(_do())
    console.print(f"[green]spawned[/] id={node.id} name={node.name} ip={node.ipv4}")


@app.command("rotate")
def rotate(node_id: int) -> None:
    """Rotate (replace) a node by id."""
    configure_logging()

    async def _do():
        await create_all()
        engine = RotationEngine()
        try:
            return await engine.rotate_node(node_id, RotationReason.MANUAL)
        finally:
            await engine.aclose()

    new = _run(_do())
    console.print(f"[green]rotated[/] -> new id={new.id} name={new.name} ip={new.ipv4}")


@app.command("ensure-pool")
def ensure_pool() -> None:
    """Ensure the pool contains at least node_pool_min_size nodes."""
    configure_logging()

    async def _do():
        await create_all()
        engine = RotationEngine()
        try:
            await engine.ensure_pool()
        finally:
            await engine.aclose()

    _run(_do())
    console.print("[green]pool ensured[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
