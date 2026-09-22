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
from app.services.telethon_service import session_health_checker
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


async def _webhook_guard(bot: Bot):
    """
    Fon vazifasi: agar biror sabab bilan (masalan, boshqa bir dastur yoki
    deployment) ishlash paytida webhook qayta o'rnatib qo'yilsa, buni
    har 60 soniyada tekshirib, avtomatik o'chirib turadi. Bez shu bo'lmasa,
    faqat DASTUR BOSHLANISHIDA bir marta tozalanadi — agar keyin kimdir
    yana webhook o'rnatsa, polling abadiy "Conflict" xatosi bilan
    to'xtab qolaveradi.
    """
    while True:
        await asyncio.sleep(60)
        try:
            info = await bot.get_webhook_info()
            if info.url:
                logger.warning(
                    f"⚠️ Webhook ishlash paytida QAYTA o'rnatilgan ekan (URL: {info.url})! "
                    f"Avtomatik o'chirilmoqda. Buning sababini albatta topib, oldini olish kerak."
                )
                await bot.delete_webhook(drop_pending_updates=False)
        except Exception as e:
            logger.warning(f"_webhook_guard tekshiruvida xato: {e}")


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

    # MUHIM: agar bu bot tokeniga avvalroq (masalan sinov paytida, yoki
    # boshqa joyda) webhook o'rnatilgan bo'lsa, polling (getUpdates) usuli
    # u bilan TO'QNASHADI va bot yangilanishlarni umuman ololmay qoladi
    # ("Conflict: can't use getUpdates method while webhook is active").
    # Shu sabab, polling boshlashdan OLDIN webhook'ni har doim tozalab
    # qo'yamiz — webhook aslida o'rnatilmagan bo'lsa ham, bu chaqiruv
    # xavfsiz va hech narsani buzmaydi.
    #
    # Bundan tashqari, MUAMMONI ANIQLASH uchun avval joriy webhook holatini
    # LOGGA yozib qo'yamiz. Agar shu yerda haqiqiy URL ko'rinsa — demak
    # boshqa bir joy (masalan, boshqa deployment yoki boshqa dastur) shu
    # BOT_TOKEN'ni webhook rejimida ishlatmoqda va buni albatta to'xtatish
    # kerak, aks holda muammo qayta-qayta takrorlanaveradi.
    try:
        info = await bot.get_webhook_info()
        if info.url:
            logger.warning(
                f"⚠️ DIQQAT: bu botda ALLAQACHON webhook o'rnatilgan edi! "
                f"URL: {info.url} | Kutilayotgan yangilanishlar: {info.pending_update_count}. "
                f"Buni hozir o'chiryapmiz, lekin agar bu xabar qayta-qayta chiqsa, "
                f"demak boshqa bir dastur/deployment shu BOT_TOKEN'ni webhook "
                f"rejimida ishlatmoqda — uni albatta to'xtatish kerak!"
            )
        else:
            logger.info("✅ Webhook o'rnatilmagan edi (hammasi joyida).")
    except Exception as e:
        logger.error(f"Webhook holatini tekshirishda xato: {e}")

    deleted = await bot.delete_webhook(drop_pending_updates=False)
    logger.info(f"Webhook tozalash natijasi: {deleted}")

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
    asyncio.create_task(session_health_checker(bot))
    asyncio.create_task(_webhook_guard(bot))
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