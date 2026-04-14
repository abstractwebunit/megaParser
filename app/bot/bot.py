from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.bot.handlers import BotContext, router, set_context
from app.core.account_manager import AccountManager
from app.services.runner import ControlBus
from app.settings import Settings


def build_bot(
    settings: Settings,
    accounts: AccountManager,
    control: ControlBus,
) -> tuple[Bot, Dispatcher]:
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.include_router(router)
    set_context(BotContext(settings=settings, accounts=accounts, control=control))
    return bot, dp
