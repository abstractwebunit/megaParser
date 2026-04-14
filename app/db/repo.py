"""Thin repository functions. Short-lived AsyncSession per call."""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import (
    AccountState,
    DiscoveredLink,
    GroupMembership,
    Keyword,
    ParsedMessage,
    ParsedUser,
    ParserTask,
    Proxy,
    TargetGroup,
    TelegramAccount,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def today() -> date:
    return utcnow().date()


# ---------------- accounts ----------------


async def get_enabled_accounts(sf: async_sessionmaker[AsyncSession]) -> Sequence[TelegramAccount]:
    async with sf() as s:
        res = await s.execute(
            select(TelegramAccount).where(TelegramAccount.enabled.is_(True))
        )
        return res.scalars().all()


async def get_account_by_id(
    sf: async_sessionmaker[AsyncSession], account_id: int
) -> TelegramAccount | None:
    async with sf() as s:
        res = await s.execute(
            select(TelegramAccount).where(TelegramAccount.id == account_id)
        )
        return res.scalar_one_or_none()


async def get_account_by_name(
    sf: async_sessionmaker[AsyncSession], name: str
) -> TelegramAccount | None:
    async with sf() as s:
        res = await s.execute(
            select(TelegramAccount).where(TelegramAccount.name == name)
        )
        return res.scalar_one_or_none()


async def upsert_account(
    sf: async_sessionmaker[AsyncSession], data: dict[str, Any]
) -> int:
    async with sf() as s, s.begin():
        stmt = (
            pg_insert(TelegramAccount)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    k: v
                    for k, v in data.items()
                    if k not in ("name", "created_at", "id")
                },
            )
            .returning(TelegramAccount.id)
        )
        res = await s.execute(stmt)
        return int(res.scalar_one())


async def ensure_account_state(sf: async_sessionmaker[AsyncSession], account_id: int) -> None:
    async with sf() as s, s.begin():
        stmt = (
            pg_insert(AccountState)
            .values(account_id=account_id, day_date=today())
            .on_conflict_do_nothing(index_elements=["account_id"])
        )
        await s.execute(stmt)


async def get_account_state(
    sf: async_sessionmaker[AsyncSession], account_id: int
) -> AccountState | None:
    async with sf() as s:
        res = await s.execute(
            select(AccountState).where(AccountState.account_id == account_id)
        )
        return res.scalar_one_or_none()


async def update_account_state(
    sf: async_sessionmaker[AsyncSession], account_id: int, fields: dict[str, Any]
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(AccountState).where(AccountState.account_id == account_id).values(**fields)
        )


async def set_account_enabled(
    sf: async_sessionmaker[AsyncSession], account_id: int, enabled: bool
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(TelegramAccount)
            .where(TelegramAccount.id == account_id)
            .values(enabled=enabled)
        )


async def mark_account_banned(
    sf: async_sessionmaker[AsyncSession],
    account_id: int,
    seconds: int,
    reason: str,
) -> None:
    now = utcnow()
    until = now + timedelta(seconds=seconds)
    async with sf() as s, s.begin():
        await s.execute(
            update(AccountState)
            .where(AccountState.account_id == account_id)
            .values(
                status="banned",
                banned_at=now,
                ban_until=until,
                ban_seconds=seconds,
                ban_reason=reason,
                total_bans=AccountState.total_bans + 1,
            )
        )


async def mark_account_dead(
    sf: async_sessionmaker[AsyncSession], account_id: int, reason: str
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(AccountState)
            .where(AccountState.account_id == account_id)
            .values(status="dead", ban_reason=reason, banned_at=utcnow())
        )
        await s.execute(
            update(TelegramAccount)
            .where(TelegramAccount.id == account_id)
            .values(enabled=False)
        )


async def expire_bans(sf: async_sessionmaker[AsyncSession]) -> list[int]:
    """Move banned→idle for accounts whose ban_until has passed. Return affected IDs."""
    now = utcnow()
    async with sf() as s, s.begin():
        res = await s.execute(
            select(AccountState.account_id).where(
                and_(
                    AccountState.status == "banned",
                    AccountState.ban_until.is_not(None),
                    AccountState.ban_until < now,
                )
            )
        )
        ids = [int(r) for r in res.scalars().all()]
        if ids:
            await s.execute(
                update(AccountState)
                .where(AccountState.account_id.in_(ids))
                .values(status="idle", ban_until=None)
            )
        return ids


async def reset_daily_counters(sf: async_sessionmaker[AsyncSession]) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(AccountState)
            .where(AccountState.day_date < today())
            .values(
                day_date=today(),
                groups_today=0,
                members_today=0,
                messages_today=0,
                profiles_today=0,
                searches_today=0,
                floods_hour=0,
            )
        )


# ---------------- proxies ----------------


async def upsert_proxy(
    sf: async_sessionmaker[AsyncSession], data: dict[str, Any]
) -> int:
    async with sf() as s, s.begin():
        stmt = (
            pg_insert(Proxy)
            .values(**data)
            .on_conflict_do_update(
                index_elements=["host", "port", "username"],
                set_={"active": True},
            )
            .returning(Proxy.id)
        )
        res = await s.execute(stmt)
        return int(res.scalar_one())


