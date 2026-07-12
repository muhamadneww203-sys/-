# -*- coding: utf-8 -*-
"""
Telegram File-Share Bot (1 файл, ҳама чиз дар ҳамин ҷо)
=========================================================
Насб:
    pip install aiogram==3.13.1

Танзим:
    Дар поён BOT_TOKEN ва ADMIN_IDS-ро иваз кунед (ё ENV гузоред).

Ҷамъбаст (мутобиқи дархости шумо):
  - Админ файл/мусиқӣ/видео мефиристад -> бот код (1,2,3...) медиҳад ва
    линк месозад: https://t.me/<bot_username>?start=series-<код>
  - Корбар бо ин линк ё бо ворид кардани код файлро мегирад.
  - Пеш аз додани файл, бот обунаи корбарро ба каналҳои ҳатмӣ месанҷад.
  - Ҳар бор баъд аз гирифтани файл, ба корбар реклома (агар фаъол бошад)
    фиристода мешавад ва баъд аз вақти муайяншуда автоматӣ нест мешавад.
  - Админ метавонад паёми умумӣ ба ҳамаи корбарон фиристад (бо вақти
    худ-нобудшавӣ).
  - Ҳар файли нав автоматӣ ба канали танзимшуда бо тугмаи "Скачать" мефиристад.
  - Панели админ: Омор (бо рӯйхат ва имкони нест кардан), Санҷиши обуна
    (илова/нест кардани канал), Реклама, Паём ба корбарон, Канал.
"""

import asyncio
import logging
import sqlite3
import time
from contextlib import closing

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import CommandStart, Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from aiogram.exceptions import TelegramBadRequest

# ============================== ТАНЗИМОТ ===================================

BOT_TOKEN = "8761935615:AAFgthqu3yUj8jMLISmW5W0hzyvI7HpqPsE"        # токени боти шумо
ADMIN_IDS = {8548475549}                       # ID-и админ(ҳо), рақами Telegram
DB_PATH = "bot.db"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("filebot")

router = Router()

