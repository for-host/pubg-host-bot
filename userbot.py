"""Host akkauntini ulash (Telethon) va tanlangan chatlarga postlarni avtomatik yuborish.

Muhim cheklovlar:
- Akkaunt orqali faqat botda yaratilgan postlar yuboriladi (erkin matn yuborish yo'q).
- Xabarlar o'qilmaydi, hech qanday xabar handleri ulanmagan.
- Sessiya bazada shifrlangan (Fernet) holda saqlanadi.
- Akkaunt xabarlarida inline tugmalar bo'lmaydi (Telegram buni akkauntlarga ruxsat bermaydi).
"""
import asyncio
import logging
import os
import random
import re
import time
from html import escape
from pathlib import Path

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from common import NOT_SPECIAL, HostOnly, edit_or_send

try:
    from telethon import TelegramClient, errors, utils
    from telethon.sessions import StringSession

    TELETHON_OK = True
except ImportError:  # pragma: no cover
    TELETHON_OK = False

try:
    from cryptography.fernet import Fernet, InvalidToken

    CRYPTO_OK = True
except ImportError:  # pragma: no cover
    CRYPTO_OK = False

API_ID = int(os.getenv("API_ID", "0") or 0)
API_HASH = os.getenv("API_HASH", "")
MAX_TARGETS = 20
PAGE_SIZE = 8
PENDING_TTL = 600  # login seansi 10 daqiqa

router = Router()
router.message.filter(HostOnly())
router.callback_query.filter(HostOnly())


class Login(StatesGroup):
    phone = State()
    code = State()
    password = State()


class AddChat(StatesGroup):
    chat = State()


_clients: dict = {}
_pending: dict = {}
_dialogs: dict = {}
_conn_locks: dict = {}
_send_locks: dict = {}
_tasks: set = set()


def _conn_lock(uid: int) -> asyncio.Lock:
    return _conn_locks.setdefault(uid, asyncio.Lock())


def _send_lock(uid: int) -> asyncio.Lock:
    return _send_locks.setdefault(uid, asyncio.Lock())


# ---------- mavjudlik va shifrlash ----------

def unavailable_reason() -> str | None:
    if not db.get_flag("feature_userbot"):
        return "Akkaunt ulash funksiyasi admin tomonidan o'chirilgan."
    if not TELETHON_OK or not CRYPTO_OK:
        return "Serverda telethon yoki cryptography o'rnatilmagan (pip install -r requirements.txt)."
    if not API_ID or not API_HASH:
        return "Admin hali API_ID va API_HASH ni .env ga kiritmagan."
    return None


def _fernet():
    key = os.getenv("SESSION_KEY")
    if not key:
        path = Path(__file__).with_name(".session_key")
        if path.exists():
            key = path.read_text().strip()
        else:
            key = Fernet.generate_key().decode()
            path.write_text(key)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    return Fernet(key.encode())


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(value: str):
    try:
        return _fernet().decrypt(value.encode()).decode()
    except (InvalidToken, ValueError):
        return None


# ---------- Telethon klientlari ----------

def new_client(session: str = ""):
    return TelegramClient(
        StringSession(session), API_ID, API_HASH,
        device_model="PUBG Host Bot", system_version="Server", app_version="1.0",
    )


async def _quiet_disconnect(client) -> None:
    try:
        await client.disconnect()
    except Exception:
        pass


def drop_session(uid: int) -> None:
    db.clear_session(uid)
    _clients.pop(uid, None)
    _dialogs.pop(uid, None)


