# 🤖 AutoAd Bot — Telegram Auto Advertisement Bot

A professional, production-ready Telegram bot for automated group advertising with subscription management.

## ✨ Features

- 💳 **3 Subscription Plans** with admin approval
- 📱 **Telethon Integration** — connects user's own Telegram account
- 📁 **Folder-based Group Import** — auto-detects groups from Telegram folders
- 📢 **One-flow Broadcast** — ad text → interval → confirm → auto-send, all in a single guided flow
- ⏱ **Customizable Intervals** — 7, 10, 15, or 20 minutes
- ⏸ **Pause/Resume** sending without losing state
- 📊 **Admin Panel** with full user/payment management
- 💰 **2 Payment Methods**: Admin, Card+Screenshot
- 🔔 **Automatic Reminders** for expiring subscriptions
- 🚫 **Ban/Unban** users from admin panel
- 📢 **Broadcast** messages to all users
- 🖲 **Fully inline UI** — every menu uses inline buttons (except the one spot Telegram requires a reply keyboard: sharing your phone number)

---

## 📁 Project Structure

```
tg_adbot/
├── main.py                    # Entry point
├── config.py                  # Configuration
├── requirements.txt           # Dependencies
├── .env.example              # Environment template
├── .env                      # Your environment (create from example)
├── data/
│   ├── bot.db                # SQLite database (auto-created)
│   └── sessions/             # Telethon sessions (auto-created)
├── logs/
│   └── bot.log               # Log file (auto-created)
└── app/
    ├── database.py           # Database models & helpers
    ├── keyboards.py          # All keyboards/buttons
    ├── states.py             # FSM states
    ├── handlers/
    │   ├── start.py          # /start, welcome, main menu
    │   ├── payment.py        # Plan selection & payment flow
    │   ├── phone.py          # Telegram account connection
    │   ├── groups.py         # Group/folder management
    │   ├── broadcast.py      # Ad text + interval + start/pause/stop (single flow)
    │   ├── user_info.py      # Payments history, contact admin
    │   └── admin.py          # Admin panel
    ├── middlewares/
    │   └── subscription.py   # Subscription gate middleware
    └── services/
        ├── telethon_service.py   # Telethon client management
        ├── sender_service.py     # Background sending engine
        └── scheduler.py          # Subscription reminder scheduler
```

---

## 🚀 Quick Start

### 1. Prerequisites

