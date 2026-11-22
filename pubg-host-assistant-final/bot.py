import asyncio
import io
import logging
import os
import re
from datetime import timedelta
from uuid import uuid4

from aiogram import Bot, Dispatcher, F, Router
from aiohttp import web
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    InlineQuery,
    InlineQueryResultArticle,
    InputTextMessageContent,
)

import admin
import db
import settings
import userbot
from common import (
    ADMIN_IDS,
    BTN_CANCEL,
    BTN_CHANNELS,
    BTN_CONTACT,
    BTN_CREATE,
    BTN_GUIDE,
    BTN_REDO,
    DEFAULT_TEXTS,
    NOT_SPECIAL,
    TZ,
    flow_kb,
    is_host,
    main_kb,
    plain,
    render_post,
)

router = Router()


class PostFlow(StatesGroup):
    waiting_id = State()


# ---------- yordamchilar ----------

def post_kb(uid: int, lobby_id: str, html_text: str) -> InlineKeyboardMarkup:
    username = db.get_username(uid)
    contact_url = f"https://t.me/{username}" if username else f"tg://user?id={uid}"
    share_key = uuid4().hex[:16]
    db.save_share(share_key, uid, lobby_id, html_text)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text="🟢 Lobby ID ni nusxalash",
                copy_text=CopyTextButton(text=lobby_id),
                style="success",
            )],
            [InlineKeyboardButton(
                text="🔵 Postni tugmalari bilan ulashish",
                switch_inline_query=share_key,
                style="primary",
            )],
            [InlineKeyboardButton(
                text="📞 Host bilan bog'lanish", url=contact_url, style="primary"
            )],
        ]
    )


@router.inline_query()
async def share_post_inline(query: InlineQuery):
    key = (query.query or "").strip()
    row = db.get_share(key) if key else None
    if not row:
        await query.answer([], cache_time=0, is_personal=True)
        return
    uid, lobby_id, html_text = row["user_id"], row["lobby_id"], row["html_text"]
    username = db.get_username(uid)
    contact_url = f"https://t.me/{username}" if username else f"tg://user?id={uid}"
    markup = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🟢 Lobby ID ni nusxalash", copy_text=CopyTextButton(text=lobby_id), style="success")],
        [InlineKeyboardButton(text="📞 Host bilan bog'lanish", url=contact_url, style="primary")],
    ])
    result = InlineQueryResultArticle(
        id=key,
        title="PUBG lobby postini ulashish",
        description="Post matni va tugmalari bilan yuboriladi",
        input_message_content=InputTextMessageContent(
            message_text=html_text, parse_mode=ParseMode.HTML,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        ),
        reply_markup=markup,
    )
    await query.answer([result], cache_time=0, is_personal=True)


def _ocr_sync(data: bytes):
    """(id yoki None, OCR mavjudmi) qaytaradi."""
    try:
        import pytesseract
        from PIL import Image, ImageOps
    except ImportError:
        return None, False
    try:
        img = Image.open(io.BytesIO(data)).convert("L")
        img = ImageOps.autocontrast(img)
        w, h = img.size
        if max(w, h) < 2000:
            img = img.resize((w * 2, h * 2))
        found: list[str] = []
        for psm in (6, 11):
            text = pytesseract.image_to_string(img, config=f"--psm {psm}")
            found += re.findall(r"(?<!\d)\d{6,8}(?!\d)", text)
            if any(len(x) == 7 for x in found):
                break
        for x in found:
            if len(x) == 7:
                return x, True
        return (found[0] if found else None), True
    except Exception:
        logging.exception("OCR xatosi")
        return None, False


async def extract_id(message: Message, bot: Bot):
    """Xabardan ID ni oladi. (id yoki None, xato matni yoki None)"""
    if message.text:
        m = re.search(r"\d{5,9}", message.text)
        if m:
            return m.group(0), None
        return None, "ID topilmadi. Raqamni yozib yuboring yoki skrinshot tashlang."
    if message.photo:
        buf = await bot.download(message.photo[-1])
        lobby_id, ok = await asyncio.to_thread(_ocr_sync, buf.read())
        if lobby_id:
            return lobby_id, None
        if not ok:
            return None, "Skrinshotni o'qiy olmadim. ID ni matn qilib yuboring."
        return None, "Skrinshotdan ID topilmadi. Aniqroq skrinshot yoki matn yuboring."
    return None, "ID ni matn yoki skrinshot qilib yuboring."


def ask_text(idx: int, count: int, maps: list[str]) -> str:
    return (
        f"{idx + 1}-karta ({maps[idx]}), jami {count} ta.\n"
        "ID ni matn yoki skrinshot qilib yuboring."
    )


# ---------- /start va oddiy menyu ----------

@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    u = message.from_user
    db.upsert_user(u.id, u.username, u.full_name)
    await message.answer(
        db.get_text("welcome", DEFAULT_TEXTS["welcome"]), reply_markup=main_kb(u.id)
    )


@router.message(F.text == BTN_CHANNELS)
async def btn_channels(message: Message, state: FSMContext):
    await state.clear()
    chans = db.list_channels()
    default = DEFAULT_TEXTS["channels"] if chans else "Hozircha turnir kanallari qo'shilmagan."
    markup = None
    if chans:
        markup = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=c["title"], url=c["url"])] for c in chans]
        )
    await message.answer(db.get_text("channels", default), reply_markup=markup)


