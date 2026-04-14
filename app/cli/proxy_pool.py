"""Parse proxies.txt, validate, import into DB, round-robin assign to accounts.

Accepted line formats:
    login:password@host:port                            (HTTP/HTTPS by default)
    http://login:password@host:port
    socks5://login:password@host:port
    host:port                                           (no auth)
"""
import asyncio
import re
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp_socks import ProxyConnector
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import repo
from app.db.models import TelegramAccount
from sqlalchemy import select, update

_PROXY_RE = re.compile(
    r"^(?:(?P<scheme>\w+)://)?"
    r"(?:(?P<user>[^:@\s]+):(?P<pwd>[^@\s]+)@)?"
    r"(?P<host>[\w\.\-]+):(?P<port>\d+)\s*$"
)


def parse_proxy_line(line: str, default_scheme: str = "http") -> dict[str, Any] | None:
    raw = line.strip().lstrip("\ufeff")
    if not raw or raw.startswith("#"):
        return None
    m = _PROXY_RE.match(raw)
    if not m:
        return None
    scheme = (m.group("scheme") or default_scheme).lower()
    return {
        "scheme": scheme,
        "host": m.group("host"),
        "port": int(m.group("port")),
        "username": m.group("user"),
        "password": m.group("pwd"),
    }


def parse_file(path: Path, default_scheme: str = "http") -> list[dict[str, Any]]:
    proxies: list[dict[str, Any]] = []
    bad = 0
    for lineno, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        parsed = parse_proxy_line(line, default_scheme)
        if parsed is None:
            if line.strip() and not line.strip().startswith("#"):
                bad += 1
                logger.warning("skip bad proxy line {}: {}", lineno, line.strip())
            continue
        proxies.append(parsed)
    logger.info("parsed {} proxies from {} ({} bad lines)", len(proxies), path, bad)
    return proxies


def proxy_to_pyrogram_dict(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "scheme": p["scheme"],
        "hostname": p["host"],
        "port": p["port"],
        "username": p.get("username"),
        "password": p.get("password"),
    }


async def import_to_db(
    sf: async_sessionmaker[AsyncSession], path: Path, default_scheme: str = "http"
) -> int:
    proxies = parse_file(path, default_scheme)
    added = 0
    for p in proxies:
        await repo.upsert_proxy(sf, p)
        added += 1
    return added


# ---------------- validation ----------------


async def _ping_proxy(p: dict[str, Any], timeout: float = 10.0) -> bool:
    url = "https://www.google.com/generate_204"
    scheme = p["scheme"]
    host = p["host"]
    port = p["port"]
    user = p.get("username")
    pwd = p.get("password")
    auth = f"{user}:{pwd}@" if user and pwd else ""
    proxy_url = f"{scheme}://{auth}{host}:{port}"

    try:
        if scheme in ("socks4", "socks5"):
            connector = ProxyConnector.from_url(proxy_url)
            async with aiohttp.ClientSession(connector=connector) as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as r:
                    return r.status in (200, 204)
        else:
            async with aiohttp.ClientSession() as s:
                async with s.get(
                    url,
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as r:
                    return r.status in (200, 204)
    except Exception as e:
        logger.debug("proxy {}:{} failed: {}", host, port, e)
        return False


async def test_all(sf: async_sessionmaker[AsyncSession], concurrency: int = 10) -> tuple[int, int]:
    proxies = await repo.list_active_proxies(sf)
    if not proxies:
        logger.warning("no active proxies in DB")
        return 0, 0

    sem = asyncio.Semaphore(concurrency)

    async def _check(px) -> tuple[int, bool]:
        async with sem:
            d = {
                "scheme": px.scheme,
                "host": px.host,
                "port": px.port,
                "username": px.username,
                "password": px.password,
            }
            ok = await _ping_proxy(d)
            return px.id, ok

    results = await asyncio.gather(*[_check(p) for p in proxies])
    ok_count = sum(1 for _, ok in results if ok)
    bad_count = len(results) - ok_count
    for pid, ok in results:
        if not ok:
            await repo.mark_proxy_fail(sf, pid)
    logger.info("proxy test: {} ok, {} bad (total {})", ok_count, bad_count, len(results))
    return ok_count, bad_count


# ---------------- assignment ----------------


async def assign_round_robin(sf: async_sessionmaker[AsyncSession]) -> int:
    """Give one proxy to each account without proxy. Returns assigned count."""
    async with sf() as s:
        res = await s.execute(
            select(TelegramAccount).where(TelegramAccount.proxy.is_(None))
        )
        accounts_no_proxy = res.scalars().all()

    free_proxies = [p for p in await repo.list_active_proxies(sf) if p.assigned_account_id is None]
    if not free_proxies:
        logger.warning("no free proxies to assign")
        return 0

    assigned = 0
    for acc, px in zip(accounts_no_proxy, free_proxies):
        pdict = {
            "scheme": px.scheme,
            "hostname": px.host,
            "port": px.port,
            "username": px.username,
            "password": px.password,
        }
        await repo.assign_proxy_to_account(sf, px.id, acc.id, pdict)
        assigned += 1

    leftover = len(accounts_no_proxy) - assigned
    if leftover > 0:
        logger.warning(
            "{} accounts have no proxy — consider adding more or they will be disabled",
            leftover,
        )
    logger.info("assigned {} proxies to accounts", assigned)
    return assigned
