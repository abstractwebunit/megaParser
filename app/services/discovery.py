"""Discovery service: seed groups, global keyword search, chain-walk resolution."""
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.account_manager import Account, AccountManager
from app.db import repo
from app.settings import Settings


async def resolve_username(
    account: Account,
    username: str,
    sf: async_sessionmaker[AsyncSession],
    depth: int = 0,
    source_tg_id: int | None = None,
) -> int | None:
    uname = username.lstrip("@").strip()
    if len(uname) < 4 or uname.lower() in set(
        s.lower() for s in ["joinchat", "addlist", "proxy", "socks", "c", "s", "iv", "share"]
    ):
        return None
    try:
        await account.limiter.throttle("group")
        chat = await account.client.get_chat(uname)
    except Exception as e:
        logger.debug("resolve {} failed: {}", uname, e)
        return None

    type_name = ""
    if hasattr(getattr(chat, "type", None), "name"):
        type_name = chat.type.name.lower()

    row = {
        "tg_id": int(chat.id),
        "username": getattr(chat, "username", uname),
        "title": (getattr(chat, "title", "") or "")[:255],
        "type": type_name,
        "members_count": getattr(chat, "members_count", 0) or 0,
        "description": (getattr(chat, "description", "") or "")[:2000],
        "discovered_via": "chain" if source_tg_id else "seed",
        "depth": depth,
    }
    gid = await repo.upsert_target_group(sf, row)
    return gid


async def seed_phase(
    account: Account, sf: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    added = 0
    for sg in settings.yaml_cfg.discovery.seed_groups:
        gid = await resolve_username(account, sg, sf, depth=0)
        if gid:
            added += 1
    logger.info("seed phase: added/updated {} groups", added)
    return added


async def keywords_phase(
    accounts: AccountManager, sf: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    keywords = [k.strip() for k in settings.yaml_cfg.discovery.keywords if k.strip()]
    if not keywords:
        return 0

    found_total = 0
    for kw in keywords:
        acc = await accounts.get_available("discovery")
        if acc is None:
            acc = await accounts.get_available("scanner")
        if acc is None or not acc.db_model.can_search:
            logger.warning("no can_search account for keyword '{}'", kw)
            continue
        try:
            await acc.limiter.throttle("search")
            results = []
            async for chat in acc.client.search_global(kw, limit=100):
                results.append(chat)
        except Exception as e:
            logger.warning("search_global '{}' failed on {}: {}", kw, acc.name, e)
            continue

        for chat in results:
            uname = getattr(chat, "username", None)
            if not uname:
                continue
            row = {
                "tg_id": int(getattr(chat, "id", 0)) or None,
                "username": uname,
                "title": (getattr(chat, "title", "") or "")[:255],
                "type": "",
                "discovered_via": "keyword",
                "depth": 0,
            }
            await repo.upsert_target_group(sf, row)
            found_total += 1

        acc.limiter.bump("searches_today")

    logger.info("keywords phase: {} candidates added", found_total)
    return found_total


async def chain_walk_phase(
    accounts: AccountManager, sf: async_sessionmaker[AsyncSession], settings: Settings
) -> int:
    cfg = settings.yaml_cfg.discovery
    batch_size = 20
    processed = 0

    links = await repo.fetch_unresolved_links_locked(sf, cfg.max_resolve_per_cycle)
    if not links:
        return 0

    logger.info("chain-walk: resolving {} links", len(links))

    for link in links:
        if link.depth >= cfg.max_depth:
            continue
        acc = await accounts.get_available("discovery") or await accounts.get_available("scanner")
        if acc is None:
            logger.warning("no available account for chain-walk, stopping")
            break
        await resolve_username(
            acc,
            link.target_username,
            sf,
            depth=link.depth + 1,
            source_tg_id=link.source_group_tg_id,
        )
        processed += 1

    logger.info("chain-walk: processed {}", processed)
    return processed
