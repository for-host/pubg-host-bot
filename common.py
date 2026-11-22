"""Hamma modullar uchun umumiy narsalar: sozlamalar, tugma matnlari, yordamchilar."""
import os
import re
from html import escape, unescape
from zoneinfo import ZoneInfo

from aiogram import F
from aiogram.filters import Filter
from aiogram.types import (
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)

import db


def load_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()

TZ = ZoneInfo(os.getenv("TZ_NAME", "Asia/Tashkent"))
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").replace(" ", "").split(",") if x]

# ---------- tugma matnlari ----------
BTN_CHANNELS = "Turnir kanallar"
BTN_GUIDE = "Hostlar uchun qo'llanma"
BTN_CONTACT = "Admin / host bilan bog'lanish"
BTN_CREATE = "Post yaratish"
BTN_SETTINGS = "Sozlamalar"
BTN_ADMIN = "Admin panel"
BTN_REDO = "Oldingisini qayta"
BTN_CANCEL = "Bekor qilish"

MENU_TEXTS = [
    BTN_CHANNELS, BTN_GUIDE, BTN_CONTACT, BTN_CREATE,
    BTN_SETTINGS, BTN_ADMIN, BTN_REDO, BTN_CANCEL,
]

# Holat (FSM) ichida menyu tugmalari va buyruqlar qiymat sifatida qabul qilinmasin
NOT_SPECIAL = F.text & ~F.text.in_(MENU_TEXTS) & ~F.text.startswith("/")

DEFAULT_TEXTS = {
    "welcome": "Assalomu alaykum! Pastdagi tugmalardan birini tanlang.",
    "channels": "Turnir kanallari:",
    "guide": "Host bo'lish uchun admin bilan bog'laning.",
    "contact": "Admin bilan bog'lanish uchun hozircha ma'lumot kiritilmagan.",
}

# Admin yoqib/o'chira oladigan oddiy foydalanuvchi tugmalari
PUBLIC_BUTTONS = [
    ("btn_channels", BTN_CHANNELS),
    ("btn_guide", BTN_GUIDE),
    ("btn_contact", BTN_CONTACT),
]


# ---------- rollar ----------

def is_super(uid: int) -> bool:
    return uid in ADMIN_IDS


def is_admin(uid: int) -> bool:
    return is_super(uid) or db.get_role(uid) == "admin"


def is_host(uid: int) -> bool:
    return is_admin(uid) or db.get_role(uid) == "host"


class HostOnly(Filter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user) and is_host(event.from_user.id)


class AdminOnly(Filter):
    async def __call__(self, event) -> bool:
        return bool(event.from_user) and is_admin(event.from_user.id)


# ---------- klaviaturalar ----------

def main_kb(uid: int):
    rows = []
    if is_host(uid):
        rows.append([KeyboardButton(text=BTN_CREATE), KeyboardButton(text=BTN_SETTINGS)])
    pub = [KeyboardButton(text=t) for key, t in PUBLIC_BUTTONS[:2] if db.get_flag(key)]
    if pub:
        rows.append(pub)
    if db.get_flag("btn_contact"):
        rows.append([KeyboardButton(text=BTN_CONTACT)])
    if is_admin(uid):
        rows.append([KeyboardButton(text=BTN_ADMIN)])
    if not rows:
        return ReplyKeyboardRemove()
    return ReplyKeyboardMarkup(keyboard=rows, resize_keyboard=True)


def flow_kb() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BTN_REDO)], [KeyboardButton(text=BTN_CANCEL)]],
        resize_keyboard=True,
    )


# ---------- post qolipi ----------

TEMPLATE_VARS = ("n", "map", "id", "pw", "time", "contact", "note")
SAMPLE = {
    "n": 1, "map": "Erangel", "id": "1234567", "pw": "g1",
    "time": "19:24", "contact": "@host", "note": "Ogohlantirish matni",
}


def vars_help() -> str:
    return ", ".join("{" + v + "}" for v in TEMPLATE_VARS)


def validate_template(t: str):
    """Xato bo'lsa matn, bo'lmasa None qaytaradi."""
    if "{id}" not in t:
        return "Qolipda {id} bo'lishi shart."
    if len(t) > 1000:
        return "Qolip juda uzun (1000 belgigacha)."
    try:
        t.format(**SAMPLE)
    except KeyError as e:
        return "Noma'lum o'zgaruvchi: {" + str(e.args[0]) + "}. Mumkin: " + vars_help()
    except (IndexError, ValueError):
        return "Qolipdagi { } qavslar noto'g'ri. Mumkin: " + vars_help()
    return None


def render_post(s: dict, n: int, map_: str, lobby_id: str, start: str) -> str:
    try:
        template = s["template"]
        # Lobby ID doim monospace bo'lsin. Agar host qolipda <code>{id}</code>
        # deb yozgan bo'lsa, tegni takrorlamaymiz.
        id_value = escape(lobby_id)
        if not re.search(r"<\s*code\s*>\s*\{id\}\s*<\s*/\s*code\s*>", template, re.I):
            id_value = f"<code>{id_value}</code>"
        return template.format(
            n=n,
            map=escape(map_),
            id=id_value,
            pw=escape(f"{s['prefix']}{n}"),
            time=escape(start),
            contact=escape(s["contact"]),
            note=escape(s["note"]),
        )
    except (KeyError, IndexError, ValueError):
        # qolipda xato bo'lsa, standart qolipga qaytamiz
        return render_post({**s, "template": db.DEFAULT_TEMPLATE}, n, map_, lobby_id, start)


def plain(html_text: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", html_text))


def rich(message: Message) -> str:
    """Xabarni HTML ko'rinishida oladi.

    Telegram'da qalin/quote kabi formatlash qilingan bo'lsa shuni, oddiy matn
    bo'lsa (ichida <b> kabi teglar yozilgan bo'lishi mumkin) o'zini qaytaradi.
    """
    if message.entities:
        return message.html_text
    return message.text or ""