async def list_active_proxies(sf: async_sessionmaker[AsyncSession]) -> Sequence[Proxy]:
    async with sf() as s:
        res = await s.execute(select(Proxy).where(Proxy.active.is_(True)).order_by(Proxy.id))
        return res.scalars().all()


async def assign_proxy_to_account(
    sf: async_sessionmaker[AsyncSession], proxy_id: int, account_id: int, proxy_json: dict
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(Proxy).where(Proxy.id == proxy_id).values(assigned_account_id=account_id)
        )
        await s.execute(
            update(TelegramAccount)
            .where(TelegramAccount.id == account_id)
            .values(proxy=proxy_json)
        )


async def mark_proxy_fail(sf: async_sessionmaker[AsyncSession], proxy_id: int) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(Proxy)
            .where(Proxy.id == proxy_id)
            .values(
                fails_count=Proxy.fails_count + 1,
                last_checked_at=utcnow(),
            )
        )
        await s.execute(
            update(Proxy).where(and_(Proxy.id == proxy_id, Proxy.fails_count >= 3)).values(active=False)
        )


# ---------------- groups ----------------


async def upsert_target_group(
    sf: async_sessionmaker[AsyncSession], data: dict[str, Any]
) -> int:
    async with sf() as s, s.begin():
        ins = pg_insert(TargetGroup).values(**data)
        if "tg_id" in data and data["tg_id"] is not None:
            stmt = ins.on_conflict_do_update(
                index_elements=["tg_id"],
                set_={k: v for k, v in data.items() if k not in ("id", "discovered_at", "tg_id")},
            ).returning(TargetGroup.id)
        else:
            stmt = ins.on_conflict_do_nothing().returning(TargetGroup.id)
        res = await s.execute(stmt)
        rid = res.scalar_one_or_none()
        if rid is None and "username" in data and data["username"]:
            res2 = await s.execute(
                select(TargetGroup.id).where(TargetGroup.username == data["username"])
            )
            rid = res2.scalar_one_or_none()
        return int(rid) if rid is not None else 0


async def get_pending_groups(
    sf: async_sessionmaker[AsyncSession], limit: int = 50, cooldown_hours: int = 24
) -> Sequence[TargetGroup]:
    cutoff = utcnow() - timedelta(hours=cooldown_hours)
    async with sf() as s:
        res = await s.execute(
            select(TargetGroup)
            .where(
                and_(
                    TargetGroup.scan_status == "pending",
                    or_(
                        TargetGroup.cooldown_until.is_(None),
                        TargetGroup.cooldown_until < utcnow(),
                    ),
                    or_(
                        TargetGroup.last_scanned_at.is_(None),
                        TargetGroup.last_scanned_at < cutoff,
                    ),
                )
            )
            .order_by(TargetGroup.depth, TargetGroup.id)
            .limit(limit)
        )
        return res.scalars().all()


async def mark_group_scanning(
    sf: async_sessionmaker[AsyncSession], group_id: int
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(TargetGroup).where(TargetGroup.id == group_id).values(scan_status="scanning")
        )


async def mark_group_scanned(
    sf: async_sessionmaker[AsyncSession],
    group_id: int,
    last_msg_id: int | None,
    cooldown_hours: int = 24,
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(TargetGroup)
            .where(TargetGroup.id == group_id)
            .values(
                scan_status="scanned",
                last_scanned_at=utcnow(),
                last_scanned_msg_id=last_msg_id,
                cooldown_until=utcnow() + timedelta(hours=cooldown_hours),
            )
        )


async def mark_group_private(
    sf: async_sessionmaker[AsyncSession], group_id: int, error: str = ""
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(TargetGroup)
            .where(TargetGroup.id == group_id)
            .values(scan_status="private", error=error[:250])
        )


async def mark_group_error(
    sf: async_sessionmaker[AsyncSession], group_id: int, error: str
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(TargetGroup)
            .where(TargetGroup.id == group_id)
            .values(scan_status="error", error=error[:250])
        )


# ---------------- messages / users / memberships ----------------


