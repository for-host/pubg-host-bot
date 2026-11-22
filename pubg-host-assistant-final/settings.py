"""Host sozlamalari: vaqt farqi, parol, kik, ogohlantirish, kartalar, qolip."""
from html import escape

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import db
from common import (
    BTN_SETTINGS,
    NOT_SPECIAL,
    HostOnly,
    render_post,
    rich,
    validate_template,
    vars_help,
)

router = Router()
router.message.filter(HostOnly())
router.callback_query.filter(HostOnly())

SHIFT_QUICK = (3, 4, 5, 10)
RESETS = {
    "shift": db.DEFAULT_SHIFT,
    "prefix": db.DEFAULT_PREFIX,
    "contact": "",
    "note": "",
    "maps": "",
    "template": "",
}


class SettingEdit(StatesGroup):
    waiting_value = State()


# ---------- ko'rinishlar ----------

def settings_view(uid: int):
    s = db.get_settings(uid)
    sample = render_post(s, 1, s["maps"][0], "1234567", "19:24")
    p = escape(s["prefix"])
    text = (
        "<b>Sozlamalar</b>\n\n"
        f"Vaqt farqi: <b>+{s['shift']}</b> daqiqa (ID yuborilgan vaqtdan)\n"
        f"Parol: <b>{p}1, {p}2, {p}3 ...</b>\n"
        f"Kik va zaddeshka: <b>{escape(s['contact']) or 'yo`q'}</b>\n"
        f"Ogohlantirish: {escape(s['note'])}\n"
        f"Kartalar: {escape(', '.join(s['maps']))}\n\n"
        "<b>Post namunasi:</b>\n\n" + sample
    )
    rows = [
        [
            InlineKeyboardButton(
                text=("✓ " if s["shift"] == n else "") + f"+{n}",
                callback_data=f"st:shift:{n}",
            )
            for n in SHIFT_QUICK
        ],
        [InlineKeyboardButton(text="Boshqa vaqt", callback_data="st:edit:shift")],
        [
            InlineKeyboardButton(text="Parol", callback_data="st:edit:prefix"),
            InlineKeyboardButton(text="Kik / zaddeshka", callback_data="st:edit:contact"),
        ],
        [
            InlineKeyboardButton(text="Ogohlantirish", callback_data="st:edit:note"),
            InlineKeyboardButton(text="Kartalar", callback_data="st:edit:maps"),
        ],
        [InlineKeyboardButton(text="Post qolipini o'zgartirish", callback_data="st:edit:template")],
        [InlineKeyboardButton(text="Akkaunt va chatlar (avto yuborish)", callback_data="ub:menu")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def prompt_for(field: str, uid: int) -> str:
    s = db.get_settings(uid)
    if field == "shift":
        return (
            "Start time ID yuborilgan vaqtdan necha daqiqa keyin bo'lsin?\n"
            "Son yuboring (0 dan 120 gacha). Masalan: 7\n\n"
            f"Hozir: +{s['shift']}"
        )
    if field == "prefix":
        return (
            "Parol boshlanishini yuboring. Masalan: g  (g1, g2, g3...)\n\n"
            f"Hozir: {escape(s['prefix'])}"
        )
    if field == "contact":
        return (
            "Kik va zaddeshka uchun username yuboring. Masalan: @flowww2o\n\n"
            f"Hozir: {escape(s['contact']) or 'yo`q'}"
        )
    if field == "note":
        return (
            "Post oxiridagi ogohlantirish matnini yuboring (300 belgigacha).\n\n"
            f"Hozir: {escape(s['note'])}"
        )
    if field == "maps":
        return (
            "5 ta karta nomini vergul bilan yuboring.\n"
            "Masalan: Erangel, Miramar, Rondo, Erangel, Miramar\n"
            "3 yoki 4 karta tanlansa, birinchilari olinadi.\n\n"
            f"Hozir: {escape(', '.join(s['maps']))}"
        )
    return (
        "Post qolipini yuboring. Telegram'ning qalin, quote kabi formatlashi ishlaydi.\n\n"
        "O'zgaruvchilar: " + vars_help() + "\n"
        "{n} karta raqami, {map} xarita, {id} lobby ID, {pw} parol, {time} start vaqti, "
        "{contact} kik/zaddeshka, {note} ogohlantirish. {id} bo'lishi shart.\n\n"
        "Hozirgi qolip:\n<pre>" + escape(s["template"]) + "</pre>"
    )


def edit_kb(field: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Standartga qaytarish", callback_data=f"st:reset:{field}")],
            [InlineKeyboardButton(text="Bekor qilish", callback_data="st:cancel")],
        ]
    )


async def safe_edit(cb: CallbackQuery, text: str, kb=None) -> None:
    try:
        await cb.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        pass  # "message is not modified" va shunga o'xshash holatlar


# ---------- kirish ----------

@router.message(F.text == BTN_SETTINGS)
async def open_settings(message: Message, state: FSMContext):
    await state.clear()
    u = message.from_user
    db.upsert_user(u.id, u.username, u.full_name)
    text, kb = settings_view(u.id)
    await message.answer(text, reply_markup=kb)


@router.message(Command("vaqt"))
async def cmd_shift(message: Message, command: CommandObject):
    u = message.from_user
    arg = (command.args or "").strip().lstrip("+")
    if not arg.isdigit() or not (0 <= int(arg) <= 120):
        await message.answer("Masalan: /vaqt 5  (0 dan 120 gacha daqiqa)")
        return
    db.upsert_user(u.id, u.username, u.full_name)
    db.set_setting(u.id, "shift", int(arg))
    await message.answer(f"Start time endi ID yuborilgan vaqtdan +{arg} daqiqa.")


# ---------- tugmalar ----------

@router.callback_query(F.data.startswith("st:"))
async def st_callback(cb: CallbackQuery, state: FSMContext):
    uid = cb.from_user.id
    parts = cb.data.split(":")
    act = parts[1]
    toast = None

    if act == "shift":
        n = int(parts[2])
        if 0 <= n <= 120:
            db.set_setting(uid, "shift", n)
            toast = f"Saqlandi: +{n}"
        await state.clear()
        text, kb = settings_view(uid)
        await safe_edit(cb, text, kb)

    elif act == "edit" and parts[2] in RESETS:
        field = parts[2]
        await state.set_state(SettingEdit.waiting_value)
        await state.update_data(field=field)
        await safe_edit(cb, prompt_for(field, uid), edit_kb(field))

    elif act == "reset" and parts[2] in RESETS:
        db.set_setting(uid, parts[2], RESETS[parts[2]])
        await state.clear()
        toast = "Standartga qaytarildi"
        text, kb = settings_view(uid)
        await safe_edit(cb, text, kb)

    else:  # cancel / menu
        await state.clear()
        text, kb = settings_view(uid)
        await safe_edit(cb, text, kb)

    await cb.answer(toast)


# ---------- qiymat qabul qilish ----------

@router.message(SettingEdit.waiting_value, NOT_SPECIAL)
async def got_value(message: Message, state: FSMContext):
    uid = message.from_user.id
    field = (await state.get_data()).get("field")
    raw = (message.text or "").strip()
    value = None
    err = None

    if field == "shift":
        t = raw.lstrip("+")
        if t.isdigit() and 0 <= int(t) <= 120:
            value = int(t)
        else:
            err = "Faqat 0 dan 120 gacha son yuboring. Masalan: 7"

    elif field == "prefix":
        if 1 <= len(raw) <= 10 and " " not in raw:
            value = raw
        else:
            err = "Parol boshlanishi 1-10 belgi, bo'shliqsiz bo'lsin. Masalan: g"

    elif field == "contact":
        if 1 <= len(raw) <= 64:
            value = raw
        else:
            err = "Username 1-64 belgi bo'lsin. Masalan: @flowww2o"

    elif field == "note":
        if 1 <= len(raw) <= 300:
            value = raw
        else:
            err = "Matn 1-300 belgi bo'lsin."

    elif field == "maps":
        names = [x.strip() for x in raw.split(",")]
        if len(names) == 5 and all(2 <= len(x) <= 20 for x in names):
            value = ", ".join(names)
        else:
            err = (
                "Aynan 5 ta nom vergul bilan yuboring.\n"
                "Masalan: Erangel, Miramar, Rondo, Erangel, Miramar"
            )

    elif field == "template":
        t = rich(message).strip()
        err = validate_template(t)
        if err is None:
            s = db.get_settings(uid)
            preview = render_post({**s, "template": t}, 1, s["maps"][0], "1234567", "19:24")
            try:
                await message.answer("Namuna:\n\n" + preview)
                value = t
            except TelegramBadRequest as e:
                err = f"Formatlashda xato (HTML teglar noto'g'ri bo'lishi mumkin): {escape(str(e))}"

    else:
        await state.clear()
        return

    if err:
        await message.answer(err + "\n\nQayta yuboring yoki Bekor qilishni bosing.")
        return

    db.set_setting(uid, field, value)
    await state.clear()
    await message.answer("Saqlandi.")
    text, kb = settings_view(uid)
    await message.answer(text, reply_markup=kb)
