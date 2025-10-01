from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import typer

from .config import Settings, load_settings
from .logging_setup import setup_logging
from .sync import SyncService

app = typer.Typer(help="Local HTML → Readwise Reader sync CLI")
auth_app = typer.Typer(help="Authentication helpers")
app.add_typer(auth_app, name="auth")


@dataclass(slots=True)
class AppState:
    settings: Settings
    dry_run: bool


def _resolve_config_path(config: Path | None) -> Path | None:
    if config:
        return config
    env_value = os.getenv("RW_SYNC_CONFIG")
    if env_value:
        return Path(env_value)
    default = Path.cwd() / ".rw-sync.yaml"
    return default if default.exists() else None


@app.callback()
def main(
    ctx: typer.Context,
    config: Path = typer.Option(None, "--config", exists=True, help="Path to .rw-sync.yaml"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview actions without calling Readwise"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    cfg_path = _resolve_config_path(config)
    settings = load_settings(cfg_path)
    if verbose:
        settings.log_level = "DEBUG"
    setup_logging(settings.log_level)
    settings.ensure_data_dirs()
    if not settings.watch_dir.exists():
        settings.watch_dir.mkdir(parents=True, exist_ok=True)
    ctx.obj = AppState(settings=settings, dry_run=dry_run)
    typer.secho(
        f"Config: {settings.config_path or 'defaults'}, watch_dir={settings.watch_dir}",
        fg="cyan",
        err=True,
    )
    if dry_run:
        typer.secho("Running in dry-run mode", fg="yellow", err=True)


def _get_state(ctx: typer.Context) -> AppState:
    state = ctx.obj
    if not isinstance(state, AppState):
        raise typer.BadParameter("application state missing")
    return state


@auth_app.command("check")
def auth_check(ctx: typer.Context) -> None:
    state = _get_state(ctx)

    async def _run() -> bool:
        service = SyncService(state.settings)
        try:
            return await service.auth_check()
        finally:
            await service.close()

    ok = asyncio.run(_run())
    if ok:
        typer.secho("Auth OK (204)", fg="green")
    else:
        typer.secho("Auth failed", fg="red", err=True)
        raise typer.Exit(code=1)


@app.command()
def push(
    ctx: typer.Context,
    all: bool = typer.Option(False, "--all", help="Push every matching file"),
    new: bool = typer.Option(
        False,
        "--new",
        help=(
            "Push only unseen files; by default scans incrementally since the last run. "
            "Use --since to override the starting point."
        ),
    ),
    max: int | None = typer.Option(None, "--max", min=1, help="Maximum files to process"),
    since: datetime | None = typer.Option(None, "--since", formats=["%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"], help="Only process files modified on/after this timestamp"),
) -> None:
    state = _get_state(ctx)
    if all == new:
        typer.secho("Use exactly one of --all or --new", fg="red", err=True)
        raise typer.Exit(code=2)
    mode = "all" if all else "new"
    epoch = since.timestamp() if since else None

    async def _run() -> dict[str, int]:
        service = SyncService(state.settings)
        try:
            stats = await service.push(mode=mode, dry_run=state.dry_run, max_items=max, since=epoch)
            return stats.summary()
        finally:
            await service.close()

    summary = asyncio.run(_run())
    typer.echo(summary)


@app.command()
def replay(
    ctx: typer.Context,
    date_option: str | None = typer.Option(None, "--date", help="Replay failures from this date (YYYY-MM-DD)"),
) -> None:
    state = _get_state(ctx)
    if date_option:
        try:
            target_date = datetime.strptime(date_option, "%Y-%m-%d").date()
        except ValueError:
            typer.secho("--date must be in YYYY-MM-DD format", fg="red", err=True)
            raise typer.Exit(code=2) from None
    else:
        target_date = date.today()

    async def _run() -> dict[str, int]:
        service = SyncService(state.settings)
        try:
            stats = await service.replay(date=target_date, dry_run=state.dry_run)
            return stats.summary()
        finally:
            await service.close()

    summary = asyncio.run(_run())
    typer.echo(summary)


__all__ = ["app"]
