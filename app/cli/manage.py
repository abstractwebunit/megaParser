"""Management CLI: import accounts, import proxies, assign, health-check, db backup.

Usage:
    python -m app.cli.manage gen-key
    python -m app.cli.manage import-accounts path/to/211.zip
    python -m app.cli.manage import-proxies path/to/proxies.txt
    python -m app.cli.manage test-proxies
    python -m app.cli.manage assign-proxies
    python -m app.cli.manage health-check
    python -m app.cli.manage backup
    python -m app.cli.manage account-enable <name>
    python -m app.cli.manage account-disable <name>
    python -m app.cli.manage seed-group <@username>
    python -m app.cli.manage add-keyword <word>
"""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

import click
from loguru import logger

from app.cli import importer, proxy_pool
from app.crypto import generate_key, get_crypto
from app.db.base import create_db, dispose_db
from app.db import repo
from app.log import setup_logging
from app.settings import get_settings


def _init() -> tuple:
    settings = get_settings()
    setup_logging(settings.log_dir, settings.log_level)
    get_crypto(settings.fernet_key)
    _, sf = create_db(settings.database_url)
    return settings, sf


@click.group()
def cli() -> None:
    """megaParser management CLI"""


@cli.command("gen-key")
def gen_key() -> None:
    """Generate a Fernet key for FERNET_KEY env var."""
    click.echo(generate_key())


@cli.command("import-accounts")
@click.argument("source", type=click.Path(exists=True, path_type=Path))
def import_accounts(source: Path) -> None:
    """Import accounts from ZIP or directory with .session+.json pairs."""

    async def _run() -> None:
        settings, sf = _init()
        try:
            res = await importer.import_accounts(sf, source, settings.data_dir)
            click.echo(
                f"total={res.total} imported={res.imported} "
                f"skipped_spamblock={res.skipped_spamblock} errors={res.errors}"
            )
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("import-proxies")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--scheme", default="http", help="Default scheme (http/socks5)")
def import_proxies(path: Path, scheme: str) -> None:
    async def _run() -> None:
        _, sf = _init()
        try:
            added = await proxy_pool.import_to_db(sf, path, scheme)
            click.echo(f"imported {added} proxies")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("test-proxies")
@click.option("--concurrency", default=10, type=int)
def test_proxies(concurrency: int) -> None:
    async def _run() -> None:
        _, sf = _init()
        try:
            ok, bad = await proxy_pool.test_all(sf, concurrency=concurrency)
            click.echo(f"ok={ok} bad={bad}")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("assign-proxies")
def assign_proxies() -> None:
    async def _run() -> None:
        _, sf = _init()
        try:
            n = await proxy_pool.assign_round_robin(sf)
            click.echo(f"assigned {n}")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("health-check")
@click.option("--name", default=None, help="Check only one account by name")
def health_check(name: str | None) -> None:
    async def _run() -> None:
        from app.core.account_manager import AccountManager

        settings, sf = _init()
        try:
            mgr = AccountManager(sf, settings)
            await mgr.load_all(max_concurrent=10)
            rows = mgr.stats()
            for r in rows:
                if name and r["name"] != name:
                    continue
                click.echo(
                    f"{r['name']}: connected={r['connected']} status={r['status']} "
                    f"role={r['role']}"
                )
            await mgr.disconnect_all()
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("backup")
def backup() -> None:
    """pg_dump current DB into dumps/megaparser_<date>.sql.gz"""
    from datetime import datetime

    settings = get_settings()
    dumps = Path("./dumps")
    dumps.mkdir(parents=True, exist_ok=True)
    out = dumps / f"megaparser_{datetime.utcnow().strftime('%Y%m%d_%H%M')}.sql.gz"
    url = settings.database_url.replace("+asyncpg", "")
    cmd = f'pg_dump "{url}" | gzip > "{out}"'
    click.echo(f"running: {cmd}")
    code = subprocess.call(cmd, shell=True)
    if code == 0:
        click.echo(f"saved {out}")
    else:
        click.echo(f"FAILED exit={code}", err=True)
        sys.exit(code)


@cli.command("account-enable")
@click.argument("name")
def account_enable(name: str) -> None:
    async def _run() -> None:
        _, sf = _init()
        try:
            acc = await repo.get_account_by_name(sf, name)
            if not acc:
                click.echo(f"not found: {name}", err=True)
                return
            await repo.set_account_enabled(sf, acc.id, True)
            click.echo(f"enabled {name}")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("account-disable")
@click.argument("name")
def account_disable(name: str) -> None:
    async def _run() -> None:
        _, sf = _init()
        try:
            acc = await repo.get_account_by_name(sf, name)
            if not acc:
                click.echo(f"not found: {name}", err=True)
                return
            await repo.set_account_enabled(sf, acc.id, False)
            click.echo(f"disabled {name}")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("seed-group")
@click.argument("username")
def seed_group(username: str) -> None:
    async def _run() -> None:
        _, sf = _init()
        try:
            u = username.lstrip("@")
            gid = await repo.upsert_target_group(
                sf,
                {
                    "tg_id": None,
                    "username": u,
                    "discovered_via": "manual",
                    "depth": 0,
                },
            )
            tid = await repo.create_task(sf, "scan_messages", target=u, priority=10)
            click.echo(f"group id={gid}, task #{tid}")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("bulk-seed")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def bulk_seed(path: Path) -> None:
    """Read a text file with one @username per line (# for comments) and create a
    TargetGroup + scan_messages ParserTask for each. Idempotent via upsert/unique."""
    import re

    async def _run() -> None:
        _, sf = _init()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            targets: list[str] = []
            for raw in lines:
                s = raw.strip().lstrip("\ufeff")
                if not s or s.startswith("#"):
                    continue
                m = re.search(r"(?:t\.me/|@)?([A-Za-z][A-Za-z0-9_]{3,})", s)
                if m:
                    targets.append(m.group(1))
            click.echo(f"parsed {len(targets)} usernames from {path}")
            created = 0
            for u in targets:
                await repo.upsert_target_group(
                    sf,
                    {
                        "tg_id": None,
                        "username": u,
                        "discovered_via": "manual",
                        "depth": 0,
                    },
                )
                await repo.create_task(sf, "scan_messages", target=u, priority=8)
                created += 1
            click.echo(f"seeded {created} groups with scan_messages tasks")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("add-keyword")
@click.argument("word")
def add_keyword(word: str) -> None:
    async def _run() -> None:
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from app.db.models import Keyword

        _, sf = _init()
        try:
            async with sf() as s, s.begin():
                stmt = (
                    pg_insert(Keyword).values(word=word).on_conflict_do_nothing(
                        index_elements=["word"]
                    )
                )
                await s.execute(stmt)
            click.echo(f"added keyword '{word}'")
        finally:
            await dispose_db()

    asyncio.run(_run())


@cli.command("migrate")
def migrate() -> None:
    """Run alembic upgrade head."""
    code = subprocess.call(["alembic", "upgrade", "head"])
    sys.exit(code)


if __name__ == "__main__":
    cli()
