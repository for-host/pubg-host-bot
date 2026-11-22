"""Admin panel: statistika, hostlar, adminlar, kanallar, xabar yuborish, matnlar."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from html import escape

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

import db
from common import (
    BTN_ADMIN,
    DEFAULT_TEXTS,
    NOT_SPECIAL,
    PUBLIC_BUTTONS,
    TZ,
    ADMIN_IDS,
    AdminOnly,
    is_admin,
    is_super,
    main_kb,
    rich,
)

router = Router()
router.message.filter(AdminOnly())
router.callback_query.filter(AdminOnly())

TEXT_LABELS = {
    "welcome": "Salomlashuv matni",
    "channels": "Kanallar sarlavhasi",
    "guide": "Hostlar uchun qo'llanma",
    "contact": "Bog'lanish matni",
}

_tasks: set = set()


class AdminInput(StatesGroup):
    host = State()
    admin = State()
    channel = State()
    broadcast = State()
    text = State()


# ---------- yordamchilar ----------

def label(r) -> str:
    if r["username"]:
        return "@" + r["username"]
    return r["name"] or str(r["id"])


def build(items, width: int = 1, back: str | None = "adm:menu"):
    b = InlineKeyboardBuilder()
    for text, data in items:
        b.button(text=text, callback_data=data)
    if back:
        b.button(text="Orqaga", callback_data=back)
    b.adjust(width)
    return b.as_markup()


def parse_target(raw: str):
    t = (raw or "").strip()
    if t.lstrip("-").isdigit():
        return int(t)
    if t and t.lstrip("@").replace("_", "").isalnum():
        return db.find_by_username(t)
    return None


def parse_channel(raw: str):
    """'Nom | havola' -> (nom, url) yoki xato matni."""
    if "|" not in raw:
        return "Shunday yuboring: Nomi | https://t.me/kanal"
    title, link = (x.strip() for x in raw.split("|", 1))
    if not (1 <= len(title) <= 40):
        return "Nom 1-40 belgi bo'lsin."
    if link.startswith("@"):
        link = "https://t.me/" + link[1:]
    elif link.startswith("t.me/"):
        link = "https://" + link
    if not link.startswith(("https://t.me/", "http://t.me/")) or len(link) < 16:
        return "Havola https://t.me/... yoki @username ko'rinishida bo'lsin."
    return title, link


async def notify(bot: Bot, uid: int, text: str) -> bool:
    try:
        await bot.send_message(uid, text, reply_markup=main_kb(uid))
        return True
    except Exception:
        return False


async def safe_edit(cb: CallbackQuery, text: str, markup=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        pass


# ---------- rollarni o'zgartirish (panel va buyruqlar uchun umumiy) ----------

def set_host_role(uid: int):
    if is_admin(uid):
        return False, "Bu foydalanuvchi allaqachon admin."
    db.set_role(uid, "host")
    return True, f"{uid} host qilindi."


def unset_host_role(uid: int):
    if db.get_role(uid) != "host":
        return False, "Bu foydalanuvchi host emas."
    db.set_role(uid, "user")
    return True, f"{uid} hostlikdan olindi."


def set_admin_role(actor: int, uid: int):
    if not is_super(actor):
        return False, "Admin qo'shish faqat bosh admin uchun."
    db.set_role(uid, "admin")
    return True, f"{uid} admin qilindi."


def unset_admin_role(actor: int, uid: int):
    if not is_super(actor):
        return False, "Adminni olib tashlash faqat bosh admin uchun."
    if is_super(uid):
        return False, "Bosh adminni olib bo'lmaydi."
    if db.get_role(uid) != "admin":
        return False, "Bu foydalanuvchi admin emas."
    db.set_role(uid, "user")
    return True, f"{uid} adminlikdan olindi."


# ---------- ko'rinishlar ----------

def panel_view():
    items = [
        ("Statistika", "adm:stats"),
        ("Hostlar", "adm:hosts"),
        ("Adminlar", "adm:admins"),
        ("Kanallar", "adm:channels"),
        ("Xabar yuborish", "adm:bc"),
        ("Matnlar", "adm:texts"),
        ("Menyu tugmalari", "adm:buttons"),
    ]
    return "<b>Admin panel</b>", build(items, width=2, back=None)


def stats_view():
    now = datetime.now(TZ)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week = today - timedelta(days=6)
    fmt = "%Y-%m-%d %H:%M:%S"
    s = db.stats(
        today.astimezone(timezone.utc).strftime(fmt),
        week.astimezone(timezone.utc).strftime(fmt),
    )
    top = "\n".join(
        f"{i}. {escape(label(r))}: {r['c']}" for i, r in enumerate(s["top"], 1)
    ) or "hali yo'q"
    text = (
        "<b>Statistika</b>\n\n"
        f"Foydalanuvchilar: <b>{s['users']}</b> (faol: {s['active']})\n"
        f"Bugun yangi: {s['new_today']}\n"
        f"Hostlar: {s['hosts']}, adminlar: {s['admins']}\n"
        f"Ulangan akkauntlar: {s['connected']}\n\n"
        "<b>Yaratilgan postlar (kartalar)</b>\n"
        f"Bugun: <b>{s['posts_today']}</b>\n"
        f"Oxirgi 7 kun: <b>{s['posts_week']}</b>\n"
        f"Jami: <b>{s['posts_total']}</b>\n\n"
        f"<b>Eng faol hostlar (7 kun)</b>\n{top}"
    )
    return text, build([("Yangilash", "adm:stats")])


def hosts_view():
    rows = db.list_users("host")
    text = f"<b>Hostlar</b> ({len(rows)})\n\n" + (
        "Olib tashlash uchun ustiga bosing." if rows else "Hozircha host yo'q."
    )
    items = [(f"{label(r)} ({r['id']})", f"adm:hdel:{r['id']}") for r in rows[:40]]
    items.append(("Host qo'shish", "adm:hadd"))
    return text, build(items, back="adm:menu")


def admins_view(actor: int):
    rows = db.list_users("admin")
    sup = []
    for aid in ADMIN_IDS:
        u = db.get_user(aid)
        sup.append(escape(label(u)) + f" ({aid})" if u else str(aid))
    text = (
        "<b>Adminlar</b>\n\nBosh adminlar:\n" + ("\n".join(sup) or "yo'q")
        + f"\n\nQo'shilgan adminlar: {len(rows)}"
    )
    items = []
    if is_super(actor):
        items = [(f"{label(r)} ({r['id']})", f"adm:adel:{r['id']}") for r in rows[:40]]
        items.append(("Admin qo'shish", "adm:aadd"))
        text += "\nOlib tashlash uchun ustiga bosing."
    else:
        text += "\nAdmin qo'shish va olib tashlash faqat bosh admin uchun."
    return text, build(items, back="adm:menu")


def channels_view():
    rows = db.list_channels()
    text = f"<b>Turnir kanallari</b> ({len(rows)})\n\n" + (
        "O'chirish uchun ustiga bosing." if rows else "Hozircha kanal yo'q."
    )
    items = [(r["title"], f"adm:cdel:{r['id']}") for r in rows[:40]]
    items.append(("Kanal qo'shish", "adm:cadd"))
    return text, build(items, back="adm:menu")


def texts_view():
    items = [(name, f"adm:txt:{key}") for key, name in TEXT_LABELS.items()]
    return "<b>Matnlar</b>\n\nQaysi matnni o'zgartirasiz?", build(items, back="adm:menu")


def buttons_view():
    items = []
    for key, name in PUBLIC_BUTTONS:
        state = "yoqilgan" if db.get_flag(key) else "o'chirilgan"
        items.append((f"{name}: {state}", f"adm:tog:{key}"))
    ub = "yoqilgan" if db.get_flag("feature_userbot") else "o'chirilgan"
    items.append((f"Akkaunt ulash funksiyasi: {ub}", "adm:tog:feature_userbot"))
    text = (
        "<b>Menyu tugmalari</b>\n\nOddiy foydalanuvchilarga ko'rinadigan tugmalarni "
        "yoqish yoki o'chirish. Foydalanuvchida o'zgarish /start bosganda yangilanadi.\n\n"
        "Pastdagi tugma hostlarning akkaunt ulash va avto yuborish funksiyasini yoqadi yoki o'chiradi."
    )
    return text, build(items, back="adm:menu")


def confirm_view(text: str, yes: str, no: str):
    return text, build([("Ha", yes), ("Yo'q", no)], width=2, back=None)


# ---------- kirish va buyruqlar ----------

@router.message(F.text == BTN_ADMIN)
async def open_panel(message: Message, state: FSMContext):
    await state.clear()
    text, markup = panel_view()
    await message.answer(text, reply_markup=markup)


async def _cmd_role(message: Message, command: CommandObject, bot: Bot, fn, notify_text=None):
    uid = parse_target(command.args or "")
    if uid is None:
        await message.answer(
            f"Foydalanuvchi ID sini yozing. Masalan: /{command.command} 123456789",
            parse_mode=None,
        )
        return
    ok, msg = fn(uid)
    await message.answer(msg)
    if ok and notify_text and not await notify(bot, uid, notify_text):
        await message.answer("Eslatma: u botga hali /start bosmagan, xabar yetmadi.")


@router.message(Command("addhost"))
async def cmd_addhost(message: Message, command: CommandObject, bot: Bot):
    await _cmd_role(message, command, bot, set_host_role, "Siz host qilib qo'shildingiz.")


@router.message(Command("delhost"))
async def cmd_delhost(message: Message, command: CommandObject, bot: Bot):
    await _cmd_role(message, command, bot, unset_host_role)


@router.message(Command("addadmin"))
async def cmd_addadmin(message: Message, command: CommandObject, bot: Bot):
    actor = message.from_user.id
    await _cmd_role(
        message, command, bot, lambda uid: set_admin_role(actor, uid),
        "Siz admin qilib qo'shildingiz.",
    )


@router.message(Command("deladmin"))
async def cmd_deladmin(message: Message, command: CommandObject, bot: Bot):
    actor = message.from_user.id
    await _cmd_role(message, command, bot, lambda uid: unset_admin_role(actor, uid))


@router.message(Command("hosts"))
async def cmd_hosts(message: Message):
    text, markup = hosts_view()
    await message.answer(text, reply_markup=markup)


@router.message(Command("settext"))
async def cmd_settext(message: Message, command: CommandObject):
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or parts[0] not in DEFAULT_TEXTS:
        await message.answer(
            "Masalan: /settext welcome Assalomu alaykum!\n"
            f"Kalitlar: {', '.join(DEFAULT_TEXTS)}\n"
            "Qulayroq usul: Admin panel > Matnlar.",
            parse_mode=None,
        )
        return
    db.set_text(parts[0], parts[1])
    await message.answer("Saqlandi.")


# ---------- panel tugmalari ----------

@router.callback_query(F.data.startswith("adm:"))
async def adm_callback(cb: CallbackQuery, state: FSMContext, bot: Bot):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    act = parts[1]
    arg = parts[2] if len(parts) > 2 else ""
    toast = None
    await state.clear()

    if act == "stats":
        view = stats_view()
    elif act == "hosts":
        view = hosts_view()
    elif act == "admins":
        view = admins_view(uid)
    elif act == "channels":
        view = channels_view()
    elif act == "texts":
        view = texts_view()
    elif act == "buttons":
        view = buttons_view()

    elif act == "hdel" and arg.lstrip("-").isdigit():
        u = db.get_user(int(arg))
        who = escape(label(u)) if u else arg
        view = confirm_view(f"{who} hostlikdan olib tashlansinmi?", f"adm:hdelok:{arg}", "adm:hosts")
    elif act == "hdelok" and arg.lstrip("-").isdigit():
        toast = unset_host_role(int(arg))[1]
        view = hosts_view()

    elif act == "adel" and arg.lstrip("-").isdigit():
        if not is_super(uid):
            toast = "Faqat bosh admin"
            view = admins_view(uid)
        else:
            u = db.get_user(int(arg))
            who = escape(label(u)) if u else arg
            view = confirm_view(
                f"{who} adminlikdan olib tashlansinmi?", f"adm:adelok:{arg}", "adm:admins"
            )
    elif act == "adelok" and arg.lstrip("-").isdigit():
        toast = unset_admin_role(uid, int(arg))[1]
        view = admins_view(uid)

    elif act == "cdel" and arg.isdigit():
        c = db.get_channel(int(arg))
        name = escape(c["title"]) if c else arg
        view = confirm_view(f"«{name}» kanali o'chirilsinmi?", f"adm:cdelok:{arg}", "adm:channels")
    elif act == "cdelok" and arg.isdigit():
        db.del_channel(int(arg))
        toast = "O'chirildi"
        view = channels_view()

    elif act == "hadd":
        await state.set_state(AdminInput.host)
        view = (
            "Host Telegram ID sini yuboring.\nYoki @username (u botga /start bosgan bo'lsa).",
            build([], back="adm:hosts"),
        )
    elif act == "aadd":
        if not is_super(uid):
            toast = "Faqat bosh admin"
            view = admins_view(uid)
        else:
            await state.set_state(AdminInput.admin)
            view = (
                "Admin Telegram ID sini yuboring.\nYoki @username (u botga /start bosgan bo'lsa).",
                build([], back="adm:admins"),
            )
    elif act == "cadd":
        await state.set_state(AdminInput.channel)
        view = (
            "Kanal nomi va havolasini shunday yuboring:\n\n"
            "Turnir kanali | https://t.me/kanal\nyoki\nTurnir kanali | @kanal",
            build([], back="adm:channels"),
        )

    elif act == "bc":
        await state.set_state(AdminInput.broadcast)
        view = (
            "Hamma foydalanuvchilarga yuboriladigan xabarni yuboring "
            "(matn, rasm, video, formatlash bilan).",
            build([], back="adm:menu"),
        )
    elif act == "bcgo" and len(parts) > 3 and parts[3].isdigit():
        task = asyncio.create_task(do_broadcast(bot, uid, int(parts[3]), arg))
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)
        view = ("Yuborish boshlandi. Tugagach hisobot keladi.", build([], back="adm:menu"))

    elif act == "txt" and arg in TEXT_LABELS:
        await state.set_state(AdminInput.text)
        await state.update_data(key=arg)
        current = db.get_text(arg, DEFAULT_TEXTS[arg])
        view = (
            f"<b>{TEXT_LABELS[arg]}</b>\n\nHozirgi matn:\n{current}\n\n"
            "Yangi matnni yuboring (Telegram formatlash ishlaydi).",
            build([("Standartga qaytarish", f"adm:txtreset:{arg}")], back="adm:texts"),
        )
    elif act == "txtreset" and arg in TEXT_LABELS:
        db.delete_text(arg)
        toast = "Standartga qaytarildi"
        view = texts_view()

    elif act == "tog" and (arg in dict(PUBLIC_BUTTONS) or arg == "feature_userbot"):
        db.set_flag(arg, not db.get_flag(arg))
        view = buttons_view()

    else:
        view = panel_view()

    await safe_edit(cb, *view)
    await cb.answer(toast)


# ---------- qiymat qabul qilish ----------

@router.message(AdminInput.host, NOT_SPECIAL)
async def in_host(message: Message, state: FSMContext, bot: Bot):
    uid = parse_target(message.text)
    if uid is None:
        await message.answer("Topilmadi. ID raqamini yuboring (yoki botga /start bosgan @username).")
        return
    await state.clear()
    ok, msg = set_host_role(uid)
    await message.answer(msg)
    if ok and not await notify(bot, uid, "Siz host qilib qo'shildingiz."):
        await message.answer("Eslatma: u botga hali /start bosmagan, xabar yetmadi.")
    text, markup = hosts_view()
    await message.answer(text, reply_markup=markup)


@router.message(AdminInput.admin, NOT_SPECIAL)
async def in_admin(message: Message, state: FSMContext, bot: Bot):
    uid = parse_target(message.text)
    if uid is None:
        await message.answer("Topilmadi. ID raqamini yuboring (yoki botga /start bosgan @username).")
        return
    await state.clear()
    ok, msg = set_admin_role(message.from_user.id, uid)
    await message.answer(msg)
    if ok and not await notify(bot, uid, "Siz admin qilib qo'shildingiz."):
        await message.answer("Eslatma: u botga hali /start bosmagan, xabar yetmadi.")
    text, markup = admins_view(message.from_user.id)
    await message.answer(text, reply_markup=markup)


@router.message(AdminInput.channel, NOT_SPECIAL)
async def in_channel(message: Message, state: FSMContext):
    res = parse_channel(message.text)
    if isinstance(res, str):
        await message.answer(res)
        return
    await state.clear()
    db.add_channel(*res)
    await message.answer("Kanal qo'shildi.")
    text, markup = channels_view()
    await message.answer(text, reply_markup=markup)


@router.message(AdminInput.text, NOT_SPECIAL)
async def in_text(message: Message, state: FSMContext):
    key = (await state.get_data()).get("key")
    if key not in TEXT_LABELS:
        await state.clear()
        return
    value = rich(message).strip()
    if not value or len(value) > 3000:
        await message.answer("Matn 1-3000 belgi bo'lsin.")
        return
    try:
        await message.answer(value)  # ko'rinishi, formatlash xato bo'lsa shu yerda bilinadi
    except TelegramBadRequest as e:
        await message.answer(f"Formatlashda xato: {escape(str(e))}\nQayta yuboring.")
        return
    db.set_text(key, value)
    await state.clear()
    await message.answer("Saqlandi (yuqoridagi ko'rinishda).")
    text, markup = texts_view()
    await message.answer(text, reply_markup=markup)


@router.message(AdminInput.broadcast)
async def in_broadcast(message: Message, state: FSMContext):
    await state.clear()
    n_all = len(db.broadcast_ids("all"))
    n_hosts = len(db.broadcast_ids("hosts"))
    mid = message.message_id
    markup = build(
        [
            (f"Hammaga ({n_all})", f"adm:bcgo:all:{mid}"),
            (f"Faqat hostlarga ({n_hosts})", f"adm:bcgo:hosts:{mid}"),
            ("Bekor qilish", "adm:menu"),
        ]
    , back=None)
    await message.answer("Yuqoridagi xabar yuboriladi. Kimga?", reply_markup=markup)


async def do_broadcast(bot: Bot, admin_id: int, msg_id: int, audience: str) -> None:
    ids = db.broadcast_ids("hosts" if audience == "hosts" else "all")
    ok = failed = blocked = 0
    for uid in ids:
        for attempt in range(2):
            try:
                await bot.copy_message(uid, admin_id, msg_id)
                ok += 1
                break
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after + 1)
                if attempt == 1:
                    failed += 1
            except TelegramForbiddenError:
                db.mark_inactive(uid)
                blocked += 1
                break
            except Exception:
                logging.exception("broadcast xatosi: %s", uid)
                failed += 1
                break
        await asyncio.sleep(0.05)  # Telegram limiti: sekundiga ~20-30 xabar
    try:
        await bot.send_message(
            admin_id,
            f"Xabar yuborish tugadi.\nYetkazildi: {ok}\nBloklagan yoki o'chirgan: {blocked}\n"
            f"Xato: {failed}",
        )
    except Exception:
        logging.exception("hisobot yuborilmadi")