async def get_client(uid: int):
    """Hostning ulangan klientini qaytaradi (kerak bo'lsa ulaydi). Ulanmagan bo'lsa None."""
    async with _conn_lock(uid):
        c = _clients.get(uid)
        if c is not None and c.is_connected():
            return c
        enc = db.get_session(uid)
        sess = decrypt(enc) if enc else None
        if not sess:
            return None
        c = new_client(sess)
        try:
            await c.connect()
            authorized = await c.is_user_authorized()
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError,
                errors.UserDeactivatedError):
            authorized = False
        except Exception:
            logging.exception("akkauntga ulanib bo'lmadi")
            await _quiet_disconnect(c)
            return None  # tarmoq xatosi bo'lishi mumkin, sessiyani o'chirmaymiz
        if not authorized:
            await _quiet_disconnect(c)
            drop_session(uid)
            return None
        try:
            await c.get_dialogs(limit=200)  # chatlarni tanib olish uchun
        except Exception:
            logging.exception("dialoglarni yuklab bo'lmadi")
        _clients[uid] = c
        return c


async def cleanup_pending(uid: int) -> None:
    p = _pending.pop(uid, None)
    if p:
        await _quiet_disconnect(p["client"])


async def shutdown() -> None:
    for uid in list(_pending):
        await cleanup_pending(uid)
    for c in list(_clients.values()):
        await _quiet_disconnect(c)
    _clients.clear()


# ---------- yuborish ----------

def schedule_send(bot: Bot, uid: int, html_text: str) -> None:
    """Postni host akkauntidan tanlangan chatlarga fonda yuboradi (botni to'xtatmaydi)."""
    if not db.get_session(uid) or not db.get_settings(uid)["autosend"]:
        return
    if not db.list_targets(uid) or unavailable_reason():
        return
    task = asyncio.create_task(_auto_send(bot, uid, html_text))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _send_one(c, chat_id: int, text: str):
    """None (muvaffaqiyat) yoki xato matnini qaytaradi."""
    for attempt in range(2):
        try:
            await c.send_message(chat_id, text, parse_mode="html", link_preview=False)
            return None
        except (errors.FloodWaitError, errors.SlowModeWaitError) as e:
            if attempt == 0 and e.seconds <= 30:
                await asyncio.sleep(e.seconds + 1)
                continue
            return f"Telegram cheklovi, {e.seconds} soniya kuting"
        except errors.ChatWriteForbiddenError:
            return "bu chatga yozishga ruxsat yo'q"
        except errors.UserBannedInChannelError:
            return "akkaunt bu chatda cheklangan"
        except errors.ChannelPrivateError:
            return "chatga kirish yo'q"
        except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError,
                errors.UserDeactivatedError):
            raise
        except Exception as e:
            logging.exception("yuborishda xato")
            return type(e).__name__
    return "noma'lum xato"


async def _auto_send(bot: Bot, uid: int, html_text: str) -> None:
    async with _send_lock(uid):  # postlar tartib bilan ketadi
        targets = db.list_targets(uid)
        c = await get_client(uid)
        if c is None:
            await _tell(bot, uid, "Akkaunt ulanmagan yoki sessiya tugagan. "
                                  "Sozlamalar > Akkaunt va chatlar orqali qayta ulang.")
            return
        ok = 0
        fails = []
        for i, t in enumerate(targets):
            try:
                err = await _send_one(c, t["chat_id"], html_text)
            except (errors.AuthKeyUnregisteredError, errors.SessionRevokedError,
                    errors.UserDeactivatedError):
                drop_session(uid)
                await _tell(bot, uid, "Akkaunt sessiyasi bekor qilingan. Qayta ulang.")
                return
            if err is None:
                ok += 1
            else:
                fails.append(f"{escape(t['title'])}: {escape(err)}")
            if i < len(targets) - 1:
                await asyncio.sleep(random.uniform(1.5, 3.0))
        text = f"Chatlarga yuborildi: {ok}/{len(targets)}"
        if fails:
            text += "\n" + "\n".join(fails[:5])
        await _tell(bot, uid, text)


async def _tell(bot: Bot, uid: int, text: str) -> None:
    try:
        await bot.send_message(uid, text)
    except Exception:
        logging.exception("hostga xabar yuborilmadi")


# ---------- ko'rinishlar ----------

