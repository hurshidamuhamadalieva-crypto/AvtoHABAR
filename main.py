import asyncio
import logging
import sys
import os

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage

from config import config
from app.database import init_db
from app.handlers import start, payment, phone, groups, broadcast, user_info, admin
from app.middlewares.subscription import SubscriptionMiddleware, BanCheckCallbackMiddleware
from app.services.scheduler import subscription_checker
from app.handlers.phone import cleanup_stale_logins

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(config.LOGS_DIR, "bot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("aiosqlite").setLevel(logging.WARNING)


async def main():
    logger.info("🚀 AutoAd Bot ishga tushmoqda...")

    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN o'rnatilmagan! .env faylini tekshiring.")
        sys.exit(1)
    if not config.API_ID or not config.API_HASH:
        logger.error("API_ID / API_HASH o'rnatilmagan! .env faylini tekshiring.")
        sys.exit(1)

    await init_db()
    logger.info("✅ Ma'lumotlar bazasi tayyor.")

    bot = Bot(
        token=config.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.message.middleware(SubscriptionMiddleware())
    dp.callback_query.middleware(BanCheckCallbackMiddleware())

    dp.include_router(start.router)
    dp.include_router(payment.router)
    dp.include_router(phone.router)
    dp.include_router(groups.router)
    dp.include_router(broadcast.router)
    dp.include_router(user_info.router)
    dp.include_router(admin.router)

    asyncio.create_task(subscription_checker(bot))
    asyncio.create_task(cleanup_stale_logins())
    logger.info("✅ Fon vazifalar ishga tushdi.")
    logger.info("✅ Bot ishlamoqda. To'xtatish uchun Ctrl+C bosing.")

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("🛑 Bot to'xtatildi.")
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Foydalanuvchi tomonidan to'xtatildi.")
