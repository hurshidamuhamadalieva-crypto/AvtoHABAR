import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Config:
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS: list = None
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    API_ID: int = int(os.getenv("API_ID", "0"))
    API_HASH: str = os.getenv("API_HASH", "")
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///data/bot.db")
    CARD_NUMBER: str = os.getenv("CARD_NUMBER", "8600 1234 5678 9012")
    CARD_HOLDER: str = os.getenv("CARD_HOLDER", "JOHN DOE")
    SESSIONS_DIR: str = "data/sessions"
    LOGS_DIR: str = "logs"
    STAT_RETENTION_DAYS: int = max(1, int(os.getenv("STAT_RETENTION_DAYS", "7")))
    MAX_SEND_STATS: int = max(100, int(os.getenv("MAX_SEND_STATS", "20000")))

    # Yangi ulangan akkauntga "isinib olish" uchun beriladigan vaqt (daqiqada).
    # Shu vaqt ichida akkaunt orqali og'ir so'rov (masalan, barcha guruhlarni
    # olish) yuborilmaydi — chunki yangi sessiyada darhol bunday og'ir so'rov
    # yuborish Telegram tomonidan shubhali (akkaunt o'g'irlash/skraping)
    # deb topilib, akkauntni avtomatik chiqarib yuborishga sabab bo'lgan edi.
    # Qiymatni oshirsangiz (masalan 10 yoki 30) xavfsizlik yanada oshadi,
    # lekin foydalanuvchi shuncha ko'proq kutishi kerak bo'ladi.
    WARMUP_MINUTES: int = int(os.getenv("WARMUP_MINUTES", "3"))

    # Har necha daqiqada barcha ulangan akkauntlarning sessiyasi hali ham
    # amal qilayotganini yengil tekshiradi (faqat is_user_authorized —
    # hech qanday og'ir/ko'p so'rov yo'q). Bekor qilingan sessiyani DARHOL
    # aniqlab, foydalanuvchiga xabar berish uchun ishlatiladi.
    SESSION_HEALTH_CHECK_MINUTES: int = int(os.getenv("SESSION_HEALTH_CHECK_MINUTES", "15"))

    # Yuborish intervali variantlari (daqiqada) — E'lon yuborish bo'limida ko'rsatiladi
    BROADCAST_INTERVALS = [5, 7, 10, 15, 20]

    PLANS = {
        "1month":  {"name": "🥉 1 Oy",   "price": 50000,  "days": 30,  "label": "🥉 1 Oy — 50,000 so'm"},
        "3months": {"name": "🥈 3 Oy",   "price": 150000, "days": 90,  "label": "🥈 3 Oy — 150,000 so'm"},
        "5months": {"name": "🥇 5 Oy",   "price": 400000, "days": 150, "label": "🥇 5 Oy — 400,000 so'm"},
    }

    def __post_init__(self):
        if self.ADMIN_IDS is None:
            raw = os.getenv("ADMIN_IDS", "")
            self.ADMIN_IDS = [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]
        # "@username" yoki bo'sh joy bilan yozilgan bo'lsa ham to'g'ri t.me havolasi
        # hosil bo'lishi uchun tozalab olamiz (aks holda https://t.me/@username kabi
        # noto'g'ri havola hosil bo'lib, Telegram uni to'g'ridan-to'g'ri ochmay,
        # avval brauzerga tashlab yuborishi mumkin edi).
        self.ADMIN_USERNAME = (self.ADMIN_USERNAME or "admin").strip().lstrip("@")
        os.makedirs(self.SESSIONS_DIR, exist_ok=True)
        os.makedirs(self.LOGS_DIR, exist_ok=True)



config = Config()