# ============================== БАЗАИ МАЪЛУМОТ ==============================

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS files (
                code INTEGER PRIMARY KEY AUTOINCREMENT,
                file_type TEXT NOT NULL,      -- video / audio / document
                file_id TEXT NOT NULL,
                caption TEXT,
                added_at INTEGER,
                downloads INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                joined_at INTEGER,
                downloads INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                title TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ad (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                file_type TEXT,     -- text / photo / video / null
                file_id TEXT,
                text TEXT,
                duration_min INTEGER,
                active INTEGER DEFAULT 0
            )
        """)


def get_setting(key, default=None):
    with closing(db()) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default


def set_setting(key, value):
    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO settings(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def upsert_user(user_id, username):
    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO users(user_id, username, joined_at, downloads) "
            "VALUES(?,?,?,0) ON CONFLICT(user_id) DO NOTHING",
            (user_id, username or "", int(time.time())),
        )


# ============================== КЛАВИАТУРАҲО =================================

def kb(rows):
    return InlineKeyboardMarkup(inline_keyboard=rows)


def user_menu_kb():
    return kb([
        [InlineKeyboardButton(text="🔎 Ворид кардани код", callback_data="search_file")],
    ])


def admin_menu_kb():
    return kb([
        [InlineKeyboardButton(text="📊 Омор", callback_data="admin_stats")],
        [InlineKeyboardButton(text="✅ Санҷиши обуна", callback_data="admin_sub")],
        [InlineKeyboardButton(text="📢 Реклама", callback_data="admin_ads")],
        [InlineKeyboardButton(text="✉️ Паём ба корбарон", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="📺 Канал", callback_data="admin_channel")],
    ])


def back_kb(target="admin_back"):
    return kb([[InlineKeyboardButton(text="🔙 Бозгашт", callback_data=target)]])


def stats_overview_kb():
    return kb([
        [InlineKeyboardButton(text="🎬 Видео", callback_data="stats_cat:video"),
         InlineKeyboardButton(text="🎵 Мусиқӣ", callback_data="stats_cat:audio"),
         InlineKeyboardButton(text="📁 Файл", callback_data="stats_cat:document")],
        [InlineKeyboardButton(text="🔙 Бозгашт", callback_data="admin_back")],
    ])


def sub_menu_kb():
    with closing(db()) as conn:
        chans = conn.execute("SELECT * FROM channels").fetchall()
    rows = []
    for c in chans:
        rows.append([InlineKeyboardButton(
            text=f"❌ {c['title'] or c['chat_id']}", callback_data=f"sub_del:{c['id']}")])
    rows.append([InlineKeyboardButton(text="➕ Илова кардани канал", callback_data="sub_add")])
    rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="admin_back")])
    return kb(rows)


# ============================== FSM ҲОЛАТҲО ===================================

class AdminStates(StatesGroup):
    waiting_channel_add = State()
    waiting_ad_content = State()
    waiting_ad_duration = State()
    waiting_broadcast_content = State()
    waiting_broadcast_duration = State()


class UserStates(StatesGroup):
    waiting_code = State()


class ChannelStates(StatesGroup):
    waiting_post_channel = State()


# ============================== ЁРИРАСОНҲО ===================================

TYPE_LABEL = {"video": "Видео", "audio": "Мусиқӣ", "document": "Файл"}


async def is_subscribed(bot: Bot, user_id: int) -> list:
    """Бознависии рӯйхати каналҳое, ки корбар обуна нашудааст."""
    with closing(db()) as conn:
        chans = conn.execute("SELECT * FROM channels").fetchall()
    missing = []
    for c in chans:
        try:
            member = await bot.get_chat_member(c["chat_id"], user_id)
            if member.status in ("left", "kicked"):
                missing.append(c)
        except TelegramBadRequest:
            missing.append(c)
    return missing


def sub_gate_kb(missing, code=None):
    rows = []
    for c in missing:
        url = c["chat_id"]
        if str(url).startswith("@"):
            url = f"https://t.me/{str(url)[1:]}"
        rows.append([InlineKeyboardButton(text=f"➕ {c['title'] or c['chat_id']}", url=url)])
    cb = f"check_sub:{code}" if code else "check_sub:0"
    rows.append([InlineKeyboardButton(text="✅ Санҷидан", callback_data=cb)])
    return kb(rows)


async def deliver_file(bot: Bot, chat_id: int, code: int) -> bool:
    with closing(db()) as conn, conn:
        row = conn.execute("SELECT * FROM files WHERE code=?", (code,)).fetchone()
        if not row:
            return False
        conn.execute("UPDATE files SET downloads = downloads + 1 WHERE code=?", (code,))
        conn.execute("UPDATE users SET downloads = downloads + 1 WHERE user_id=?", (chat_id,))

    if row["file_type"] == "video":
        await bot.send_video(chat_id, row["file_id"], caption=row["caption"] or "")
    elif row["file_type"] == "audio":
        await bot.send_audio(chat_id, row["file_id"], caption=row["caption"] or "")
    else:
        await bot.send_document(chat_id, row["file_id"], caption=row["caption"] or "")

    await send_ad_if_active(bot, chat_id)
    return True


async def send_ad_if_active(bot: Bot, chat_id: int):
    with closing(db()) as conn:
        ad = conn.execute("SELECT * FROM ad WHERE id=1").fetchone()
    if not ad or not ad["active"]:
        return
    exit_kb = kb([[InlineKeyboardButton(text="✖️ Пӯшидан", callback_data="close_msg")]])
    msg = None
    try:
        if ad["file_type"] == "photo":
            msg = await bot.send_photo(chat_id, ad["file_id"], caption=ad["text"] or "", reply_markup=exit_kb)
        elif ad["file_type"] == "video":
            msg = await bot.send_video(chat_id, ad["file_id"], caption=ad["text"] or "", reply_markup=exit_kb)
        else:
            msg = await bot.send_message(chat_id, ad["text"] or "", reply_markup=exit_kb)
    except Exception as e:
        logger.warning("ad send failed: %s", e)
        return
    if msg and ad["duration_min"]:
        asyncio.create_task(schedule_delete(bot, chat_id, msg.message_id, int(ad["duration_min"]) * 60))


async def schedule_delete(bot: Bot, chat_id: int, message_id: int, delay_sec: int, notify_admin_text: str = None):
    await asyncio.sleep(delay_sec)
    try:
        await bot.delete_message(chat_id, message_id)
    except Exception:
        pass
    if notify_admin_text:
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(admin_id, notify_admin_text)
            except Exception:
                pass


# ============================== /start ======================================

@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject, state: FSMContext, bot: Bot):
    await state.clear()
    upsert_user(message.from_user.id, message.from_user.username)

    payload = command.args  # мисол: series-12
    if payload and payload.startswith("series-"):
        code_str = payload.split("series-", 1)[1]
        if code_str.isdigit():
            code = int(code_str)
            missing = await is_subscribed(bot, message.from_user.id)
            if missing:
                await message.answer(
                    "Барои гирифтани файл аввал ба каналҳои зерин обуна шавед:",
                    reply_markup=sub_gate_kb(missing, code),
                )
                return
            ok = await deliver_file(bot, message.chat.id, code)
            if not ok:
                await message.answer("❗️ Файл бо ин код ёфт нашуд.")
            return

    text = (
        f"Хуш омадед, {message.from_user.first_name}!\n\n"
        "Барои гирифтани файл, мусиқӣ ё видео, тугмаи зеринро зер кунед "
        "ва коди файлро ворид намоед."
    )
    await message.answer(text, reply_markup=user_menu_kb())
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Панели админ:", reply_markup=admin_menu_kb())


@router.callback_query(F.data == "search_file")
async def cb_search_file(call: CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.waiting_code)
    await call.message.answer("Коди файлро ворид кунед (мисол: 12):")
    await call.answer()


@router.message(UserStates.waiting_code)
async def on_code_entered(message: Message, state: FSMContext, bot: Bot):
    await state.clear()
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Лутфан фақат рақами кодро ворид кунед.")
        return
    code = int(message.text.strip())
    missing = await is_subscribed(bot, message.from_user.id)
    if missing:
        await message.answer(
            "Барои гирифтани файл аввал ба каналҳои зерин обуна шавед:",
            reply_markup=sub_gate_kb(missing, code),
        )
        return
    ok = await deliver_file(bot, message.chat.id, code)
    if not ok:
        await message.answer("❗️ Файл бо ин код ёфт нашуд.")


@router.callback_query(F.data.startswith("check_sub:"))
async def cb_check_sub(call: CallbackQuery, bot: Bot):
    code = call.data.split(":", 1)[1]
    missing = await is_subscribed(bot, call.from_user.id)
    if missing:
        await call.answer("Шумо ҳанӯз ба ҳамаи каналҳо обуна нашудаед.", show_alert=True)
        return
    await call.answer("✅ Тасдиқ шуд!")
    if code and code != "0":
        ok = await deliver_file(bot, call.message.chat.id, int(code))
        if not ok:
            await call.message.answer("❗️ Файл бо ин код ёфт нашуд.")
    try:
        await call.message.delete()
    except Exception:
        pass


@router.callback_query(F.data == "close_msg")
async def cb_close_msg(call: CallbackQuery):
    try:
        await call.message.delete()
    except Exception:
        pass
    await call.answer()


# ============================== ГУЗОШТАНИ ФАЙЛ АЗ ТАРАФИ АДМИН ===============

@router.message(F.video | F.audio | F.document)
async def on_admin_file(message: Message, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return  # корбарони оддӣ файл фиристода наметавонанд

    if message.video:
        file_type, file_id = "video", message.video.file_id
    elif message.audio:
        file_type, file_id = "audio", message.audio.file_id
    else:
        file_type, file_id = "document", message.document.file_id

    caption = message.caption or ""
    with closing(db()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO files(file_type, file_id, caption, added_at) VALUES(?,?,?,?)",
            (file_type, file_id, caption, int(time.time())),
        )
        code = cur.lastrowid

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=series-{code}"
    await message.reply(
        f"✅ Файл нигоҳ дошта шуд!\nНавъ: {TYPE_LABEL[file_type]}\nКод: {code}\nЛинк: {link}"
    )

    # автопост ба канал
    channel_id = get_setting("post_channel")
    if channel_id:
        post_kb = kb([[InlineKeyboardButton(text="⬇️ Скачать", url=link)]])
        try:
            if file_type == "video":
                await bot.send_video(channel_id, file_id, caption=caption, reply_markup=post_kb)
            elif file_type == "audio":
                await bot.send_audio(channel_id, file_id, caption=caption, reply_markup=post_kb)
            else:
                await bot.send_document(channel_id, file_id, caption=caption, reply_markup=post_kb)
        except Exception as e:
            await message.answer(f"⚠️ Ба канал фиристода нашуд: {e}")


# ============================== ПАНЕЛИ АДМИН: ОМОР ============================

def admin_only(func):
    async def wrapper(event, *args, **kwargs):
        uid = event.from_user.id
        if uid not in ADMIN_IDS:
            if isinstance(event, CallbackQuery):
                await event.answer("Дастрасӣ мавҷуд нест.", show_alert=True)
            return
        return await func(event, *args, **kwargs)
    return wrapper


@router.callback_query(F.data == "admin_back")
@admin_only
async def cb_admin_back(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Панели админ:", reply_markup=admin_menu_kb())
    await call.answer()


@router.callback_query(F.data == "admin_stats")
@admin_only
async def cb_admin_stats(call: CallbackQuery):
    with closing(db()) as conn:
        total_users = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        today_start = int(time.time()) - 24 * 3600
        today_users = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE joined_at >= ?", (today_start,)
        ).fetchone()["c"]
        counts = {}
        for t in ("video", "audio", "document"):
            counts[t] = conn.execute(
                "SELECT COUNT(*) c FROM files WHERE file_type=?", (t,)
            ).fetchone()["c"]
        top = conn.execute(
            "SELECT * FROM files ORDER BY downloads DESC LIMIT 1"
        ).fetchone()

    text = (
        f"📊 Омори умумӣ\n\n"
        f"👤 Ҳамаи корбарон: {total_users}\n"
        f"🆕 Корбарони имрӯза: {today_users}\n\n"
        f"🎬 Видео: {counts['video']}\n"
        f"🎵 Мусиқӣ: {counts['audio']}\n"
        f"📁 Файл: {counts['document']}\n\n"
    )
    if top:
        text += f"🔥 Бисёртар зеркашидашуда: код {top['code']} ({top['downloads']} маротиба)"

    await call.message.edit_text(text, reply_markup=stats_overview_kb())
    await call.answer()


@router.callback_query(F.data.startswith("stats_cat:"))
@admin_only
async def cb_stats_cat(call: CallbackQuery):
    cat = call.data.split(":", 1)[1]
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT * FROM files WHERE file_type=? ORDER BY code", (cat,)
        ).fetchall()

    if not rows:
        await call.message.edit_text(
            f"Рӯйхати {TYPE_LABEL[cat]} холист.", reply_markup=back_kb("admin_stats")
        )
        await call.answer()
        return

    kb_rows = []
    for r in rows:
        kb_rows.append([InlineKeyboardButton(
            text=f"№{r['code']} | ⬇️{r['downloads']}",
            callback_data=f"noop")])
        kb_rows.append([InlineKeyboardButton(
            text=f"🗑 Нест кардани №{r['code']}", callback_data=f"stats_del:{r['code']}")])
    kb_rows.append([InlineKeyboardButton(
        text="🗑 Нест кардани ҳама", callback_data=f"stats_delall:{cat}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="admin_stats")])

    await call.message.edit_text(f"Рӯйхати: {TYPE_LABEL[cat]}", reply_markup=kb(kb_rows))
    await call.answer()


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


@router.callback_query(F.data.startswith("stats_del:"))
@admin_only
async def cb_stats_del(call: CallbackQuery):
    code = int(call.data.split(":", 1)[1])
    with closing(db()) as conn, conn:
        row = conn.execute("SELECT file_type FROM files WHERE code=?", (code,)).fetchone()
        conn.execute("DELETE FROM files WHERE code=?", (code,))
    await call.answer("Нест шуд ✅")
    if row:
        await cb_stats_cat_reload(call, row["file_type"])


async def cb_stats_cat_reload(call: CallbackQuery, cat: str):
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT * FROM files WHERE file_type=? ORDER BY code", (cat,)
        ).fetchall()
    if not rows:
        await call.message.edit_text(
            f"Рӯйхати {TYPE_LABEL[cat]} холист.", reply_markup=back_kb("admin_stats")
        )
        return
    kb_rows = []
    for r in rows:
        kb_rows.append([InlineKeyboardButton(
            text=f"№{r['code']} | ⬇️{r['downloads']}", callback_data="noop")])
        kb_rows.append([InlineKeyboardButton(
            text=f"🗑 Нест кардани №{r['code']}", callback_data=f"stats_del:{r['code']}")])
    kb_rows.append([InlineKeyboardButton(
        text="🗑 Нест кардани ҳама", callback_data=f"stats_delall:{cat}")])
    kb_rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="admin_stats")])
    await call.message.edit_text(f"Рӯйхати: {TYPE_LABEL[cat]}", reply_markup=kb(kb_rows))


@router.callback_query(F.data.startswith("stats_delall:"))
@admin_only
async def cb_stats_delall(call: CallbackQuery):
    cat = call.data.split(":", 1)[1]
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM files WHERE file_type=?", (cat,))
    await call.answer("Ҳама нест карда шуд ✅")
    await call.message.edit_text(
        f"Рӯйхати {TYPE_LABEL[cat]} холист.", reply_markup=back_kb("admin_stats")
    )


# ============================== ПАНЕЛИ АДМИН: САНҶИШИ ОБУНА ===================

@router.callback_query(F.data == "admin_sub")
@admin_only
async def cb_admin_sub(call: CallbackQuery):
    await call.message.edit_text("Каналҳои ҳатмии обуна:", reply_markup=sub_menu_kb())
    await call.answer()


@router.callback_query(F.data == "sub_add")
@admin_only
async def cb_sub_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_channel_add)
    await call.message.edit_text(
        "ID ё @username-и каналро фиристед.\n"
        "(Бот бояд дар канал админ бошад.)",
        reply_markup=back_kb("admin_sub"),
    )
    await call.answer()


@router.message(AdminStates.waiting_channel_add)
async def on_channel_add(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_ref = message.text.strip()
    try:
        chat = await bot.get_chat(chat_ref)
        title = chat.title or chat_ref
    except Exception:
        title = chat_ref
    with closing(db()) as conn, conn:
        conn.execute("INSERT INTO channels(chat_id, title) VALUES(?,?)", (chat_ref, title))
    await state.clear()
    await message.answer("✅ Канал илова шуд.", reply_markup=sub_menu_kb())


@router.callback_query(F.data.startswith("sub_del:"))
@admin_only
async def cb_sub_del(call: CallbackQuery):
    cid = int(call.data.split(":", 1)[1])
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM channels WHERE id=?", (cid,))
    await call.answer("Нест шуд ✅")
    await call.message.edit_text("Каналҳои ҳатмии обуна:", reply_markup=sub_menu_kb())


# ============================== ПАНЕЛИ АДМИН: РЕКЛАМА =========================

@router.callback_query(F.data == "admin_ads")
@admin_only
async def cb_admin_ads(call: CallbackQuery):
    with closing(db()) as conn:
        ad = conn.execute("SELECT * FROM ad WHERE id=1").fetchone()
    status = "фаъол ✅" if ad and ad["active"] else "хомӯш ⛔️"
    text = f"Ҳолати реклама: {status}\n\nБарои иваз кардан, реклама (матн/расм/видео) фиристед."
    rows = [[InlineKeyboardButton(text="➕ Иловаи реклама", callback_data="ads_add")]]
    if ad and ad["active"]:
        rows.append([InlineKeyboardButton(text="⛔️ Хомӯш кардани реклама", callback_data="ads_off")])
    rows.append([InlineKeyboardButton(text="🔙 Бозгашт", callback_data="admin_back")])
    await call.message.edit_text(text, reply_markup=kb(rows))
    await call.answer()


@router.callback_query(F.data == "ads_off")
@admin_only
async def cb_ads_off(call: CallbackQuery):
    with closing(db()) as conn, conn:
        conn.execute("UPDATE ad SET active=0 WHERE id=1")
    await call.answer("Реклама хомӯш карда шуд.")
    await cb_admin_ads(call)


@router.callback_query(F.data == "ads_add")
@admin_only
async def cb_ads_add(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_ad_content)
    await call.message.edit_text(
        "Матни реклама (ё расм/видео бо матн)-ро фиристед:",
        reply_markup=back_kb("admin_ads"),
    )
    await call.answer()


@router.message(AdminStates.waiting_ad_content)
async def on_ad_content(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if message.photo:
        await state.update_data(file_type="photo", file_id=message.photo[-1].file_id, text=message.caption or "")
    elif message.video:
        await state.update_data(file_type="video", file_id=message.video.file_id, text=message.caption or "")
    else:
        await state.update_data(file_type="text", file_id=None, text=message.text or "")
    await state.set_state(AdminStates.waiting_ad_duration)
    await message.answer("Реклама чанд дақиқа намоён бошад? (рақам фиристед, мисол: 30)")


@router.message(AdminStates.waiting_ad_duration)
async def on_ad_duration(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Лутфан фақат рақам фиристед.")
        return
    data = await state.get_data()
    duration = int(message.text.strip())
    with closing(db()) as conn, conn:
        conn.execute(
            "INSERT INTO ad(id, file_type, file_id, text, duration_min, active) "
            "VALUES(1,?,?,?,?,1) "
            "ON CONFLICT(id) DO UPDATE SET file_type=excluded.file_type, "
            "file_id=excluded.file_id, text=excluded.text, "
            "duration_min=excluded.duration_min, active=1",
            (data.get("file_type"), data.get("file_id"), data.get("text"), duration),
        )
    await state.clear()
    await message.answer("✅ Реклама фаъол шуд. Ба ҳар корбар баъд аз гирифтани файл фиристода мешавад.")


# ============================== ПАНЕЛИ АДМИН: ПАЁМ БА КОРБАРОН =================

@router.callback_query(F.data == "admin_broadcast")
@admin_only
async def cb_admin_broadcast(call: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_broadcast_content)
    await call.message.edit_text(
        "Паёмеро, ки мехоҳед ба ҳамаи корбарон фиристед, нависед:",
        reply_markup=back_kb("admin_back"),
    )
    await call.answer()


@router.message(AdminStates.waiting_broadcast_content)
async def on_broadcast_content(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        return
    await state.update_data(text=message.text or message.caption or "")
    await state.set_state(AdminStates.waiting_broadcast_duration)
    await message.answer("Паём баъд аз чанд дақиқа автоматӣ нест шавад? (рақам фиристед)")


@router.message(AdminStates.waiting_broadcast_duration)
async def on_broadcast_duration(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    if not message.text or not message.text.strip().isdigit():
        await message.answer("Лутфан фақат рақам фиристед.")
        return
    duration = int(message.text.strip())
    data = await state.get_data()
    text = data.get("text", "")
    await state.clear()

    with closing(db()) as conn:
        users = conn.execute("SELECT user_id FROM users").fetchall()

    exit_kb = kb([[InlineKeyboardButton(text="✖️ Пӯшидан", callback_data="close_msg")]])
    sent = 0
    for u in users:
        try:
            msg = await bot.send_message(u["user_id"], text, reply_markup=exit_kb)
            sent += 1
            asyncio.create_task(schedule_delete(bot, u["user_id"], msg.message_id, duration * 60))
        except Exception:
            continue

    await message.answer(f"✅ Паём ба {sent} корбар фиристода шуд.")


# ============================== ПАНЕЛИ АДМИН: КАНАЛ ============================

@router.callback_query(F.data == "admin_channel")
@admin_only
async def cb_admin_channel(call: CallbackQuery, state: FSMContext):
    current = get_setting("post_channel", "танзим нашудааст")
    await state.set_state(ChannelStates.waiting_post_channel)
    await call.message.edit_text(
        f"Канали ҳозира барои автопост: {current}\n\n"
        "ID ё @username-и канали навро фиристед (бот бояд дар он админ бошад):",
        reply_markup=back_kb("admin_back"),
    )
    await call.answer()


@router.message(ChannelStates.waiting_post_channel)
async def on_post_channel_set(message: Message, state: FSMContext, bot: Bot):
    if message.from_user.id not in ADMIN_IDS:
        return
    chat_ref = message.text.strip()
    try:
        chat = await bot.get_chat(chat_ref)
        chat_id_to_store = chat.id
    except Exception:
        chat_id_to_store = chat_ref
    set_setting("post_channel", chat_id_to_store)
    await state.clear()
    await message.answer(f"✅ Канал барои автопост танзим шуд: {chat_ref}", reply_markup=admin_menu_kb())


# ============================== ПАЁМИ ИСТИҚБОЛ БАРОИ КОРБАРОНИ НАВ ============
# (Аллакай дар cmd_start амалӣ шудааст — ҳангоми аввалин /start.)


# ============================== РОҲАНДОЗӢ ======================================

async def main():
    init_db()
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)
    logger.info("Бот оғоз шуд...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
