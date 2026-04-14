"""Realtime message monitoring via Kurigram on_message handlers.

Dedicated monitor pool (min 2 accounts for failover). Listens to TargetGroup.monitor_enabled=True.
"""
import asyncio
from datetime import datetime, timezone

from loguru import logger
from pyrogram import filters
from pyrogram.handlers import MessageHandler
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.account_manager import Account, AccountManager
from app.db import repo
from app.db.models import TargetGroup
from app.settings import Settings


class MonitorService:
    def __init__(
        self,
        accounts: AccountManager,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
    ) -> None:
        self.accounts = accounts
        self.sf = sf
        self.settings = settings
        self._stop = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        self._active: Account | None = None
        self._active_ids: set[int] = set()
        self._handler = None

    async def start(self) -> None:
        if not self.settings.yaml_cfg.monitor.enabled:
            logger.info("monitor disabled in config")
            return

        await self._rebuild_primary()
        rebuild_task = asyncio.create_task(self._rebuild_loop())
        self._tasks.add(rebuild_task)
        rebuild_task.add_done_callback(self._tasks.discard)

        await self._stop.wait()

    async def stop(self) -> None:
        self._stop.set()
        for t in list(self._tasks):
            t.cancel()
        if self._active and self._handler:
            try:
                self._active.client.remove_handler(self._handler)
            except Exception:
                pass

    async def _rebuild_loop(self) -> None:
        interval = self.settings.yaml_cfg.monitor.rebuild_filter_seconds
        while not self._stop.is_set():
            try:
                await asyncio.sleep(interval)
                await self._rebuild_primary()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("monitor rebuild error: {}", e)

    async def _load_monitored_ids(self) -> set[int]:
        async with self.sf() as s:
            res = await s.execute(
                select(TargetGroup.tg_id).where(
                    TargetGroup.monitor_enabled.is_(True),
                    TargetGroup.tg_id.is_not(None),
                )
            )
            return {int(x) for x in res.scalars().all() if x is not None}

    async def _pick_monitor_account(self) -> Account | None:
        candidates = [
            a
            for a in self.accounts.all
            if a.role == "monitor"
            and not a.limiter.is_banned()
            and a.connected
        ]
        if not candidates:
            candidates = [
                a
                for a in self.accounts.all
                if not a.limiter.is_banned() and a.connected
            ]
        if not candidates:
            return None
        return candidates[0]

    async def _rebuild_primary(self) -> None:
        new_ids = await self._load_monitored_ids()
        if new_ids == self._active_ids and self._active is not None and self._active.connected:
            return

        acc = await self._pick_monitor_account()
        if acc is None:
            logger.warning("no account available for monitoring")
            return

        if self._active and self._handler:
            try:
                self._active.client.remove_handler(self._handler)
            except Exception:
                pass
            self._handler = None

        if not new_ids:
            self._active = None
            self._active_ids = set()
            return

        flt = filters.chat(list(new_ids))

        async def handler(client, message):
            try:
                await self._on_message(message)
            except Exception as e:
                logger.exception("monitor handler error: {}", e)

        self._handler = MessageHandler(handler, flt)
        acc.client.add_handler(self._handler)
        self._active = acc
        self._active_ids = new_ids
        logger.info("monitor active: account={}, groups={}", acc.name, len(new_ids))

    async def _on_message(self, message) -> None:
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        sender = getattr(message, "from_user", None) or getattr(message, "sender_chat", None)
        sender_id = getattr(sender, "id", None) if sender else None
        sender_username = getattr(sender, "username", None) if sender else None
        fn = getattr(sender, "first_name", "") if sender else ""
        ln = getattr(sender, "last_name", "") if sender else ""
        sender_name = (f"{fn} {ln}".strip()) or None

        msg_date = message.date
        if isinstance(msg_date, datetime) and msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)

        await repo.bulk_insert_messages(
            self.sf,
            [
                {
                    "group_tg_id": int(message.chat.id),
                    "message_id": int(message.id),
                    "sender_id": sender_id,
                    "sender_username": sender_username,
                    "sender_name": sender_name,
                    "text": text[:10000],
                    "date": msg_date,
                    "has_links": "t.me/" in text.lower() if text else False,
                    "matched_keywords": None,
                    "source": "realtime",
                }
            ],
        )
