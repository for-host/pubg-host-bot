import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).with_name("bot.db")
conn = sqlite3.connect(DB_PATH, check_same_thread=False)
conn.row_factory = sqlite3.Row

DEFAULT_NOTE = "Har ehtimolga qarshi screenshot olib qo‘ying !"
DEFAULT_TEMPLATE = (
    "<b>Match {n} - {map}</b>\n"
    "ID: <code>{id}</code>\n"
    "P: {pw}\n"
    "Start time: {time}\n"
    "Kik va zaddeshka uchun: {contact}\n"
    "<blockquote><i>{note}</i></blockquote>"
)
DEFAULT_SHIFT = 4
DEFAULT_PREFIX = "g"
DEFAULT_MAPS = ["Erangel", "Miramar", "Rondo", "Erangel", "Miramar"]

# Host sozlamalari (settings bo'limida o'zgartiriladi)
SETTING_KEYS = {"shift", "prefix", "contact", "note", "template", "maps", "autosend"}


def init() -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users(
            id INTEGER PRIMARY KEY,
            role TEXT NOT NULL DEFAULT 'user',
            username TEXT,
            name TEXT,
            shift INTEGER NOT NULL DEFAULT 4,
            prefix TEXT NOT NULL DEFAULT 'g',
            contact TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            template TEXT NOT NULL DEFAULT '',
            maps TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            session TEXT NOT NULL DEFAULT '',
            autosend INTEGER NOT NULL DEFAULT 0,
            joined TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS texts(
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS posts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            map TEXT,
            lobby_id TEXT,
            created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS share_posts(
            share_key TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            lobby_id TEXT NOT NULL,
            html_text TEXT NOT NULL,
            created TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS channels(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            url TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS targets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            UNIQUE(user_id, chat_id)
        );
        """
    )
    # 1-bosqichda yaratilgan bazani yangilash
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
    if "maps" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN maps TEXT NOT NULL DEFAULT ''")
    if "active" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "session" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN session TEXT NOT NULL DEFAULT ''")
    if "autosend" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN autosend INTEGER NOT NULL DEFAULT 0")
    conn.commit()


# ---------- foydalanuvchilar ----------

def upsert_user(uid: int, username: str | None, name: str | None) -> None:
    conn.execute(
        """
        INSERT INTO users(id, username, name) VALUES(?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            username=excluded.username, name=excluded.name, active=1
        """,
        (uid, username, name),
    )
    conn.commit()


def get_role(uid: int) -> str:
    row = conn.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
    return row["role"] if row else "user"


def set_role(uid: int, role: str) -> None:
    conn.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (uid,))
    conn.execute("UPDATE users SET role=? WHERE id=?", (role, uid))
    conn.commit()


def list_users(role: str):
    return conn.execute(
        "SELECT id, username, name FROM users WHERE role=? ORDER BY joined, id", (role,)
    ).fetchall()


def get_user(uid: int):
    return conn.execute(
        "SELECT id, username, name, role FROM users WHERE id=?", (uid,)
    ).fetchone()


def get_username(uid: int) -> str | None:
    row = conn.execute("SELECT username FROM users WHERE id=?", (uid,)).fetchone()
    return row["username"] if row else None


def find_by_username(username: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM users WHERE lower(username)=lower(?)", (username.lstrip("@"),)
    ).fetchone()
    return row["id"] if row else None


def mark_inactive(uid: int) -> None:
    conn.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    conn.commit()


def broadcast_ids(audience: str) -> list[int]:
    if audience == "hosts":
        sql = "SELECT id FROM users WHERE active=1 AND role IN ('host','admin')"
    else:
        sql = "SELECT id FROM users WHERE active=1"
    return [r["id"] for r in conn.execute(sql)]


# ---------- host sozlamalari ----------

def _parse_maps(raw: str) -> list[str]:
    items = [x.strip() for x in (raw or "").split(",") if x.strip()]
    return items if len(items) == 5 else list(DEFAULT_MAPS)


def get_settings(uid: int) -> dict:
    row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        return {
            "shift": DEFAULT_SHIFT,
            "prefix": DEFAULT_PREFIX,
            "contact": "",
            "note": DEFAULT_NOTE,
            "template": DEFAULT_TEMPLATE,
            "maps": list(DEFAULT_MAPS),
            "autosend": 0,
        }
    contact = row["contact"] or (f"@{row['username']}" if row["username"] else "")
    return {
        "shift": row["shift"],
        "prefix": row["prefix"],
        "contact": contact,
        "note": row["note"] or DEFAULT_NOTE,
        "template": row["template"] or DEFAULT_TEMPLATE,
        "maps": _parse_maps(row["maps"]),
        "autosend": row["autosend"],
    }


def set_setting(uid: int, key: str, value) -> None:
    if key not in SETTING_KEYS:
        raise ValueError(f"noma'lum sozlama: {key}")
    conn.execute(f"UPDATE users SET {key}=? WHERE id=?", (value, uid))
    conn.commit()


# ---------- matnlar va menyu tugmalari (admin o'zgartiradi) ----------

def get_text(key: str, default: str) -> str:
    row = conn.execute("SELECT value FROM texts WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_text(key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO texts(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def delete_text(key: str) -> None:
    conn.execute("DELETE FROM texts WHERE key=?", (key,))
    conn.commit()


def get_flag(key: str) -> bool:
    return get_text(key, "1") != "0"


def set_flag(key: str, on: bool) -> None:
    set_text(key, "1" if on else "0")


# ---------- kanallar ----------

def add_channel(title: str, url: str) -> None:
    conn.execute("INSERT INTO channels(title, url) VALUES(?, ?)", (title, url))
    conn.commit()


def list_channels():
    return conn.execute("SELECT id, title, url FROM channels ORDER BY id").fetchall()


def get_channel(cid: int):
    return conn.execute("SELECT id, title, url FROM channels WHERE id=?", (cid,)).fetchone()


def del_channel(cid: int) -> None:
    conn.execute("DELETE FROM channels WHERE id=?", (cid,))
    conn.commit()


# ---------- ulangan akkaunt (shifrlangan sessiya) va yuborish chatlari ----------

def get_session(uid: int) -> str:
    row = conn.execute("SELECT session FROM users WHERE id=?", (uid,)).fetchone()
    return row["session"] if row else ""


def set_session(uid: int, encrypted: str) -> None:
    conn.execute("INSERT OR IGNORE INTO users(id) VALUES(?)", (uid,))
    conn.execute("UPDATE users SET session=? WHERE id=?", (encrypted, uid))
    conn.commit()


def clear_session(uid: int) -> None:
    """Sessiyani, avto yuborishni va tanlangan chatlarni o'chiradi."""
    conn.execute("UPDATE users SET session='', autosend=0 WHERE id=?", (uid,))
    conn.execute("DELETE FROM targets WHERE user_id=?", (uid,))
    conn.commit()


def add_target(uid: int, chat_id: int, title: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO targets(user_id, chat_id, title) VALUES(?, ?, ?)",
        (uid, chat_id, title),
    )
    conn.execute(
        "UPDATE targets SET title=? WHERE user_id=? AND chat_id=?", (title, uid, chat_id)
    )
    conn.commit()


def del_target(uid: int, chat_id: int) -> None:
    conn.execute("DELETE FROM targets WHERE user_id=? AND chat_id=?", (uid, chat_id))
    conn.commit()


def list_targets(uid: int):
    return conn.execute(
        "SELECT chat_id, title FROM targets WHERE user_id=? ORDER BY id", (uid,)
    ).fetchall()


def target_ids(uid: int) -> set[int]:
    return {r["chat_id"] for r in list_targets(uid)}


# ---------- postlar va statistika ----------

def add_post(uid: int, map_: str, lobby_id: str) -> None:
    conn.execute(
        "INSERT INTO posts(user_id, map, lobby_id) VALUES(?, ?, ?)",
        (uid, map_, lobby_id),
    )
    conn.commit()


def stats(today_utc: str, week_utc: str) -> dict:
    """today_utc / week_utc: 'YYYY-MM-DD HH:MM:SS' (UTC) chegaralari."""

    def one(sql: str, *args) -> int:
        return conn.execute(sql, args).fetchone()[0]

    return {
        "users": one("SELECT COUNT(*) FROM users"),
        "active": one("SELECT COUNT(*) FROM users WHERE active=1"),
        "hosts": one("SELECT COUNT(*) FROM users WHERE role='host'"),
        "admins": one("SELECT COUNT(*) FROM users WHERE role='admin'"),
        "connected": one("SELECT COUNT(*) FROM users WHERE session!=''"),
        "new_today": one("SELECT COUNT(*) FROM users WHERE joined>=?", today_utc),
        "posts_today": one("SELECT COUNT(*) FROM posts WHERE created>=?", today_utc),
        "posts_week": one("SELECT COUNT(*) FROM posts WHERE created>=?", week_utc),
        "posts_total": one("SELECT COUNT(*) FROM posts"),
        "top": conn.execute(
            """
            SELECT u.id, u.username, u.name, COUNT(*) AS c
            FROM posts p JOIN users u ON u.id = p.user_id
            WHERE p.created>=?
            GROUP BY u.id ORDER BY c DESC LIMIT 5
            """,
            (week_utc,),
        ).fetchall(),
    }


def save_share(share_key: str, uid: int, lobby_id: str, html_text: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO share_posts(share_key, user_id, lobby_id, html_text) VALUES(?, ?, ?, ?)",
        (share_key, uid, lobby_id, html_text),
    )
    conn.commit()

def get_share(share_key: str):
    return conn.execute(
        "SELECT user_id, lobby_id, html_text FROM share_posts WHERE share_key=?",
        (share_key,),
    ).fetchone()
