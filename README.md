# AvtoHABAR

Telegram hisobidan tanlangan guruh va kanallarga matnli yoki rasmli e'lon yuboruvchi bot.

## Ishga tushirish

Python 3.10+ kerak. `pip install -r requirements.txt` bilan kutubxonalarni o'rnating.
`.env.example` dan `.env` yarating va BOT_TOKEN, API_ID, API_HASH, ADMIN_IDS,
ADMIN_USERNAME hamda karta ma'lumotlarini kiriting. API_ID va API_HASH o'zingizning
https://my.telegram.org ilovangizdan olinadi. API_ID=0 bilan hisob ulash ishlamaydi.
`python main.py` orqali ishga tushiring.

Bot faqat proksisiz, Telegramga to'g'ridan-to'g'ri ulanadi. Eski PROXIES,
PROXY_TYPE muhit sozlamalari va bazadagi eski proksi yozuvlari ishlatilmaydi.
Serverda Telegramga chiqish tarmog'i ochiq bo'lishi kerak.

## Hisob ulash

- Telefon raqam orqali: Telegram qaytargan javob asosida kod Telegram xizmat
  chatiga, SMS, email yoki qo'ng'iroq orqali berilishi ko'rsatiladi.
- Telegram ruxsat bergan kutish vaqtidan keyin kodni bir marta qayta so'rash mumkin.
  Qayta so'rash imkoniyatini Telegramning o'zi belgilaydi.
- Kod kelmasa **QR orqali ulash (kodsiz)** tugmasidan foydalaning. QR'ni boshqa
  ekranda ochib, telefondagi Telegram → Sozlamalar → Qurilmalar → Qurilma ulash
  orqali skanerlang. Muddati o'tgan QR yangidan olinadi.
- 2FA yoqilgan hisobda bulut paroli alohida so'raladi. Parol xabari tekshirishdan
  keyin o'chiriladi. QR rasmlari muddati tugaganda, ulanish yakunlanganda yoki
  bekor qilinganda o'chiriladi; diskda to'planmaydi.
- Qo'llab-quvvatlanmagan yoki muddati tugagan javobda "kod yuborildi" degan
  xabar chiqarilmaydi. Kod yetkazilishi Telegram tomonidan boshqariladi.

## Obuna va admin

ADMIN_IDS dagi adminlar obunasiz va to'lov arizasisiz barcha bo'limlardan foydalanadi.
Avval bazaga oddiy foydalanuvchi bo'lib kirgan adminning maqomi ham yangilanadi.
Oddiy foydalanuvchilar uchun 1, 3 yoki 5 oylik obuna va admin tasdig'i mavjud.

Admin paneli → **Shafyor qo'shish** → ID → tarif → tasdiqlash: to'lov arizasisiz
obuna bugundan tanlangan muddatga beriladi, oldingi faol tarif almashtiriladi.
Foydalanuvchi hali /start bosmagan bo'lsa ham obuna saqlanadi. Takroriy tasdiqlash
va eski tugmalar orqali boshqa arizani faollashtirish cheklangan.

## E'lonlar va guruh/kanallar

Guruh yoki kanalni Telegram jildidan import qiling yoki "Bot uchun" jildida tanlang.
Kanalga yozish uchun ulangan hisob kanal egasi yoki post joylash huquqli admin bo'lishi kerak.

Matnli e'lon yoki **Rasmli e'lon yuborish** → rasm → matn → interval → tasdiqlash.
Rasmli matn 1024, oddiy matn 4096 Telegram belgisigacha. Formatlash saqlanadi.
Interval variantlari: 5, 7, 10, 15 va 20 daqiqa; sikl tugagandan keyin hisoblanadi.
Pauzada **Tahrirlash** orqali matn/rasm yangilanadi va eski intervalda davom etadi.
Obuna tugaganda oddiy foydalanuvchining yuborishi to'xtaydi; adminda cheklanmaydi.

## Serverda joy sarfi

Rasmning Telegram file_id qiymati saqlanadi, server diskiga rasm yig'ilmaydi.
Loglar 2 MB dan 4 fayl bilan cheklanadi. Yuborish tafsilotlari har soatda tozalanadi:
STAT_RETENTION_DAYS=7, MAX_SEND_STATS=20000; jami yuborish hisoblagichi saqlanadi.
Faqat joriy e'lon saqlanadi. SQLite'da bo'sh sahifalar yetarlicha yig'ilsa joy qaytariladi.

Docker'da data/ doimiy diskka ulangan bo'lishi kerak. Yangilashda yangi requirements.txt
bo'yicha paketlarni o'rnatib, botni qayta ishga tushiring. SQLite ustunlari va yangi
jadvallar avtomatik qo'shiladi. Mavjud obuna va guruhlar saqlanadi.

## Testlar

`python -m unittest discover -s tests -v`

Testlar vaqtinchalik bazada ishlaydi, haqiqiy foydalanuvchilarga xabar yoki kod yubormaydi.
