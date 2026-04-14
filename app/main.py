"""megaParser entrypoint — starts runner + aiogram bot + monitor in one event loop."""
import asyncio
import signal

from loguru import logger

from app.bot.bot import build_bot
from app.core.account_manager import AccountManager
from app.crypto import get_crypto
from app.db.base import create_db, dispose_db
from app.log import setup_logging
from app.services.monitor import MonitorService
from app.services.runner import ControlBus, Runner
from app.settings import get_settings


async def _async_main() -> None:
    settings = get_settings()
    setup_logging(settings.log_dir, settings.log_level)
    logger.info("megaParser starting (dry_run={})", settings.dry_run)

    # init crypto early to fail-fast on missing FERNET_KEY
    get_crypto(settings.fernet_key)

    engine, session_factory = create_db(settings.database_url)

    control = ControlBus()
    accounts = AccountManager(session_factory, settings)
    await accounts.load_all(settings.yaml_cfg.runner.max_concurrent_accounts)

    runner = Runner(accounts, session_factory, settings, control)
    bot, dp = build_bot(settings, accounts, control)
    monitor = MonitorService(accounts, session_factory, settings)

    loop = asyncio.get_running_loop()

    def _on_sigint() -> None:
        logger.warning("SIGINT received, shutting down")
        control.stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, _on_sigint)
        loop.add_signal_handler(signal.SIGTERM, _on_sigint)
    except NotImplementedError:
        pass

    try:
        await asyncio.gather(
            runner.start(),
            dp.start_polling(bot),
            monitor.start(),
            return_exceptions=True,
        )
    finally:
        logger.info("graceful shutdown")
        control.stop_event.set()
        try:
            await monitor.stop()
        except Exception:
            pass
        try:
            await accounts.disconnect_all()
        except Exception:
            pass
        try:
            await bot.session.close()
        except Exception:
            pass
        try:
            await dp.stop_polling()
        except Exception:
            pass
        await dispose_db()
        logger.info("bye")


def run() -> None:
    asyncio.run(_async_main())


if __name__ == "__main__":
    run()
