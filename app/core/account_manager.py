"""AccountManager — load pool, rotate, track ban state.

Adapted from tg-harvester/accounts.py.
"""
import asyncio
import random
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Literal

from loguru import logger
from pyrogram import Client
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.client_factory import build_client
from app.core.rate_limiter import AccountRuntime, RateLimiter
from app.db import repo
from app.db.models import TelegramAccount
from app.settings import Settings

Role = Literal["scanner", "discovery", "monitor"]


@dataclass
class Account:
    id: int
    name: str
    role: Role
    client: Client
    limiter: RateLimiter
    db_model: TelegramAccount
    connected: bool = False
    failures: int = 0


class AccountManager:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.sf = session_factory
        self.settings = settings
        self._accounts: dict[int, Account] = {}
        self._rotation_ix = 0
        self._connect_lock = asyncio.Lock()

    @property
    def all(self) -> list[Account]:
        return list(self._accounts.values())

    def by_id(self, account_id: int) -> Account | None:
        return self._accounts.get(account_id)

    async def load_all(self, max_concurrent: int) -> None:
        await repo.reset_daily_counters(self.sf)
        await repo.expire_bans(self.sf)

        models = await repo.get_enabled_accounts(self.sf)
        logger.info("loading {} enabled accounts (max_concurrent={})", len(models), max_concurrent)

        for m in models:
            await repo.ensure_account_state(self.sf, m.id)
            state = await repo.get_account_state(self.sf, m.id)
            premium_mult = 1.5 if m.is_premium else 1.0

            rt = AccountRuntime(
                account_id=m.id,
                premium_multiplier=premium_mult,
                groups_today=state.groups_today if state else 0,
                messages_today=state.messages_today if state else 0,
                profiles_today=state.profiles_today if state else 0,
                searches_today=state.searches_today if state else 0,
                floods_hour=state.floods_hour if state else 0,
                banned_until=state.ban_until if state else None,
                ban_reason=state.ban_reason if state else "",
            )

            limiter = RateLimiter(self.settings.yaml_cfg.rate_limits, rt)
            role: Role = (m.role or "scanner").strip().lower()  # type: ignore[assignment]
            if role not in ("scanner", "discovery", "monitor"):
                role = "scanner"

            client = build_client(
                m,
                workdir=self.settings.data_dir,
                no_updates=(role != "monitor"),
            )

            self._accounts[m.id] = Account(
                id=m.id, name=m.name, role=role, client=client, limiter=limiter, db_model=m
            )

        active_ids = [a.id for a in self._accounts.values()][:max_concurrent]
        for acc_id in active_ids:
            await self._connect_one(self._accounts[acc_id])
            await asyncio.sleep(random.uniform(2, 5))

        logger.info(
            "pool ready: {} total, {} connected",
            len(self._accounts),
            sum(1 for a in self._accounts.values() if a.connected),
        )

    async def _connect_one(self, acc: Account) -> bool:
        async with self._connect_lock:
            if acc.connected:
                return True
            try:
                await acc.client.start()
                me = await acc.client.get_me()
                acc.connected = True
                logger.info("connected account {} (id={}, @{})", acc.name, me.id, me.username)
                await repo.update_account_state(
                    self.sf, acc.id, {"status": "idle", "last_active_at": _now()}
                )
                return True
            except Exception as e:
                acc.failures += 1
                logger.exception("connect failed for account {}: {}", acc.name, e)
                await repo.update_account_state(
                    self.sf, acc.id, {"status": "disconnected", "ban_reason": f"connect: {e!s}"[:60]}
                )
                return False

    async def disconnect_all(self) -> None:
        for acc in self._accounts.values():
            if acc.connected:
                try:
                    await acc.client.stop()
                except Exception as e:
                    logger.warning("disconnect error for {}: {}", acc.name, e)
                acc.connected = False

    async def get_available(self, role: Role = "scanner") -> Account | None:
        candidates = [
            a
            for a in self._accounts.values()
            if a.role == role and not a.limiter.is_banned() and not a.limiter.is_resting()
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda a: (a.limiter.rt.last_action_at or _epoch()))
        for acc in candidates:
            ok, reason = acc.limiter.can_continue()
            if not ok:
                logger.debug("skip {} — {}", acc.name, reason)
                continue
            if not acc.connected:
                if not await self._connect_one(acc):
                    continue
            return acc
        return None

    async def mark_banned(self, acc: Account, seconds: int, reason: str) -> None:
        acc.limiter.mark_long_ban(seconds, reason)
        await repo.mark_account_banned(self.sf, acc.id, seconds, reason)
        logger.warning("account {} banned {}s: {}", acc.name, seconds, reason)

    async def mark_dead(self, acc: Account, reason: str) -> None:
        await repo.mark_account_dead(self.sf, acc.id, reason)
        if acc.connected:
            try:
                await acc.client.stop()
            except Exception:
                pass
            acc.connected = False
        logger.error("account {} DEAD: {}", acc.name, reason)

    async def check_recoveries(self) -> list[int]:
        recovered = await repo.expire_bans(self.sf)
        for acc_id in recovered:
            acc = self._accounts.get(acc_id)
            if acc is None:
                continue
            acc.limiter.rt.banned_until = None
            acc.limiter.rt.ban_reason = ""
            logger.info("account {} ban expired — back to idle", acc.name)
        return recovered

    async def persist_counters(self) -> None:
        for acc in self._accounts.values():
            rt = acc.limiter.rt
            await repo.update_account_state(
                self.sf,
                acc.id,
                {
                    "groups_today": rt.groups_today,
                    "members_today": rt.members_today,
                    "messages_today": rt.messages_today,
                    "profiles_today": rt.profiles_today,
                    "searches_today": rt.searches_today,
                    "floods_hour": rt.floods_hour,
                    "last_flood_at": rt.last_flood_at,
                    "last_active_at": rt.last_action_at,
                },
            )

    def stats(self) -> list[dict]:
        out = []
        for a in self._accounts.values():
            rt = a.limiter.rt
            out.append(
                {
                    "id": a.id,
                    "name": a.name,
                    "role": a.role,
                    "connected": a.connected,
                    "status": "banned"
                    if a.limiter.is_banned()
                    else "resting"
                    if a.limiter.is_resting()
                    else "idle",
                    "groups_today": rt.groups_today,
                    "profiles_today": rt.profiles_today,
                    "searches_today": rt.searches_today,
                    "floods_hour": rt.floods_hour,
                    "banned_until": rt.banned_until.isoformat() if rt.banned_until else None,
                    "ban_reason": rt.ban_reason,
                }
            )
        return out


def _now():
    from datetime import datetime

    return datetime.now(timezone.utc)


def _epoch():
    from datetime import datetime

    return datetime(1970, 1, 1, tzinfo=timezone.utc)
