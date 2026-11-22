# PUBG host boti (3-bosqich)

## Ishga tushirish

1. Python 3.10+ kerak.
2. `pip install -r requirements.txt`
3. Skrinshotdan ID o'qish uchun serverda Tesseract o'rnating:
   `sudo apt install tesseract-ocr` (Windows/Mac uchun alohida o'rnatiladi).
   Tesseract bo'lmasa bot ishlaydi, faqat skrinshot o'rniga ID ni matn qilib yuborish kerak bo'ladi.
4. `.env.example` ni `.env` ga nusxalab to'ldiring:
   - `BOT_TOKEN`, `ADMIN_IDS` (bosh admin ID si) majburiy.
   - `API_ID`, `API_HASH`: akkaunt ulash uchun. https://my.telegram.org > API development tools
     orqali bir marta olinadi, bitta juft hamma hostlarga yetadi. Bo'sh qolsa, akkaunt ulash o'chiq bo'ladi.
5. `python bot.py`

Eski `bot.db` bo'lsa o'chirmang: yangi ustun va jadvallar o'zi qo'shiladi.

## Hostlar uchun

**Post yaratish:** 3 / 4 / 5 ta karta tanlanadi, har karta uchun ID matn yoki skrinshot bilan yuboriladi.
Start time = ID yuborilgan vaqt + sozlangan daqiqa. "Oldingisini qayta" tugmasi oldingi karta ID sini qayta kiritish uchun.

**Sozlamalar** (har host uchun alohida): vaqt farqi (+3, +4, +5, +10 yoki istalgan son), parol boshlanishi,
kik/zaddeshka username, ogohlantirish matni, kartalar tartibi, post qolipi (`{n} {map} {id} {pw} {time} {contact} {note}`).
Har maydonda "Standartga qaytarish" bor. Tezkor buyruq: `/vaqt 5`.

**Akkaunt va chatlar** (Sozlamalar ichida):
1. "Akkauntni ulash": telefon raqam, Telegram yuborgan kod, kerak bo'lsa 2FA parol.
   Kodni **bo'shliq bilan** yozing (1 2 3 4 5), aks holda Telegram uni bekor qiladi.
   Kod va parol xabarlari botdan keyin o'chiriladi.
2. "Chatlarni tanlash": akkauntdagi guruh, kanal va lichkalardan belgilang (eng ko'pi 20 ta),
   yoki "Chat qo'shish" orqali @username / havola bilan qo'shing.
3. "Avto yuborish" yoqilsa, post yaratilishi bilan u host akkauntidan tanlangan chatlarga fonda ketadi
   (chatlar orasida 1.5-3 soniya pauza). Oxirida "Chatlarga yuborildi: 3/3" hisoboti keladi.
4. "Akkauntni uzish" sessiyani va chatlar ro'yxatini o'chiradi.

Cheklovlar va xavfsizlik:
- Akkaunt orqali yuborilgan xabarlarda **nusxalash / ulashish / bog'lanish tugmalari bo'lmaydi**
  (Telegram akkauntlarga inline tugma qo'yishga ruxsat bermaydi). Tugmalar faqat botning o'z postida chiqadi.
- Faqat botda yaratilgan postlar yuboriladi, erkin matn yuborish yo'q. Xabarlar o'qilmaydi.
- Sessiya bazada Fernet bilan shifrlangan. Kalit `SESSION_KEY` yoki `.session_key` faylida,
  uni va `bot.db` ni zaxiralab, begonaga bermang.
- Telegram akkauntni spam deb cheklashi mumkin. Faqat o'z turnir chatlaringizga yuboring.
- Host istalgan payt Telegram > Sozlamalar > Qurilmalar orqali "PUBG Host Bot" seansini tugatishi mumkin.

## Admin panel

"Admin panel" tugmasi (faqat adminlarga ko'rinadi):
- **Statistika:** foydalanuvchilar, hostlar, ulangan akkauntlar, bugun / 7 kun / jami postlar, eng faol hostlar
- **Hostlar:** qo'shish (ID yoki @username), olib tashlash
- **Adminlar:** qo'shish va olib tashlash faqat bosh admin (`ADMIN_IDS`) uchun
- **Kanallar:** "Turnir kanallar" tugmasida chiqadigan kanallar ro'yxati
- **Xabar yuborish:** hammaga yoki faqat hostlarga (matn, rasm, video, formatlash bilan)
- **Matnlar:** salomlashuv, kanallar sarlavhasi, hostlar uchun qo'llanma, bog'lanish matni
- **Menyu tugmalari:** oddiy foydalanuvchi tugmalarini va akkaunt ulash funksiyasini yoqish / o'chirish

Buyruqlar: `/addhost`, `/delhost`, `/addadmin`, `/deladmin`, `/hosts`, `/settext`.
Host Telegram ID sini @userinfobot orqali bilish mumkin. Host avval botga /start bosishi kerak.

## Fayllar

- `bot.py`: ishga tushirish, menyu, post yaratish oqimi
- `settings.py`: host sozlamalari
- `userbot.py`: akkaunt ulash (Telethon), chat tanlash, avto yuborish
- `admin.py`: admin panel
- `common.py`: umumiy sozlamalar, tugma matnlari, post qolipi
- `db.py`: SQLite baza
