"""aiogram 3 handlers. Admin-only."""
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from loguru import logger
from sqlalchemy import select, update

from app.core.account_manager import AccountManager
from app.db import repo
from app.db.base import get_session_factory
from app.db.models import TargetGroup, TelegramAccount
from app.services.runner import ControlBus
from app.settings import Settings

router = Router(name="admin")


class BotContext:
    def __init__(
        self,
        settings: Settings,
        accounts: AccountManager,
        control: ControlBus,
    ) -> None:
        self.settings = settings
        self.accounts = accounts
        self.control = control


_ctx: BotContext | None = None


def set_context(ctx: BotContext) -> None:
    global _ctx
    _ctx = ctx


def ctx() -> BotContext:
    if _ctx is None:
        raise RuntimeError("bot context not set")
    return _ctx


def _is_admin(message: Message) -> bool:
    uid = message.from_user.id if message.from_user else 0
    return uid in ctx().settings.admin_ids


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    """No admin check — used for initial bootstrap to discover your user_id."""
    u = message.from_user
    if u is None:
        return
    await message.answer(
        f"your user_id: <code>{u.id}</code>\n"
        f"username: @{u.username}\n"
        f"is_admin: {u.id in ctx().settings.admin_ids}\n\n"
        f"add this id to ALLOWED_ADMIN_IDS in /opt/megaParser/.env and restart app"
    )


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    if not _is_admin(message):
        await message.answer("you are not admin. send /whoami to see your user_id")
        return
    ctx().control.run_event.set()
    ctx().control.stop_event.clear()
    await message.answer("runner started")


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    if not _is_admin(message):
        return
    ctx().control.run_event.clear()
    await message.answer("runner paused (graceful, no stop_event)")


@router.message(Command("shutdown"))
async def cmd_shutdown(message: Message) -> None:
    if not _is_admin(message):
        return
    await message.answer("shutting down...")
    ctx().control.stop_event.set()


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    if not _is_admin(message):
        return
    c = ctx()
    stats = await repo.stats_24h(get_session_factory())
    acc_total = len(c.accounts.all)
    acc_connected = sum(1 for a in c.accounts.all if a.connected)
    acc_banned = sum(1 for a in c.accounts.all if a.limiter.is_banned())
    text = (
        f"<b>megaParser status</b>\n"
        f"running: <code>{c.control.run_event.is_set()}</code>\n"
        f"accounts: <code>{acc_connected}/{acc_total}</code> connected, <code>{acc_banned}</code> banned\n\n"
        f"<b>messages</b>\n"
        f"  total:    <code>{stats['messages_total']:,}</code>\n"
        f"  posted 24h: <code>{stats['messages_24h']:,}</code> (by tg date, not parse time)\n\n"
        f"<b>groups</b>\n"
        f"  total:    <code>{stats['groups_total']:,}</code>\n"
        f"  scanned:  <code>{stats['groups_scanned']:,}</code>\n"
        f"  pending:  <code>{stats['groups_pending']:,}</code>\n\n"
        f"<b>queue</b>\n"
        f"  pending tasks:    <code>{stats['pending_tasks']:,}</code>\n"
        f"  unresolved links: <code>{stats['unresolved_links']:,}</code>"
    )
    await message.answer(text)


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message):
        return
    stats = await repo.stats_24h(get_session_factory())
    await message.answer(
        f"<b>messages</b>\n"
        f"  total: <code>{stats['messages_total']:,}</code>\n"
        f"  by tg date 24h: <code>{stats['messages_24h']:,}</code>\n\n"
        f"<b>groups</b>\n"
        f"  total: <code>{stats['groups_total']:,}</code>\n"
        f"  scanned: <code>{stats['groups_scanned']:,}</code>\n"
        f"  pending: <code>{stats['groups_pending']:,}</code>\n\n"
        f"users in db: <code>{stats['users_total']:,}</code>\n"
        f"task queue: <code>{stats['pending_tasks']:,}</code> pending\n"
        f"chain links to resolve: <code>{stats['unresolved_links']:,}</code>"
    )


