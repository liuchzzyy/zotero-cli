"""`zot config init` / `zot config show` - 创建并查看配置文件。"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from zotero_cli.config import (
    AppConfig,
    ConfigError,
    detect_zotero_data_dir,
    get_data_dir,
    list_profiles,
    load_config,
    merge_config_data,
    read_config_data,
    resolve_config_path,
    write_config_data,
)
from zotero_cli.output.envelope import envelope_ok
from zotero_cli.output.exit_codes import emit_error


def _json_out(ctx: typer.Context) -> bool:
    return bool(ctx.obj.get("json", False)) if ctx.obj else False


def _profile(ctx: typer.Context) -> str | None:
    profile = ctx.obj.get("profile") if ctx.obj else None
    return str(profile) if profile else None


def _emit_config_error(ctx: typer.Context, exc: ConfigError) -> None:
    emit_error(
        "configuration_error",
        str(exc),
        output_json=_json_out(ctx),
        hint="Fix the config file or run 'zot config init'.",
        context="config",
    )


def init_command(
    ctx: typer.Context,
    config_path: str | None = typer.Option(None, "--config-path", help="配置文件路径。"),
    data_dir: str | None = typer.Option(None, "--data-dir", help="Zotero 数据目录（缺省自动检测）。"),
    library_id: str | None = typer.Option(None, "--library-id", help="Zotero 库 ID。"),
    api_key: str | None = typer.Option(None, "--api-key", help="Zotero API key。"),
    dry_run: bool = typer.Option(False, "--dry-run", help="仅预览合并结果，不写入。"),
) -> None:
    """初始化或更新配置，保留已有的配置段与 profile。"""
    json_out = _json_out(ctx)
    profile = _profile(ctx)
    no_interaction = bool(ctx.obj.get("no_interaction", False)) if ctx.obj else False

    try:
        path = resolve_config_path(config_path)
        existing_data = read_config_data(path) if path.exists() else {}
        existing_profiles = list_profiles(path) if path.exists() else []
        if path.exists() and (profile is None or profile in existing_profiles):
            existing = load_config(path, profile=profile)
        else:
            existing = AppConfig()
    except ConfigError as exc:
        _emit_config_error(ctx, exc)
        return

    detected_dir = detect_zotero_data_dir(AppConfig())
    interactive = not no_interaction and not json_out

    if interactive:
        if library_id is None:
            library_id = typer.prompt("Zotero library ID", default=existing.library_id)
        if api_key is None:
            api_key = (
                typer.prompt("Zotero API key (leave blank to keep current)", default="", hide_input=True)
                or existing.api_key
            )
        if data_dir is None:
            data_dir = typer.prompt("Zotero data directory", default=existing.data_dir or str(detected_dir))
    else:
        if library_id is None:
            library_id = existing.library_id
        if api_key is None:
            api_key = existing.api_key
        if data_dir is None:
            data_dir = existing.data_dir or str(detected_dir)

    if data_dir:
        data_dir = str(Path(data_dir).expanduser().resolve())

    cfg = AppConfig(
        data_dir=data_dir or "",
        library_id=library_id or "",
        api_key=api_key or "",
        semantic_scholar_api_key=existing.semantic_scholar_api_key,
        crossref_mailto=existing.crossref_mailto,
        default_format=existing.default_format,
        default_limit=existing.default_limit,
        default_export_style=existing.default_export_style,
        prefs_js_path=existing.prefs_js_path,
    )

    warnings: list[str] = []
    if cfg.data_dir:
        dd = Path(cfg.data_dir)
        if not dd.exists():
            warnings.append(f"Directory does not exist: {dd}")
        elif not (dd / "zotero.sqlite").exists():
            warnings.append(f"zotero.sqlite not found in {dd}")

    backup: Path | None = None
    try:
        merged, changed = merge_config_data(existing_data, cfg, profile=profile, allow_new_profile=True)
        if not dry_run:
            backup = write_config_data(path, merged)
    except ConfigError as exc:
        _emit_config_error(ctx, exc)
        return

    merged_default = merged.get("default", {})
    effective_profile = profile or (
        str(merged_default.get("profile", "")).strip() if isinstance(merged_default, dict) else ""
    )
    payload = {
        "config_path": str(path),
        "profile": effective_profile,
        "data_dir": cfg.data_dir,
        "library_id": cfg.library_id,
        "api_key_set": bool(cfg.api_key),
        "changed": changed,
        "backup_path": str(backup) if backup is not None else "",
        "warnings": warnings,
    }
    if json_out:
        extra = {"dry_run": True} if dry_run else None
        typer.echo(json.dumps(envelope_ok(payload, extra=extra), indent=2, ensure_ascii=False))
        return

    prefix = "Would save" if dry_run else "Configuration saved to"
    typer.echo(f"{prefix} {path}")
    if effective_profile:
        typer.echo(f"  Profile: {effective_profile}")
    typer.echo(f"  Data directory: {cfg.data_dir or '(auto-detect)'}")
    for warning in warnings:
        typer.echo(typer.style(f"  WARNING: {warning}", fg="yellow"))


def show_command(
    ctx: typer.Context,
    config_path: str | None = typer.Option(None, "--config-path", help="配置文件路径。"),
) -> None:
    """显示当前配置。"""
    json_out = _json_out(ctx)
    profile = _profile(ctx)
    try:
        path = resolve_config_path(config_path, must_exist=config_path is not None)
        cfg = load_config(path, profile=profile)
    except ConfigError as exc:
        _emit_config_error(ctx, exc)
        return

    data_dir = get_data_dir(cfg)
    db_file = data_dir / "zotero.sqlite"
    data_dir_exists = data_dir.exists()
    database_ok = db_file.exists()
    data = {
        "config_path": str(path),
        "profile": profile or "",
        "library_id": cfg.library_id,
        "api_key_set": bool(cfg.api_key),
        "api_key_tail": cfg.api_key[-4:] if len(cfg.api_key) > 4 else "",
        "data_dir": cfg.data_dir or "(auto-detect)",
        "resolved_data_dir": str(data_dir),
        "default_format": cfg.default_format,
        "default_limit": cfg.default_limit,
        "default_export_style": cfg.default_export_style,
        "database_path": str(db_file),
        "data_dir_exists": data_dir_exists,
        "database_ok": database_ok,
    }
    if json_out:
        typer.echo(json.dumps(envelope_ok(data), indent=2, ensure_ascii=False))
        return

    typer.echo(f"Config:     {path}")
    if profile:
        typer.echo(f"Profile:    {profile}")
    typer.echo(f"Library ID: {cfg.library_id}")
    if len(cfg.api_key) > 4:
        api_key_display = "***" + cfg.api_key[-4:]
    elif cfg.api_key:
        api_key_display = "***"
    else:
        api_key_display = "(not set)"
    typer.echo(f"API Key:    {api_key_display}")
    typer.echo(f"Data Dir:   {cfg.data_dir or '(auto-detect)'}")
    typer.echo(f"Format:     {cfg.default_format}")
    typer.echo(f"Limit:      {cfg.default_limit}")
    typer.echo(f"Export:     {cfg.default_export_style}")

    if not data_dir_exists:
        typer.echo(typer.style(f"\nWARNING: Data directory not found: {data_dir}", fg="yellow"))
    elif not database_ok:
        typer.echo(typer.style(f"\nWARNING: zotero.sqlite not found in {data_dir}", fg="yellow"))
    else:
        typer.echo(typer.style(f"\nDatabase:   {db_file} (OK)", fg="green"))