@router.message(F.text == BTN_GUIDE)
async def btn_guide(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(db.get_text("guide", DEFAULT_TEXTS["guide"]))


@router.message(F.text == BTN_CONTACT)
async def btn_contact(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(db.get_text("contact", DEFAULT_TEXTS["contact"]))


# ---------- post yaratish oqimi ----------

@router.message(F.text == BTN_CREATE)
async def create_start(message: Message, state: FSMContext):
    u = message.from_user
    if not is_host(u.id):
        return
    db.upsert_user(u.id, u.username, u.full_name)
    await state.clear()
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🔴 3 ta karta", callback_data="cnt:3")],
            [InlineKeyboardButton(text="🔵 4 ta karta", callback_data="cnt:4")],
            [InlineKeyboardButton(text="🟢 5 ta karta (final)", callback_data="cnt:5")],
        ]
    )
    await message.answer("Nechta karta o'ynaladi?", reply_markup=kb)


@router.callback_query(F.data.startswith("cnt:"))
async def choose_count(cb: CallbackQuery, state: FSMContext):
    if not is_host(cb.from_user.id):
        await cb.answer()
        return
    count = int(cb.data.split(":")[1])
    if count not in (3, 4, 5):
        await cb.answer()
        return
    maps = db.get_settings(cb.from_user.id)["maps"]
    await state.set_state(PostFlow.waiting_id)
    await state.update_data(count=count, idx=0, maps=maps)
    await cb.message.edit_text(f"Karta soni: {count}")
    await cb.message.answer(ask_text(0, count, maps), reply_markup=flow_kb())
    await cb.answer()


@router.message(PostFlow.waiting_id, F.text == BTN_CANCEL)
async def flow_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Bekor qilindi.", reply_markup=main_kb(message.from_user.id))


@router.message(PostFlow.waiting_id, F.text == BTN_REDO)
async def flow_redo(message: Message, state: FSMContext):
    data = await state.get_data()
    idx, count, maps = data["idx"], data["count"], data["maps"]
    if idx == 0:
        await message.answer("Hozir 1-kartadasiz.\n" + ask_text(0, count, maps))
        return
    idx -= 1
    await state.update_data(idx=idx)
    await message.answer(ask_text(idx, count, maps))


@router.message(PostFlow.waiting_id, F.photo | NOT_SPECIAL)
async def flow_got_id(message: Message, state: FSMContext, bot: Bot):
    u = message.from_user
    lobby_id, error = await extract_id(message, bot)
    if error:
        await message.answer(error)
        return

    data = await state.get_data()
    if not data or not all(k in data for k in ("idx", "count", "maps")):
        await state.clear()
        await message.answer("Post yaratish sessiyasi uzilib qoldi. 'Post yaratish'ni qayta bosing.")
        return
    idx, count, maps = data["idx"], data["count"], data["maps"]
    if not isinstance(maps, list) or not (0 <= idx < count) or idx >= len(maps):
        await state.clear()
        await message.answer("Karta sozlamasida xatolik bor. Sozlamalardan 5 ta kartani tekshiring.")
        return
    s = db.get_settings(u.id)

    # Start time = ID yuborilgan vaqt + host belgilagan daqiqa (standart +4)
    sent = message.date.astimezone(TZ)
    start = (sent + timedelta(minutes=s["shift"])).strftime("%H:%M")

    html_text = render_post(s, idx + 1, maps[idx], lobby_id, start)
    try:
        await message.answer(
            html_text,
            reply_markup=post_kb(u.id, lobby_id, html_text),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramBadRequest:
        # Eski Telegram Bot API/server yoki client copy/style tugmalarini qabul
        # qilmasa, postning o'zi yo'qolib qolmasin: oddiy inline tugmalar bilan yuboramiz.
        logging.exception("Rangli/copy tugmalarni yuborib bo'lmadi; oddiy tugmalarga o'tildi")
        await message.answer(
            html_text,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )
    except TelegramAPIError:
        logging.exception("Post yuborishda Telegram API xatosi")
        await message.answer("Postni yuborib bo'lmadi. Bot logidagi xatoni tekshirish kerak.")
        return

    db.add_post(u.id, maps[idx], lobby_id)
    try:
        userbot.schedule_send(bot, u.id, html_text)  # akkaunt ulangan va avto yuborish yoqilgan bo'lsa
    except Exception:
        logging.exception("Avto yuborishni rejalashda xato")

    idx += 1
    if idx >= count:
        await state.clear()
        await message.answer("Hammasi tayyor.", reply_markup=main_kb(u.id))
    else:
        await state.update_data(idx=idx)
        await message.answer(ask_text(idx, count, maps))


# ---------- ishga tushirish ----------

async def health(request):
    return web.json_response({"ok": True, "service": "pubg-host-bot"})


async def main():
    logging.basicConfig(level=logging.INFO)
    token = os.getenv("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN topilmadi. Render Environment yoki .env ga kiriting.")
    db.init()
    for aid in ADMIN_IDS:
        db.set_role(aid, "admin")

    bot = Bot(token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    # Tartib muhim: avval menyu va post oqimi, keyin sozlamalar va admin panel
    dp.include_router(router)
    dp.include_router(settings.router)
    dp.include_router(userbot.router)
    dp.include_router(admin.router)

    # Render Web Service uchun HTTP health endpoint.
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", "10000"))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info("Health server 0.0.0.0:%s da ishga tushdi", port)

    try:
        await dp.start_polling(bot)
    finally:
        await userbot.shutdown()
        await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
