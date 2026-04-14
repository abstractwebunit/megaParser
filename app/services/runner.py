"""Runner orchestrator.

- DB poller pulls ParserTask rows via SELECT FOR UPDATE SKIP LOCKED → in-memory queue
- Worker loop dispatches by task_type
- Recovery loop unmarks expired bans and resets stale tasks
- Bot control via ControlBus (stop_event)
"""
import asyncio
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.account_manager import AccountManager
from app.core.errors import ErrorKind, classify, flood_seconds
from app.db import repo
from app.db.models import ParserTask, TargetGroup
from app.services import discovery, parser_members, parser_messages
from app.settings import Settings


@dataclass
class ControlBus:
    run_event: asyncio.Event = field(default_factory=asyncio.Event)
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)


class Runner:
    def __init__(
        self,
        accounts: AccountManager,
        sf: async_sessionmaker[AsyncSession],
        settings: Settings,
        control: ControlBus,
    ) -> None:
        self.accounts = accounts
        self.sf = sf
        self.settings = settings
        self.control = control
        self._queue: asyncio.Queue[ParserTask] = asyncio.Queue(maxsize=500)
        self._tasks: set[asyncio.Task] = set()

    def _spawn(self, coro) -> asyncio.Task:
        t = asyncio.create_task(coro)
        self._tasks.add(t)
        t.add_done_callback(self._tasks.discard)
        return t

    async def start(self) -> None:
        await repo.recover_stale_tasks(
            self.sf, self.settings.yaml_cfg.runner.stale_task_timeout_minutes
        )
        self.control.run_event.set()

        self._spawn(self._db_poller_loop())
        self._spawn(self._recovery_loop())
        self._spawn(self._persist_loop())

        for i in range(self.settings.yaml_cfg.runner.worker_pool_size):
            self._spawn(self._worker_loop(i))

        await self.control.stop_event.wait()
        logger.info("runner stopping...")
        for t in list(self._tasks):
            t.cancel()

    async def _db_poller_loop(self) -> None:
        interval = self.settings.yaml_cfg.runner.db_poll_interval_seconds
        while not self.control.stop_event.is_set():
            try:
                if not self.control.run_event.is_set():
                    await asyncio.sleep(interval)
                    continue

                if self._queue.qsize() > 50:
                    await asyncio.sleep(interval)
                    continue

                tasks = await repo.fetch_pending_tasks(self.sf, limit=100)
                for t in tasks:
                    await self._queue.put(t)

                if not tasks:
                    await self._seed_tasks_from_groups()

                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("db_poller error: {}", e)
                await asyncio.sleep(interval)

    async def _seed_tasks_from_groups(self) -> None:
        """If no ParserTask pending, auto-create scan_messages tasks for pending groups
        and periodically kick chain-walk discovery to resolve t.me/ links."""
        groups = await repo.get_pending_groups(
            self.sf,
            limit=20,
            cooldown_hours=self.settings.yaml_cfg.rate_limits.cooldown_rescan_hours,
        )
        for g in groups:
            await repo.create_task(
                self.sf,
                task_type="scan_messages",
                target=str(g.username or g.tg_id),
                payload={"group_id": g.id},
                priority=max(0, 10 - g.depth),
            )
        if groups:
            logger.info("seeded {} scan_messages tasks", len(groups))

        # Auto-chain-walk: if there are unresolved t.me/ links and no pending/running
        # chain discovery task, create one. This keeps the discovery loop running on its own.
        from sqlalchemy import and_, or_, select

        from app.db.models import DiscoveredLink, ParserTask

        async with self.sf() as s:
            unresolved = (
                await s.execute(
                    select(DiscoveredLink.id).where(DiscoveredLink.resolved.is_(False)).limit(1)
                )
            ).first()
            if not unresolved:
                return
            already = (
                await s.execute(
                    select(ParserTask.id).where(
                        and_(
                            ParserTask.task_type == "discover",
                            or_(ParserTask.status == "pending", ParserTask.status == "running"),
                        )
                    ).limit(1)
                )
            ).first()
            if already:
                return
        await repo.create_task(
            self.sf, "discover", target="chain", payload={"kind": "chain"}, priority=3
        )
        logger.info("auto-created chain-walk discover task")

    async def _recovery_loop(self) -> None:
        interval = self.settings.yaml_cfg.runner.recovery_check_minutes * 60
        while not self.control.stop_event.is_set():
            try:
                await asyncio.sleep(interval)
                recovered = await self.accounts.check_recoveries()
                if recovered:
                    logger.info("recovered {} accounts", len(recovered))
                stale = await repo.recover_stale_tasks(
                    self.sf, self.settings.yaml_cfg.runner.stale_task_timeout_minutes
                )
                if stale:
                    logger.info("reset {} stale tasks", stale)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("recovery_loop error: {}", e)

    async def _persist_loop(self) -> None:
        while not self.control.stop_event.is_set():
            try:
                await asyncio.sleep(60)
                await self.accounts.persist_counters()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("persist_loop error: {}", e)

    async def _worker_loop(self, worker_id: int) -> None:
        logger.info("worker {} started", worker_id)
        while not self.control.stop_event.is_set():
            try:
                if not self.control.run_event.is_set():
                    await asyncio.sleep(5)
                    continue

                try:
                    task = await asyncio.wait_for(self._queue.get(), timeout=5)
                except asyncio.TimeoutError:
                    continue

                await self._dispatch(task, worker_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.exception("worker {} top-level error: {}", worker_id, e)
                await asyncio.sleep(2)
        logger.info("worker {} stopped", worker_id)

    async def _dispatch(self, task: ParserTask, worker_id: int) -> None:
        role = "discovery" if task.task_type in ("discover", "resolve_link") else "scanner"
        acc = await self.accounts.get_available(role)
        if acc is None:
            await repo.requeue_task(self.sf, task.id)
            await asyncio.sleep(30)
            return

        logger.info("worker {} task#{} {} via {}", worker_id, task.id, task.task_type, acc.name)

        try:
            if task.task_type == "scan_messages":
                group = await self._load_group(task)
                if group is None:
                    await repo.mark_task_error(self.sf, task.id, "group not found")
                    return
                await acc.limiter.throttle("group")
                result = await parser_messages.parse_history(acc, group, self.sf, self.settings)
                await repo.mark_group_scanned(
                    self.sf,
                    group.id,
                    result.get("last_msg_id"),
                    cooldown_hours=self.settings.yaml_cfg.rate_limits.cooldown_rescan_hours,
                )
                await repo.mark_task_done(self.sf, task.id, result)

            elif task.task_type == "scan_members":
                group = await self._load_group(task)
                if group is None:
                    await repo.mark_task_error(self.sf, task.id, "group not found")
                    return
                await acc.limiter.throttle("group")
                result = await parser_members.parse_members(acc, group, self.sf, self.settings)
                await repo.mark_task_done(self.sf, task.id, result)

            elif task.task_type == "discover":
                kind = (task.payload or {}).get("kind", "seed")
                if kind == "seed":
                    await discovery.seed_phase(acc, self.sf, self.settings)
                elif kind == "keyword":
                    await discovery.keywords_phase(self.accounts, self.sf, self.settings)
                elif kind == "chain":
                    await discovery.chain_walk_phase(self.accounts, self.sf, self.settings)
                await repo.mark_task_done(self.sf, task.id, {"kind": kind})

            elif task.task_type == "resolve_link":
                uname = task.target
                if uname:
                    await discovery.resolve_username(acc, uname, self.sf, depth=0)
                await repo.mark_task_done(self.sf, task.id)

            else:
                await repo.mark_task_error(self.sf, task.id, f"unknown task_type {task.task_type}")

        except Exception as e:
            await self._handle_exception(task, acc, e)

    async def _load_group(self, task: ParserTask) -> TargetGroup | None:
        gid = (task.payload or {}).get("group_id") if task.payload else None
        async with self.sf() as s:
            if gid:
                res = await s.execute(select(TargetGroup).where(TargetGroup.id == int(gid)))
                return res.scalar_one_or_none()
            if task.target:
                target = task.target.lstrip("@")
                res = await s.execute(
                    select(TargetGroup).where(TargetGroup.username == target)
                )
                g = res.scalar_one_or_none()
                if g:
                    return g
        return None

    async def _handle_exception(self, task: ParserTask, acc, exc: Exception) -> None:
        kind = classify(exc, self.settings.yaml_cfg.rate_limits.flood_long_threshold_seconds)
        logger.warning("task#{} on {} → {}: {}", task.id, acc.name, kind.name, exc)

        if kind == ErrorKind.FLOOD_SHORT:
            from pyrogram.errors import FloodWait

            if isinstance(exc, FloodWait):
                await acc.limiter.handle_flood(flood_seconds(exc))
            await repo.requeue_task(self.sf, task.id)

        elif kind == ErrorKind.FLOOD_LONG:
            from pyrogram.errors import FloodWait

            if isinstance(exc, FloodWait):
                secs = flood_seconds(exc)
                await self.accounts.mark_banned(acc, secs, f"FloodWait {secs}s")
            await repo.requeue_task(self.sf, task.id)

        elif kind == ErrorKind.PEER_FLOOD:
            await self.accounts.mark_banned(acc, 86400, "PeerFlood")
            await repo.requeue_task(self.sf, task.id)

        elif kind == ErrorKind.ACCOUNT_DEAD:
            await self.accounts.mark_dead(acc, str(exc)[:60])
            await repo.requeue_task(self.sf, task.id)

        elif kind == ErrorKind.GROUP_PRIVATE:
            gid = (task.payload or {}).get("group_id")
            if gid:
                await repo.mark_group_private(self.sf, int(gid), str(exc)[:200])
            await repo.mark_task_done(self.sf, task.id, {"status": "private"})

        elif kind == ErrorKind.GROUP_INVALID:
            gid = (task.payload or {}).get("group_id")
            if gid:
                await repo.mark_group_error(self.sf, int(gid), str(exc)[:200])
            await repo.mark_task_error(self.sf, task.id, str(exc))

        else:
            tb = traceback.format_exc()[-1000:]
            retry = (task.retry_count or 0) < 3
            await repo.mark_task_error(self.sf, task.id, tb, retry=retry)