async def bulk_insert_messages(
    sf: async_sessionmaker[AsyncSession], rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    async with sf() as s, s.begin():
        stmt = pg_insert(ParsedMessage).values(rows).on_conflict_do_nothing(
            index_elements=["group_tg_id", "message_id"]
        )
        res = await s.execute(stmt)
        return res.rowcount or 0


async def upsert_user(
    sf: async_sessionmaker[AsyncSession], data: dict[str, Any]
) -> None:
    async with sf() as s, s.begin():
        stmt = pg_insert(ParsedUser).values(**data).on_conflict_do_update(
            index_elements=["tg_id"],
            set_={k: v for k, v in data.items() if k not in ("tg_id",)},
        )
        await s.execute(stmt)


async def bulk_upsert_users(
    sf: async_sessionmaker[AsyncSession], rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    async with sf() as s, s.begin():
        stmt = pg_insert(ParsedUser).values(rows).on_conflict_do_nothing(
            index_elements=["tg_id"]
        )
        res = await s.execute(stmt)
        return res.rowcount or 0


async def bulk_insert_memberships(
    sf: async_sessionmaker[AsyncSession], rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    async with sf() as s, s.begin():
        stmt = pg_insert(GroupMembership).values(rows).on_conflict_do_nothing(
            index_elements=["group_tg_id", "user_tg_id"]
        )
        res = await s.execute(stmt)
        return res.rowcount or 0


# ---------------- discovered links ----------------


async def bulk_insert_links(
    sf: async_sessionmaker[AsyncSession], rows: list[dict[str, Any]]
) -> int:
    if not rows:
        return 0
    async with sf() as s, s.begin():
        stmt = pg_insert(DiscoveredLink).values(rows).on_conflict_do_nothing(
            index_elements=["target_username"]
        )
        res = await s.execute(stmt)
        return res.rowcount or 0


async def fetch_unresolved_links_locked(
    sf: async_sessionmaker[AsyncSession], limit: int
) -> Sequence[DiscoveredLink]:
    async with sf() as s, s.begin():
        res = await s.execute(
            select(DiscoveredLink)
            .where(DiscoveredLink.resolved.is_(False))
            .order_by(DiscoveredLink.depth, DiscoveredLink.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = res.scalars().all()
        if rows:
            ids = [r.id for r in rows]
            await s.execute(
                update(DiscoveredLink)
                .where(DiscoveredLink.id.in_(ids))
                .values(resolved=True, resolved_at=utcnow())
            )
        return rows


# ---------------- parser tasks ----------------


async def create_task(
    sf: async_sessionmaker[AsyncSession],
    task_type: str,
    target: str | None = None,
    payload: dict | None = None,
    priority: int = 0,
) -> int:
    async with sf() as s, s.begin():
        stmt = (
            pg_insert(ParserTask)
            .values(
                task_type=task_type,
                target=target,
                payload=payload,
                priority=priority,
                status="pending",
            )
            .returning(ParserTask.id)
        )
        res = await s.execute(stmt)
        return int(res.scalar_one())


async def fetch_pending_tasks(
    sf: async_sessionmaker[AsyncSession], limit: int
) -> Sequence[ParserTask]:
    async with sf() as s, s.begin():
        res = await s.execute(
            select(ParserTask)
            .where(ParserTask.status == "pending")
            .order_by(ParserTask.priority.desc(), ParserTask.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        rows = res.scalars().all()
        if rows:
            ids = [r.id for r in rows]
            await s.execute(
                update(ParserTask)
                .where(ParserTask.id.in_(ids))
                .values(status="running", started_at=utcnow())
            )
        return rows


async def mark_task_done(
    sf: async_sessionmaker[AsyncSession], task_id: int, result: dict | None = None
) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(ParserTask)
            .where(ParserTask.id == task_id)
            .values(status="done", finished_at=utcnow(), result=result)
        )


async def mark_task_error(
    sf: async_sessionmaker[AsyncSession], task_id: int, error: str, retry: bool = False
) -> None:
    async with sf() as s, s.begin():
        if retry:
            await s.execute(
                update(ParserTask)
                .where(ParserTask.id == task_id)
                .values(
                    status="pending",
                    started_at=None,
                    retry_count=ParserTask.retry_count + 1,
                    error=error[:1000],
                )
            )
        else:
            await s.execute(
                update(ParserTask)
                .where(ParserTask.id == task_id)
                .values(status="error", finished_at=utcnow(), error=error[:1000])
            )


async def requeue_task(sf: async_sessionmaker[AsyncSession], task_id: int) -> None:
    async with sf() as s, s.begin():
        await s.execute(
            update(ParserTask)
            .where(ParserTask.id == task_id)
            .values(status="pending", started_at=None)
        )


async def recover_stale_tasks(
    sf: async_sessionmaker[AsyncSession], stale_minutes: int = 10
) -> int:
    cutoff = utcnow() - timedelta(minutes=stale_minutes)
    async with sf() as s, s.begin():
        res = await s.execute(
            update(ParserTask)
            .where(
                and_(
                    ParserTask.status == "running",
                    ParserTask.started_at.is_not(None),
                    ParserTask.started_at < cutoff,
                )
            )
            .values(status="pending", started_at=None)
        )
        return res.rowcount or 0


# ---------------- stats ----------------


async def stats_24h(sf: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    cutoff = utcnow() - timedelta(hours=24)
    async with sf() as s:
        msgs = (
            await s.execute(
                select(func.count(ParsedMessage.id)).where(ParsedMessage.date >= cutoff)
            )
        ).scalar_one()
        users = (await s.execute(select(func.count(ParsedUser.id)))).scalar_one()
        groups = (await s.execute(select(func.count(TargetGroup.id)))).scalar_one()
        pending_tasks = (
            await s.execute(
                select(func.count(ParserTask.id)).where(ParserTask.status == "pending")
            )
        ).scalar_one()
        return {
            "messages_24h": int(msgs or 0),
            "users_total": int(users or 0),
            "groups_total": int(groups or 0),
            "pending_tasks": int(pending_tasks or 0),
        }