- Python 3.10+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Telegram API credentials from [my.telegram.org](https://my.telegram.org)

### 2. Get Telegram API Credentials

1. Go to [https://my.telegram.org](https://my.telegram.org)
2. Log in with your phone number
3. Go to **API Development Tools**
4. Create an application
5. Copy your `API_ID` and `API_HASH`

### 3. Clone & Setup

```bash
# Navigate to project folder
cd tg_adbot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate      # Linux/Mac
# venv\Scripts\activate       # Windows

# Install dependencies
pip install -r requirements.txt
```

### 4. Configure Environment

```bash
# Copy example env file
cp .env.example .env

# Edit .env with your values
nano .env   # or any text editor
```

Fill in:
- `BOT_TOKEN` — your bot token from @BotFather
- `ADMIN_IDS` — your Telegram user ID(s), comma-separated
- `ADMIN_USERNAME` — your Telegram username (without @)
- `API_ID` — from my.telegram.org
- `API_HASH` — from my.telegram.org
- `CARD_NUMBER` — your payment card number
- `CARD_HOLDER` — cardholder name

> Note: `ADMIN_USERNAME` works whether you write it with or without the leading `@` (e.g. `@myadmin` or `myadmin`) — the bot strips it automatically so the "Contact admin" button always opens Telegram directly instead of a browser.

### 5. Run the Bot

```bash
python main.py
```

---

## 💳 Subscription Plans

| Plan | Price | Duration |
|------|-------|----------|
| 🥉 1 Month | 50,000 UZS | 30 days |
| 🥈 3 Months | 200,000 UZS | 90 days |
| 🥇 5 Months | 400,000 UZS | 150 days |

Prices can be changed in `config.py` under `PLANS`.

---

## 👤 User Flow

1. User sends `/start`
2. Selects a subscription plan
3. Chooses payment method (Admin / Card)
4. Admin approves payment → user gets access
5. User connects their Telegram account (📱 Raqam qo'shish)
6. User imports groups from a folder (📂 Guruh qo'shish)
7. User taps **📢 E'lon yuborish** — writes the ad text, picks an interval, confirms
8. Bot sends the ad to all groups automatically and keeps repeating on the chosen interval (with pause/stop controls)

---

## ⚙️ Admin Panel Commands

Access via **⚙️ Admin Panel** button (shown to admins only):

| Button | Action |
|--------|--------|
| 👥 Users | View all registered users |
| 💰 Payments | View payment history |
| 📊 Statistics | Revenue, user count, messages sent |
| 📢 Broadcast | Send message to all users |
| 🚫 Ban User | Ban by Telegram ID |
| ✅ Approve User | Manually activate a user |
| ⏹ Stop User Sending | Force-stop a user's sending session |
| 🔙 Main Menu | Return to main menu |

---

## 🛡️ Security Notes

- User Telegram sessions are stored as encrypted strings in the database
- Sessions never leave the server
- Bot only sends to groups the user has access to
- Users are responsible for their own ad content
- Admin must manually approve all payments

---

## 🔧 Production Deployment

### Using systemd (Linux)

Create `/etc/systemd/system/autoadbot.service`:

```ini
[Unit]
Description=AutoAd Telegram Bot
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/tg_adbot
ExecStart=/path/to/tg_adbot/venv/bin/python main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable autoadbot
sudo systemctl start autoadbot
sudo systemctl status autoadbot
```

### Using screen

```bash
screen -S autoadbot
python main.py
# Ctrl+A then D to detach
```

---

## 📦 Dependencies

| Package | Version | Purpose |
|---------|---------|---------|
| aiogram | 3.13.1 | Telegram Bot Framework |
| telethon | 1.36.0 | Telegram User Client |
| sqlalchemy | 2.0.36 | Async ORM |
| aiosqlite | 0.20.0 | SQLite async driver |
| python-dotenv | 1.0.1 | Environment config |
| aiofiles | 24.1.0 | Async file I/O |
| cryptg | 0.4.0 | Telethon encryption speedup |

---

## 🐞 Troubleshooting

**Bot doesn't start:**
- Check `BOT_TOKEN` in `.env`
- Ensure Python 3.10+

**Telethon errors:**
- Verify `API_ID` and `API_HASH`
- Check internet connectivity

**No groups found:**
- User must be member of the groups
- Try using "All Groups" (if no folders exist)

**FloodWait errors:**
- Normal — bot automatically waits
- Increase interval to 15–20 minutes

**Session expired:**
- User must reconnect via 📱 Add Number

---

## 🌐 Railway (yoki boshqa server)da "kod kelmayabdi" muammosi

**Sabab:** Bitta serverdan (Railway, VPS va h.k.) bir nechta akkaunt ulanmoqchi
bo'lganingizda, Telegram bir xil IP manzildan ketma-ket kelayotgan "kod yubor"
so'rovlarini bot-farm urinishi deb hisoblab, ma'lum sondan keyin ba'zi
raqamlarga kodni **yubormay qo'yishi** mumkin (so'rov botga "muvaffaqiyatli"
qaytadi, lekin foydalanuvchiga SMS/Telegram kodi kelmaydi). Lokal kompyuterda
muammo bo'lmasligi sababi — odatda uy IP manzili Telegram nazarida "toza" va
kamdan-kam shubhali hisoblanadi, Railway kabi hosting IP'lari esa ko'p botlar
tomonidan ishlatilgani uchun tezroq cheklanadi.

**Botda nima qilingan:**
- Agar `.env` faylida `PROXIES` sozlansa, har bir yangi login urinishi
  navbatdagi (boshqa) proksidan foydalanadi — shu bilan Telegram uchun
  so'rovlar bir nechta turli IP'dan kelayotgandek ko'rinadi.
- Har bir yangi login uchun tasodifiy, lekin haqiqiy qurilma ko'rinishi
  (Samsung, iPhone, Windows va h.k.) tanlanadi — barcha urinishlar bir xil
  "qurilma"dan kelayotganday ko'rinmaydi.
- So'rovlar orasida eng kamida bir necha soniyalik pauza saqlanadi.
- Telegram haqiqatan ham vaqtinchalik cheklov (FloodWait) qo'ysa, bot buni
  aniq ko'rsatadi ("taxminan N daqiqadan keyin urinib ko'ring") — avvalgidek
  "kod yuborildi" deb yolg'on ma'lumot bermaydi.

**Sizdan talab qilinadigan qadam — PROXIES sozlash:**
1. SOCKS5 proksi xizmati sotib oling (residential/mobile proksilar eng
   yaxshi natija beradi; oddiy datacenter proksilar ham Railway'nikidan
   yaxshiroq, lekin ba'zan ular ham cheklanishi mumkin — bepul/umumiy
   proksilardan saqlaning, ular ko'pincha allaqachon bloklangan bo'ladi).
2. Har bir proksini `.env` dagi `PROXIES` ga qo'shing:
   ```
   PROXIES=1.2.3.4:1080:user1:pass1,5.6.7.8:1080:user2:pass2
   ```
3. Nechta akkauntni bir vaqtda ulamoqchi bo'lsangiz, shuncha (yoki undan
   kamroq — chunki bot ularni aylantirib ishlatadi) proksi qo'shish tavsiya
   etiladi.

⚠️ **Muhim, halol ogohlantirish:** `PROXIES` bo'sh qoldirilsa, bot avvalgidek
Railway'ning bitta umumiy IP'idan ishlaydi — kod ketma-ket ko'p akkaunt uchun
kelmasligi ehtimoli baribir qoladi, chunki bu Telegramning o'z tomonidagi
cheklov, uni faqat haqiqiy, turli IP manzillar (ya'ni real proksilar) orqali
"aylanib o'tish" mumkin. Kodni o'zi sun'iy ravishda "har doim kep-kelaveradigan"
qilib bo'lmaydi — bu Telegram serverining xavfsizlik siyosati.