def _kb(rows, back: str | None = "ub:menu") -> InlineKeyboardMarkup:
    rows = [list(r) for r in rows]
    if back:
        rows.append([InlineKeyboardButton(text="Orqaga", callback_data=back)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def menu_view(uid: int):
    reason = unavailable_reason()
    if reason:
        return f"<b>Akkaunt va chatlar</b>\n\n{escape(reason)}", _kb([], back="st:menu")
    if not db.get_session(uid):
        text = (
            "<b>Akkaunt va chatlar</b>\n\nHolat: ulanmagan\n\n"
            "Akkauntingizni ulasangiz, bot yaratilgan postlarni sizning nomingizdan "
            "tanlagan guruh va lichkalaringizga avtomatik yuboradi."
        )
        return text, _kb([[_btn("Akkauntni ulash", "ub:connect")]], back="st:menu")
    n = len(db.list_targets(uid))
    auto = db.get_settings(uid)["autosend"]
    text = (
        "<b>Akkaunt va chatlar</b>\n\nHolat: ulangan\n"
        f"Tanlangan chatlar: <b>{n}</b>\n"
        f"Avto yuborish: <b>{'yoqilgan' if auto else 'oʻchirilgan'}</b>\n\n"
        "Avto yuborish yoqilgan bo'lsa, post yaratilishi bilan tanlangan chatlarga ketadi. "
        "Akkaunt orqali yuborilgan xabarlarda nusxalash va ulashish tugmalari bo'lmaydi."
    )
    rows = [
        [_btn("Chatlarni tanlash", "ub:pick:0")],
        [_btn("Chat qo'shish (username / havola)", "ub:add")],
        [_btn(f"Tanlangan chatlar ({n})", "ub:targets")],
        [_btn(f"Avto yuborish: {'yoqilgan' if auto else 'oʻchirilgan'}", "ub:auto")],
        [_btn("Akkauntni uzish", "ub:logout")],
    ]
    return text, _kb(rows, back="st:menu")


def targets_view(uid: int):
    rows = db.list_targets(uid)
    text = f"<b>Tanlangan chatlar</b> ({len(rows)}/{MAX_TARGETS})\n\n" + (
        "Olib tashlash uchun ustiga bosing." if rows else "Hozircha chat tanlanmagan."
    )
    kb_rows = [[_btn(r["title"][:40], f"ub:tdel:{r['chat_id']}")] for r in rows]
    return text, _kb(kb_rows)


def picker_view(uid: int, page: int):
    items = _dialogs.get(uid, [])
    pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    chosen = db.target_ids(uid)
    kinds = {"user": "lichka", "group": "guruh", "channel": "kanal"}
    rows = []
    for cid, title, kind in items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]:
        mark = "✓ " if cid in chosen else ""
        rows.append([_btn(f"{mark}{title[:26]} ({kinds[kind]})", f"ub:tg:{cid}:{page}")])
    nav = []
    if page > 0:
        nav.append(_btn("<", f"ub:pick:{page - 1}"))
    nav.append(_btn(f"{page + 1}/{pages}", "ub:noop"))
    if page < pages - 1:
        nav.append(_btn(">", f"ub:pick:{page + 1}"))
    rows.append(nav)
    text = (
        f"<b>Chatlarni tanlash</b> ({len(chosen)}/{MAX_TARGETS})\n\n"
        "Bosib tanlang yoki bekor qiling. Ro'yxatda yo'q chatni "
        "\"Chat qo'shish\" orqali username bilan qo'shing."
    )
    return text, _kb(rows)


async def safe_edit(cb: CallbackQuery, text: str, markup=None) -> None:
    await edit_or_send(cb, text, markup)


async def _delete_quietly(message: Message) -> None:
    try:
        await message.delete()
    except Exception:
        pass


async def load_dialogs(uid: int) -> bool:
    c = await get_client(uid)
    if c is None:
        return False
    items = []
    async for d in c.iter_dialogs(limit=150):
        e = d.entity
        if d.is_user and (getattr(e, "bot", False) or getattr(e, "deleted", False)
                          or getattr(e, "is_self", False)):
            continue
        if not (d.is_user or d.is_group or d.is_channel):
            continue
        kind = "user" if d.is_user else ("group" if d.is_group else "channel")
        items.append((d.id, d.name or str(d.id), kind))
    _dialogs[uid] = items
    return True


# ---------- tugmalar ----------

@router.callback_query(F.data.startswith("ub:"))
async def ub_callback(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    act = parts[1]
    arg = parts[2] if len(parts) > 2 else ""
    toast = None
    view = None

    if act == "noop":
        await cb.answer()
        return

    await state.clear()
    await cleanup_pending(uid)

    reason = unavailable_reason()
    if reason and act != "menu":
        toast = reason
        view = menu_view(uid)

    elif act == "menu":
        view = menu_view(uid)

    elif act == "connect":
        view = (
            "<b>Akkauntni ulash haqida</b>\n\n"
            "• Bot akkauntingizga ulanadi va faqat o'zi yaratgan postlarni siz tanlagan "
            "chatlarga yuboradi. Xabarlaringizni o'qimaydi.\n"
            "• Texnik jihatdan sessiya akkauntga to'liq kirish beradi, shuning uchun u "
            "serverda shifrlangan holda saqlanadi.\n"
            "• Istalgan payt uzishingiz mumkin: bu yerdan yoki Telegram > Sozlamalar > "
            "Qurilmalar orqali \"PUBG Host Bot\" seansini tugatib.\n"
            "• Akkauntga Telegram cheklov qo'yishi mumkin (spam deb hisoblansa). "
            "Faqat o'z turnir chatlaringizga yuboring.\n\n"
            "Davom etasizmi?",
            _kb([[_btn("Roziman, davom etish", "ub:consent")]]),
        )

    elif act == "consent":
        await state.set_state(Login.phone)
        view = (
            "Telefon raqamingizni xalqaro formatda yuboring.\nMasalan: +998901234567",
            _kb([]),
        )

    elif act == "pick":
        ok = await load_dialogs(uid)
        if not ok:
            toast = "Akkauntga ulanib bo'lmadi. Qayta ulang."
            view = menu_view(uid)
        else:
            view = picker_view(uid, int(arg or 0))

    elif act == "tg" and len(parts) > 3:
        cid, page = int(parts[2]), int(parts[3])
        if cid in db.target_ids(uid):
            db.del_target(uid, cid)
        elif len(db.list_targets(uid)) >= MAX_TARGETS:
            toast = f"Eng ko'pi {MAX_TARGETS} ta chat"
        else:
            title = next((t for i, t, _ in _dialogs.get(uid, []) if i == cid), str(cid))
            db.add_target(uid, cid, title)
        view = picker_view(uid, page)

    elif act == "add":
        if await get_client(uid) is None:
            toast = "Akkauntga ulanib bo'lmadi. Qayta ulang."
            view = menu_view(uid)
        else:
            await state.set_state(AddChat.chat)
            view = (
                "Chat username yoki havolasini yuboring.\n"
                "Masalan: @guruh_nomi yoki https://t.me/guruh_nomi\n"
                "Lichka uchun: @username. (Taklif havolasi t.me/+... ishlamaydi, "
                "avval o'sha chatga o'zingiz qo'shiling va ro'yxatdan tanlang.)",
                _kb([]),
            )

    elif act == "targets":
        view = targets_view(uid)

    elif act == "tdel" and arg.lstrip("-").isdigit():
        db.del_target(uid, int(arg))
        view = targets_view(uid)

    elif act == "auto":
        cur = db.get_settings(uid)["autosend"]
        if not cur and not db.list_targets(uid):
            toast = "Avval chat tanlang"
        else:
            db.set_setting(uid, "autosend", 0 if cur else 1)
            toast = "Yoqildi" if not cur else "O'chirildi"
        view = menu_view(uid)

    elif act == "logout":
        view = (
            "Akkaunt uzilsinmi? Tanlangan chatlar ro'yxati ham o'chadi.",
            _kb([[_btn("Ha, uzish", "ub:logoutok")]]),
        )

    elif act == "logoutok":
        c = _clients.pop(uid, None)
        if c is None:
            c = await get_client(uid)
        if c is not None:
            try:
                await c.log_out()
            except Exception:
                logging.exception("log_out xatosi")
                await _quiet_disconnect(c)
        drop_session(uid)
        toast = "Akkaunt uzildi"
        view = menu_view(uid)

    else:
        view = menu_view(uid)

    await safe_edit(cb, *view)
    await cb.answer(toast)


# ---------- ulash oqimi ----------

@router.message(Login.phone, NOT_SPECIAL)
async def got_phone(message: Message, state: FSMContext):
    uid = message.from_user.id
    reason = unavailable_reason()
    if reason:
        await state.clear()
        await message.answer(reason)
        return
    phone = re.sub(r"[^\d+]", "", message.text)
    if not re.fullmatch(r"\+?\d{9,15}", phone):
        await message.answer("Raqam noto'g'ri. Masalan: +998901234567")
        return
    if not phone.startswith("+"):
        phone = "+" + phone
    await _delete_quietly(message)

    for old_uid, p in list(_pending.items()):  # eskirgan seanslarni tozalash
        if time.time() - p["ts"] > PENDING_TTL:
            await cleanup_pending(old_uid)

    client = new_client()
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except errors.PhoneNumberInvalidError:
        await _quiet_disconnect(client)
        await message.answer("Telefon raqam noto'g'ri. Qayta yuboring.")
        return
    except errors.PhoneNumberBannedError:
        await _quiet_disconnect(client)
        await state.clear()
        await message.answer("Bu raqam Telegram tomonidan bloklangan.")
        return
    except errors.FloodWaitError as e:
        await _quiet_disconnect(client)
        await state.clear()
        await message.answer(f"Telegram cheklovi: {e.seconds} soniyadan keyin urinib ko'ring.")
        return
    except errors.ApiIdInvalidError:
        await _quiet_disconnect(client)
        await state.clear()
        await message.answer("API_ID / API_HASH noto'g'ri. Adminga ayting.")
        return
    except Exception:
        logging.exception("kod yuborishda xato")
        await _quiet_disconnect(client)
        await state.clear()
        await message.answer("Kod yuborib bo'lmadi. Keyinroq urinib ko'ring.")
        return

    _pending[uid] = {"client": client, "phone": phone, "hash": sent.phone_code_hash, "ts": time.time()}
    await state.set_state(Login.code)
    await message.answer(
        "Telegram sizga kod yubordi (Telegram ilovasi yoki SMS).\n\n"
        "Kodni <b>bo'shliq bilan</b> yozing: 1 2 3 4 5\n"
        "(Oddiy yozsangiz Telegram kodni bekor qiladi.)",
        reply_markup=_kb([]),
    )


@router.message(Login.code, NOT_SPECIAL)
async def got_code(message: Message, state: FSMContext):
    uid = message.from_user.id
    p = _pending.get(uid)
    await _delete_quietly(message)
    if not p:
        await state.clear()
        await message.answer("Seans tugagan. Sozlamalar > Akkaunt va chatlar orqali qayta boshlang.")
        return
    code = re.sub(r"\D", "", message.text)
    if not code:
        await message.answer("Kodni raqamlar bilan yuboring: 1 2 3 4 5")
        return
    try:
        await p["client"].sign_in(p["phone"], code, phone_code_hash=p["hash"])
    except errors.SessionPasswordNeededError:
        await state.set_state(Login.password)
        await message.answer(
            "Akkauntda ikki bosqichli parol (2FA) bor. Parolni yuboring.\n"
            "Xabar yuborilgach o'chiriladi.",
            reply_markup=_kb([]),
        )
        return
    except errors.PhoneCodeInvalidError:
        await message.answer("Kod noto'g'ri. Qayta yuboring: 1 2 3 4 5")
        return
    except errors.PhoneCodeExpiredError:
        await cleanup_pending(uid)
        await state.clear()
        await message.answer("Kod eskirgan. Qaytadan boshlang.")
        return
    except errors.FloodWaitError as e:
        await cleanup_pending(uid)
        await state.clear()
        await message.answer(f"Telegram cheklovi: {e.seconds} soniyadan keyin urinib ko'ring.")
        return
    except Exception:
        logging.exception("sign_in xatosi")
        await cleanup_pending(uid)
        await state.clear()
        await message.answer("Ulab bo'lmadi. Keyinroq urinib ko'ring.")
        return
    await _finish_login(message, state, uid)


@router.message(Login.password, NOT_SPECIAL)
async def got_password(message: Message, state: FSMContext):
    uid = message.from_user.id
    p = _pending.get(uid)
    password = message.text
    await _delete_quietly(message)
    if not p:
        await state.clear()
        await message.answer("Seans tugagan. Qaytadan boshlang.")
        return
    try:
        await p["client"].sign_in(password=password)
    except errors.PasswordHashInvalidError:
        await message.answer("Parol noto'g'ri. Qayta yuboring.")
        return
    except errors.FloodWaitError as e:
        await cleanup_pending(uid)
        await state.clear()
        await message.answer(f"Telegram cheklovi: {e.seconds} soniyadan keyin urinib ko'ring.")
        return
    except Exception:
        logging.exception("2FA xatosi")
        await cleanup_pending(uid)
        await state.clear()
        await message.answer("Ulab bo'lmadi. Keyinroq urinib ko'ring.")
        return
    await _finish_login(message, state, uid)


async def _finish_login(message: Message, state: FSMContext, uid: int) -> None:
    p = _pending.pop(uid)
    client = p["client"]
    try:
        db.set_session(uid, encrypt(client.session.save()))
        me = await client.get_me()
        await client.get_dialogs(limit=200)
    except Exception:
        logging.exception("sessiyani saqlashda xato")
        await _quiet_disconnect(client)
        await state.clear()
        await message.answer("Sessiyani saqlab bo'lmadi. Qayta urinib ko'ring.")
        return
    old = _clients.pop(uid, None)
    if old is not None:
        await _quiet_disconnect(old)
    _clients[uid] = client
    await state.clear()
    name = escape(utils.get_display_name(me)) if me else ""
    await message.answer(f"Akkaunt ulandi: <b>{name}</b>.\nEndi chatlarni tanlang.")
    text, markup = menu_view(uid)
    await message.answer(text, reply_markup=markup)


# ---------- chatni qo'lda qo'shish ----------

@router.message(AddChat.chat, NOT_SPECIAL)
async def got_chat(message: Message, state: FSMContext):
    uid = message.from_user.id
    if len(db.list_targets(uid)) >= MAX_TARGETS:
        await state.clear()
        await message.answer(f"Eng ko'pi {MAX_TARGETS} ta chat.")
        return
    c = await get_client(uid)
    if c is None:
        await state.clear()
        await message.answer("Akkauntga ulanib bo'lmadi. Qayta ulang.")
        return
    raw = message.text.strip()
    ref = int(raw) if raw.lstrip("-").isdigit() else raw
    try:
        entity = await c.get_entity(ref)
    except Exception:
        await message.answer(
            "Chat topilmadi. Username yoki ochiq havolani tekshiring va qayta yuboring."
        )
        return
    title = utils.get_display_name(entity) or str(raw)
    db.add_target(uid, utils.get_peer_id(entity), title)
    await state.clear()
    await message.answer(f"Qo'shildi: {escape(title)}")
    text, markup = menu_view(uid)
    await message.answer(text, reply_markup=markup)
