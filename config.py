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

    # Yuborish intervali variantlari (daqiqada) — E'lon yuborish bo'limida ko'rsatiladi
    BROADCAST_INTERVALS = [7, 10, 15, 20]

    PLANS = {
        "1month":  {"name": "🥉 1 Oy",   "price": 50000,  "days": 30,  "label": "🥉 1 Oy — 50,000 so'm"},
        "3months": {"name": "🥈 3 Oy",   "price": 200000, "days": 90,  "label": "🥈 3 Oy — 200,000 so'm"},
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
