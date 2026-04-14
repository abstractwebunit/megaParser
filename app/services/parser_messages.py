"""Incremental message history parser.

Reads Kurigram get_chat_history in batches, persists resume cursor after each batch,
extracts t.me/ links into DiscoveredLink, tags matched keywords.
"""
import re
from datetime import datetime, timezone
from typing import Iterable

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.account_manager import Account
from app.db import repo
from app.db.models import TargetGroup
from app.settings import Settings

_LINK_RE = re.compile(r"t\.me/([a-zA-Z0-9_]{3,})", re.IGNORECASE)
_SKIP_LINK = {"joinchat", "addlist", "proxy", "socks", "c", "s", "iv", "share"}


def _extract_links(text: str) -> list[str]:
    if not text:
        return []
    found = set()
    for m in _LINK_RE.finditer(text):
        name = m.group(1).lower()
        if name in _SKIP_LINK:
            continue
        found.add(m.group(1))
    return list(found)


def _match_keywords(text: str, keywords: Iterable[str]) -> list[str]:
    if not text:
        return []
    lo = text.lower()
    return [kw for kw in keywords if kw.lower() in lo]


async def parse_history(
    account: Account,
    group: TargetGroup,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> dict:
    scan_cfg = settings.yaml_cfg.scanner
    keywords = [k.strip() for k in settings.yaml_cfg.discovery.keywords if k.strip()]
    batch_size = scan_cfg.messages_batch_size
    total_limit = scan_cfg.messages_per_group

    chat_ref = group.username or group.tg_id
    if chat_ref is None:
        raise ValueError(f"group {group.id} has neither tg_id nor username")

    logger.info("scan messages for {} (limit={})", chat_ref, total_limit)
    chat = await account.client.get_chat(chat_ref)
    group_tg_id = chat.id
    if group.tg_id != group_tg_id:
        from sqlalchemy import update as sa_update

        from app.db.models import TargetGroup

        type_name = ""
        t = getattr(chat, "type", None)
        if t is not None and hasattr(t, "name"):
            type_name = t.name.lower()
        async with sf() as s, s.begin():
            await s.execute(
                sa_update(TargetGroup)
                .where(TargetGroup.id == group.id)
                .values(
                    tg_id=group_tg_id,
                    username=getattr(chat, "username", None),
                    title=(getattr(chat, "title", "") or "")[:255],
                    type=type_name,
                    members_count=getattr(chat, "members_count", 0) or 0,
                    description=(getattr(chat, "description", "") or "")[:2000],
                    scan_status="scanning",
                )
            )

    batch: list[dict] = []
    links: set[str] = set()
    total = 0
    last_msg_id: int | None = group.last_scanned_msg_id
    offset_id = group.last_scanned_msg_id or 0

    async for msg in account.client.get_chat_history(group_tg_id, limit=total_limit):
        if total >= total_limit:
            break
        total += 1

        text = getattr(msg, "text", None) or getattr(msg, "caption", None) or ""
        sender_id = None
        sender_username = None
        sender_name = None
        sender = getattr(msg, "from_user", None) or getattr(msg, "sender_chat", None)
        if sender is not None:
            sender_id = getattr(sender, "id", None)
            sender_username = getattr(sender, "username", None)
            fn = getattr(sender, "first_name", "") or getattr(sender, "title", "") or ""
            ln = getattr(sender, "last_name", "") or ""
            sender_name = (f"{fn} {ln}".strip()) or None

        msg_links = _extract_links(text)
        if msg_links:
            links.update(msg_links)

        matched = _match_keywords(text, keywords) if keywords else []

        msg_date = msg.date
        if isinstance(msg_date, datetime) and msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)

        batch.append(
            {
                "group_tg_id": group_tg_id,
                "message_id": int(msg.id),
                "sender_id": sender_id,
                "sender_username": sender_username,
                "sender_name": sender_name,
                "text": text[:10000],
                "date": msg_date,
                "has_links": bool(msg_links),
                "matched_keywords": matched or None,
                "source": "history",
            }
        )
        last_msg_id = max(last_msg_id or 0, int(msg.id))

        if len(batch) >= batch_size:
            await repo.bulk_insert_messages(sf, batch)
            await repo.update_account_state(
                sf,
                account.id,
                {"messages_today": account.limiter.rt.messages_today + len(batch)},
            )
            account.limiter.bump("messages_today", len(batch))
            from sqlalchemy import update as sa_update

            from app.db.models import TargetGroup

            async with sf() as s, s.begin():
                await s.execute(
                    sa_update(TargetGroup)
                    .where(TargetGroup.id == group.id)
                    .values(last_scanned_msg_id=last_msg_id)
                )
            batch.clear()
            await account.limiter.throttle("messages_batch")

    if batch:
        await repo.bulk_insert_messages(sf, batch)
        account.limiter.bump("messages_today", len(batch))

    if links:
        rows = [
            {
                "source_group_tg_id": group_tg_id,
                "target_username": u,
                "depth": group.depth + 1,
            }
            for u in links
        ]
        await repo.bulk_insert_links(sf, rows)

    account.limiter.bump("groups_today")

    return {
        "group_tg_id": group_tg_id,
        "messages": total,
        "links": len(links),
        "last_msg_id": last_msg_id,
    }
