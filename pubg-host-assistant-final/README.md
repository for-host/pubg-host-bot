# PUBG Host Assistant — birlashtirilgan versiya

## Nimalar bor
- Aiogram 3 Telegram bot: host/admin rollari, admin panel, sozlamalar.
- 3/4/5 karta bo'yicha lobby post yaratish; ID matn yoki screenshotdan OCR orqali olinadi (Tesseract o'rnatilgan bo'lishi kerak).
- Post qolipida Lobby ID `<code>` formatida, ogohlantirish `<blockquote><i>...</i></blockquote>` ko'rinishida.
- Lobby ID nusxalash tugmasi (yashil style), inline ulashish tugmasi (ko'k style), host bilan bog'lanish tugmasi.
- Inline ulashishda post matni va inline tugmalari birga yuboriladi.
- Telethon host akkaunti, chat tanlash va avtomatik yuborish (API_ID/API_HASH va SESSION_KEY kerak).
- Render uchun `/health` HTTP endpoint.

## Lokal ishga tushirish
Python 3.10+:
```bash
python -m pip install -r requirements.txt
cp .env.example .env
python bot.py
```
`.env` ichiga `BOT_TOKEN` va `ADMIN_IDS` kiriting. `ADMIN_IDS` vergul bilan ajratiladi.

## Render sozlamalari
- Service: Web Service
- Runtime: Python 3
- Root Directory: bo'sh
- Build Command: `pip install -r requirements.txt`
- Start Command: `python bot.py`
- Health Check Path: `/health`
- Environment: `BOT_TOKEN`, `ADMIN_IDS`, `TZ_NAME=Asia/Tashkent`
- Host akkaunt funksiyasi kerak bo'lsa keyin `API_ID`, `API_HASH`, `SESSION_KEY` ham qo'shiladi.

`SESSION_KEY` yaratish:
```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```
Kalitni chatga yoki GitHub'ga yubormang; faqat Render Environment'da saqlang.

## Inline ulashishni yoqish
BotFather'da `/setinline` → botni tanlang → Enable. Shundan keyin postdagi "Postni tugmalari bilan ulashish" tugmasi chat tanlash oynasini ochadi. Tanlangan chatda inline natijani yuborish kerak.

## Muhim cheklovlar
- Telegram inline tugmalar ranglari Telegram klienti va Bot API qo'llab-quvvatlashiga bog'liq; barcha qurilmalarda bir xil ko'rinishi kafolatlanmaydi.
- Telegram akkaunti nomidan yuborilgan xabarlarga bot inline tugmalari biriktirib bo'lmaydi. Tugmali ulashish bot orqali inline mode bilan bajariladi.
- Render Free uxlab qolishi yoki qayta ishga tushishi mumkin. UptimeRobot health so'rovlari uyquni oldini olishga yordam berishi mumkin, ammo 24/7 kafolat bermaydi.
- SQLite `bot.db` Render Free ephemeral diskida doimiy saqlanmaydi. Restart/deploydan keyin ma'lumot yo'qolishi mumkin. Doimiy ishlatishdan oldin persistent storage yoki tashqi PostgreSQL migratsiyasi kerak.
- Telethon sessiyasi ham doimiy saqlash talab qiladi. Host akkauntini Render Free'da doimiy ulashni hozircha yoqmang.

## Xavfsizlik
`.env`, `.session_key`, Telethon session va `bot.db` fayllarini GitHub'ga commit qilmang. `.gitignore` qo'shilgan.