@router.message(Command("accounts"))
async def cmd_accounts(message: Message) -> None:
    if not _is_admin(message):
        return
    rows = ctx().accounts.stats()
    if not rows:
        await message.answer("no accounts loaded")
        return
    lines = ["*accounts:*"]
    for r in rows[:30]:
        flag = "✓" if r["connected"] else "✗"
        lines.append(
            f"`{flag}` {r['name']} [{r['role']}] "
            f"{r['status']} g={r['groups_today']} fl={r['floods_hour']}"
        )
    if len(rows) > 30:
        lines.append(f"... and {len(rows) - 30} more")
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("tasks"))
async def cmd_tasks(message: Message) -> None:
    if not _is_admin(message):
        return
    sf = get_session_factory()
    from app.db.models import ParserTask

    async with sf() as s:
        res = await s.execute(
            select(ParserTask).order_by(ParserTask.created_at.desc()).limit(20)
        )
        rows = res.scalars().all()
    if not rows:
        await message.answer("no tasks")
        return
    lines = ["*recent tasks:*"]
    for t in rows:
        lines.append(
            f"`#{t.id}` {t.task_type} [{t.status}] {t.target or ''}"
        )
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("seed"))
async def cmd_seed(message: Message) -> None:
    if not _is_admin(message):
        return
    text = (message.text or "").split(maxsplit=1)
    if len(text) < 2:
        await message.answer(
            "usage: /seed @group1 @group2 ...\n"
            "accepts multiple groups (space, newline, or t.me/ links)"
        )
        return
    raw = text[1].replace("\n", " ").replace(",", " ")
    import re

    targets: list[str] = []
    for token in raw.split():
        token = token.strip()
        if not token:
            continue
        m = re.search(r"(?:t\.me/|@)?([A-Za-z][A-Za-z0-9_]{3,})", token)
        if m:
            targets.append(m.group(1))
    if not targets:
        await message.answer("no valid group names parsed")
        return

    sf = get_session_factory()
    created = 0
    for u in targets:
        await repo.upsert_target_group(
            sf,
            {"tg_id": None, "username": u, "discovered_via": "manual", "depth": 0},
        )
        await repo.create_task(sf, "scan_messages", target=u, priority=10)
        created += 1
    await message.answer(f"seeded {created} groups, scan_messages tasks created")


@router.message(Command("discover"))
async def cmd_discover(message: Message) -> None:
    if not _is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    kind = (parts[1].strip() if len(parts) > 1 else "chain").lower()
    if kind not in ("chain", "seed", "keyword"):
        await message.answer("usage: /discover [chain|seed|keyword]")
        return
    sf = get_session_factory()
    tid = await repo.create_task(
        sf, "discover", target=kind, payload={"kind": kind}, priority=5
    )
    await message.answer(
        f"discover task #{tid} ({kind}) created. "
        f"chain-walk will resolve t.me/ links from parsed messages into new groups"
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    if not _is_admin(message):
        return
    await message.answer(
        "<b>megaParser — workflow</b>\n\n"
        "1) <code>/seed @group1 @group2 ...</code>\n"
        "   добавь стартовые группы/каналы\n\n"
        "2) <b>runner сам подхватит</b> их через 30 сек\n"
        "   workers скачают историю сообщений, извлекут t.me/ ссылки\n\n"
        "3) <code>/discover</code>\n"
        "   запустить chain-walk (резолвит ссылки → новые группы)\n"
        "   этот шаг запускается и автоматически каждые ~5 мин\n\n"
        "4) <code>/find ключевое_слово</code>\n"
        "   global search по keyword (нужен аккаунт с can_search=True)\n\n"
        "5) <code>/monitor add @group</code>\n"
        "   realtime мониторинг новых сообщений в группе\n\n"
        "<b>статус</b>\n"
        "<code>/status</code> · <code>/stats</code> · <code>/accounts</code> · <code>/tasks</code>\n\n"
        "<b>управление</b>\n"
        "<code>/start</code> — разрешить workers брать задачи\n"
        "<code>/stop</code> — пауза (graceful)\n"
        "<code>/shutdown</code> — полный стоп\n\n"
        "<b>как парсить весь TG</b>\n"
        "дай 5-10 разнотематических <code>/seed</code> групп, runner найдёт остальное "
        "через chain-walk (ссылки → новые группы → ссылки → ...)"
    )


@router.message(Command("find"))
async def cmd_find(message: Message) -> None:
    if not _is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("usage: /find <keyword>")
        return
    kw = parts[1].strip()
    sf = get_session_factory()
    tid = await repo.create_task(
        sf, "discover", target=kw, payload={"kind": "keyword", "word": kw}, priority=5
    )
    await message.answer(f"discover task #{tid} created for '{kw}'")


@router.message(Command("monitor"))
async def cmd_monitor(message: Message) -> None:
    if not _is_admin(message):
        return
    parts = (message.text or "").split(maxsplit=2)
    if len(parts) < 3:
        await message.answer("usage: /monitor add|remove @group")
        return
    action, target = parts[1].lower(), parts[2].lstrip("@")
    sf = get_session_factory()
    async with sf() as s, s.begin():
        res = await s.execute(select(TargetGroup).where(TargetGroup.username == target))
        g = res.scalar_one_or_none()
        if g is None:
            await message.answer(f"group {target} not in DB — /seed first")
            return
        await s.execute(
            update(TargetGroup)
            .where(TargetGroup.id == g.id)
            .values(monitor_enabled=(action == "add"))
        )
    await message.answer(f"monitor {action} {target}")


@router.message(Command("health"))
async def cmd_health(message: Message) -> None:
    if not _is_admin(message):
        return
    c = ctx()
    alive = sum(1 for a in c.accounts.all if a.connected)
    await message.answer(
        f'{{"db_ok": true, "accounts_active": {alive}, "running": {str(c.control.run_event.is_set()).lower()}}}'
    )


@router.message(F.text & ~F.text.startswith("/"))
async def catchall(message: Message) -> None:
    if not _is_admin(message):
        return
    await message.answer("send /help for the full command list and workflow")
