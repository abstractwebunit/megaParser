"""Parse group/channel participants.

For small groups (<threshold) use filter=RECENT.
For big groups (>threshold) use the alphabet-query trick: query=a, b, ... z, 0..9
to circumvent Telegram's ~200-random-members cap.
"""
from datetime import datetime, timezone
from typing import AsyncIterator

from loguru import logger
from pyrogram.enums import ChatMembersFilter
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.account_manager import Account
from app.db import repo
from app.db.models import TargetGroup
from app.settings import Settings

_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


async def _iter_members(client, chat_id, threshold: int) -> AsyncIterator:
    chat = await client.get_chat(chat_id)
    total = getattr(chat, "members_count", 0) or 0

    if total and total < threshold:
        async for m in client.get_chat_members(chat_id, filter=ChatMembersFilter.RECENT):
            yield m
        return

    seen: set[int] = set()
    for ch in _ALPHABET:
        try:
            async for m in client.get_chat_members(chat_id, query=ch):
                uid = getattr(m.user, "id", None) if hasattr(m, "user") else None
                if uid and uid not in seen:
                    seen.add(uid)
                    yield m
        except Exception as e:
            logger.debug("alphabet query {} failed: {}", ch, e)
            continue


async def parse_members(
    account: Account,
    group: TargetGroup,
    sf: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> dict:
    cfg = settings.yaml_cfg.members
    chat_ref = group.username or group.tg_id
    if chat_ref is None:
        raise ValueError(f"group {group.id} has neither tg_id nor username")

    chat = await account.client.get_chat(chat_ref)
    group_tg_id = chat.id
    logger.info("parse members for {} (id={})", getattr(chat, "title", ""), group_tg_id)

    users_batch: list[dict] = []
    memberships_batch: list[dict] = []
    total = 0
    cap = cfg.max_members_per_group

    async for member in _iter_members(account.client, group_tg_id, cfg.alphabet_trick_threshold):
        u = getattr(member, "user", None) or member
        uid = getattr(u, "id", None)
        if not uid:
            continue

        users_batch.append(
            {
                "tg_id": int(uid),
                "username": getattr(u, "username", None),
                "first_name": getattr(u, "first_name", None),
                "last_name": getattr(u, "last_name", None),
                "is_bot": bool(getattr(u, "is_bot", False)),
                "last_seen_at": datetime.now(timezone.utc),
            }
        )

        role = "member"
        status = getattr(member, "status", None)
        if status is not None:
            sname = getattr(status, "name", "").lower()
            if "owner" in sname:
                role = "creator"
            elif "admin" in sname:
                role = "admin"

        memberships_batch.append(
            {
                "group_tg_id": group_tg_id,
                "user_tg_id": int(uid),
                "role": role,
            }
        )

        total += 1
        if total >= cap:
            break

        if len(users_batch) >= 100:
            await repo.bulk_upsert_users(sf, users_batch)
            await repo.bulk_insert_memberships(sf, memberships_batch)
            account.limiter.bump("members_today", len(users_batch))
            users_batch.clear()
            memberships_batch.clear()

    if users_batch:
        await repo.bulk_upsert_users(sf, users_batch)
        await repo.bulk_insert_memberships(sf, memberships_batch)
        account.limiter.bump("members_today", len(users_batch))

    account.limiter.bump("groups_today")

    return {"group_tg_id": group_tg_id, "members": total}
