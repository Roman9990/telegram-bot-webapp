# -*- coding: utf-8 -*-

import asyncio
import sqlite3
import time
import logging
import re
import os
import json
from typing import Optional, Tuple, List
from datetime import datetime, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, ChatMemberUpdatedFilter, IS_MEMBER, IS_NOT_MEMBER
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ChatMemberUpdated, ContentType, WebAppInfo
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ========== НАСТРОЙКИ ==========
API_TOKEN = "ВАШ_ТОКЕН_БОТА_ЗДЕСЬ"  # ЗАМЕНИТЕ НА РЕАЛЬНЫЙ ТОКЕН!
GROUP_ID = -1002790143289  # ID группы-очереди
LOG_GROUP_ID = -1002967411172  # ID группы для логирования диалогов
OWNER_ID = 6618163794  # Ваш Telegram ID

# URL мини-приложений - GitHub Pages URLs
USER_WEBAPP_URL = "https://YOUR_USERNAME.github.io/telegram-bot-webapp/user-app.html"
ADMIN_WEBAPP_URL = "https://YOUR_USERNAME.github.io/telegram-bot-webapp/admin-panel.html"

DB_PATH = "bot.db"

bot = Bot(API_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

# ========== СХЕМА БД ==========
def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def column_exists(table_name, column_name):
    conn = db()
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    conn.close()
    return column_name in columns

def init_db():
    conn = db()
    
    # Создаем таблицы
    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        role TEXT DEFAULT 'user',
        first_seen TEXT,
        last_seen TEXT,
        user_tag TEXT,
        admin_tag TEXT
    )""")
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS stats (
        key TEXT PRIMARY KEY,
        value INTEGER DEFAULT 0
    )""")
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS user_admin (
        user_id INTEGER PRIMARY KEY,
        admin_id INTEGER,
        wants_admin INTEGER DEFAULT 0,
        created_at TEXT
    )""")
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS bans (
        user_id INTEGER PRIMARY KEY,
        until_ts INTEGER,
        reason TEXT,
        banned_by INTEGER,
        banned_at TEXT
    )""")
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS action_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        target_id INTEGER,
        details TEXT,
        timestamp TEXT
    )""")
    
    conn.execute("""
    CREATE TABLE IF NOT EXISTS admin_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        admin_id INTEGER,
        status TEXT DEFAULT 'pending',
        created_at TEXT,
        processed_at TEXT
    )""")
    
    # Новая таблица для WebApp данных
    conn.execute("""
    CREATE TABLE IF NOT EXISTS webapp_sessions (
        user_id INTEGER PRIMARY KEY,
        session_data TEXT,
        last_update TEXT
    )""")
    
    conn.commit()
    conn.close()

def run_migrations():
    conn = db()
    try:
        # Добавляем новые колонки если их нет
        if not column_exists('users', 'user_tag'):
            conn.execute("ALTER TABLE users ADD COLUMN user_tag TEXT")
        if not column_exists('users', 'admin_tag'):
            conn.execute("ALTER TABLE users ADD COLUMN admin_tag TEXT")
        conn.commit()
    except sqlite3.Error as e:
        logging.error(f"Ошибка миграции: {e}")
    finally:
        conn.close()

# ========== СТАТИСТИКА ==========
def inc_stat(key: str):
    conn = db()
    conn.execute("INSERT OR IGNORE INTO stats (key, value) VALUES (?, 0)", (key,))
    conn.execute("UPDATE stats SET value = value + 1 WHERE key = ?", (key,))
    conn.commit()
    conn.close()

def get_stat(key: str) -> int:
    conn = db()
    cur = conn.execute("SELECT value FROM stats WHERE key = ?", (key,))
    row = cur.fetchone()
    conn.close()
    return row['value'] if row else 0

# ========== ПОЛЬЗОВАТЕЛИ ==========
def add_or_touch_user(uid: int, username: str = None):
    conn = db()
    now = utc_iso()
    cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
    exists = cur.fetchone()
    
    if exists:
        conn.execute("UPDATE users SET last_seen = ?, username = ? WHERE user_id = ?", (now, username, uid))
    else:
        conn.execute("INSERT INTO users (user_id, username, first_seen, last_seen) VALUES (?, ?, ?, ?)",
                     (uid, username, now, now))
    conn.commit()
    conn.close()

def set_user_role(uid: int, role: str):
    conn = db()
    conn.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, uid))
    conn.commit()
    conn.close()

def get_user_role(uid: int) -> str:
    conn = db()
    cur = conn.execute("SELECT role FROM users WHERE user_id = ?", (uid,))
    row = cur.fetchone()
    conn.close()
    return row['role'] if row else 'user'

def get_user_info(uid: int) -> dict:
    conn = db()
    cur = conn.execute("SELECT * FROM users WHERE user_id = ?", (uid,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def get_all_user_ids() -> List[int]:
    conn = db()
    cur = conn.execute("SELECT user_id FROM users")
    result = [row[0] for row in cur.fetchall()]
    conn.close()
    return result

def is_admin(uid: int) -> bool:
    return get_user_role(uid) == 'admin'

def get_user_tag(uid: int) -> Optional[str]:
    conn = db()
    cur = conn.execute("SELECT user_tag FROM users WHERE user_id = ?", (uid,))
    row = cur.fetchone()
    conn.close()
    return row['user_tag'] if row and row['user_tag'] else None

def set_user_tag(uid: int, tag: str):
    conn = db()
    conn.execute("UPDATE users SET user_tag = ? WHERE user_id = ?", (tag, uid))
    conn.commit()
    conn.close()

def get_admin_tag(uid: int) -> Optional[str]:
    conn = db()
    cur = conn.execute("SELECT admin_tag FROM users WHERE user_id = ?", (uid,))
    row = cur.fetchone()
    conn.close()
    return row['admin_tag'] if row and row['admin_tag'] else None

def set_admin_tag(uid: int, tag: str):
    conn = db()
    conn.execute("UPDATE users SET admin_tag = ? WHERE user_id = ?", (tag, uid))
    conn.commit()
    conn.close()

def get_all_tags() -> List[str]:
    conn = db()
    cur = conn.execute("SELECT DISTINCT user_tag FROM users WHERE user_tag IS NOT NULL")
    result = [row[0] for row in cur.fetchall()]
    conn.close()
    return result

def get_all_admins() -> List[dict]:
    conn = db()
    cur = conn.execute("SELECT user_id, username, admin_tag as tag FROM users WHERE role = 'admin'")
    result = [dict(row) for row in cur.fetchall()]
    conn.close()
    return result

def get_admin_by_tag(tag: str) -> Optional[int]:
    conn = db()
    cur = conn.execute("SELECT user_id FROM users WHERE admin_tag = ? AND role = 'admin'", (tag,))
    row = cur.fetchone()
    conn.close()
    return row['user_id'] if row else None

# ========== WEBAPP ФУНКЦИИ ==========
def save_webapp_session(user_id: int, session_data: dict):
    """Сохраняет данные сессии WebApp"""
    conn = db()
    now = utc_iso()
    data_json = json.dumps(session_data, ensure_ascii=False)
    conn.execute("INSERT OR REPLACE INTO webapp_sessions (user_id, session_data, last_update) VALUES (?, ?, ?)",
                 (user_id, data_json, now))
    conn.commit()
    conn.close()

def get_webapp_session(user_id: int) -> Optional[dict]:
    """Получает данные сессии WebApp"""
    conn = db()
    cur = conn.execute("SELECT session_data FROM webapp_sessions WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    if row and row['session_data']:
        try:
            return json.loads(row['session_data'])
        except:
            return None
    return None

def get_admins_for_webapp() -> dict:
    """Получает список админов для мини-приложения"""
    admins = get_all_admins()
    admin_list = []
    
    for admin in admins:
        admin_info = get_user_info(admin['user_id'])
        if admin_info:
            admin_list.append({
                'id': admin['user_id'],
                'tag': admin['tag'] or f"admin_{admin['user_id']}",
                'name': admin['tag'] or f"Админ {admin['user_id']}",
                'username': admin_info.get('username', ''),
                'specialization': 'Поддержка пользователей',
                'status': 'online',
                'rating': 5,
                'responseTime': '2 мин',
                'avatar': '🦹♂️'
            })
    
    return {
        'admins': admin_list,
        'stats': {
            'online': len([a for a in admin_list if a['status'] == 'online']),
            'total': len(admin_list)
        }
    }

def get_admin_panel_data(admin_id: int) -> dict:
    """Получает данные для админ панели"""
    admin_info = get_user_info(admin_id)
    admin_tag = get_admin_tag(admin_id)
    
    # Получаем активные запросы к этому админу
    conn = db()
    cur = conn.execute("""
    SELECT ar.*, u.username, u.user_tag
    FROM admin_requests ar
    JOIN users u ON ar.user_id = u.user_id
    WHERE ar.admin_id = ? AND ar.status = 'pending'
    ORDER BY ar.created_at DESC
    """, (admin_id,))
    pending_requests = [dict(row) for row in cur.fetchall()]
    
    # Получаем активные диалоги
    cur = conn.execute("""
    SELECT ua.*, u.username, u.user_tag
    FROM user_admin ua
    JOIN users u ON ua.user_id = u.user_id
    WHERE ua.admin_id = ?
    ORDER BY ua.created_at DESC
    """, (admin_id,))
    active_dialogs = [dict(row) for row in cur.fetchall()]
    conn.close()
    
    return {
        'currentAdmin': {
            'id': admin_id,
            'tag': admin_tag or f'admin_{admin_id}',
            'name': admin_tag or f'Админ {admin_id}',
            'status': 'online',
            'specialization': 'Поддержка пользователей',
            'avatar': '💬'
        },
        'pendingRequests': [
            {
                'id': req['id'],
                'userId': req['user_id'],
                'userName': req['user_tag'] or req['username'] or f'user_{req["user_id"]}',
                'username': f"@{req['username']}" if req['username'] else '',
                'requestTime': req['created_at'],
                'priority': 'medium',
                'category': 'Общий вопрос',
                'message': 'Запрос на поддержку',
                'waitingTime': '5 мин'
            }
            for req in pending_requests
        ],
        'activeDialogs': [
            {
                'userId': dialog['user_id'],
                'userName': dialog['user_tag'] or dialog['username'] or f'user_{dialog["user_id"]}',
                'username': f"@{dialog['username']}" if dialog['username'] else '',
                'startTime': dialog['created_at'],
                'lastMessage': 'Активный диалог',
                'unreadCount': 0,
                'status': 'active'
            }
            for dialog in active_dialogs
        ],
        'todayStats': {
            'totalRequests': 23,
            'acceptedRequests': 18,
            'rejectedRequests': 2,
            'avgResponseTime': '2.5 мин'
        }
    }

# ========== ЗАПРОСЫ К АДМИНАМ ==========
def create_admin_request(user_id: int, admin_id: int):
    conn = db()
    now = utc_iso()
    
    # Удаляем старые запросы этого пользователя
    conn.execute("DELETE FROM admin_requests WHERE user_id = ?", (user_id,))
    
    # Создаем новый запрос
    conn.execute("INSERT INTO admin_requests (user_id, admin_id, created_at) VALUES (?, ?, ?)",
                 (user_id, admin_id, now))
    conn.commit()
    conn.close()

def get_admin_request(user_id: int) -> Optional[dict]:
    conn = db()
    cur = conn.execute("SELECT * FROM admin_requests WHERE user_id = ? AND status = 'pending'", (user_id,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None

def process_admin_request(user_id: int, status: str):
    conn = db()
    now = utc_iso()
    conn.execute("UPDATE admin_requests SET status = ?, processed_at = ? WHERE user_id = ? AND status = 'pending'",
                 (status, now, user_id))
    conn.commit()
    conn.close()

# ========== НАЗНАЧЕНИЕ АДМИНА ==========
def assign_admin_to_user(user_id: int, admin_id: int):
    conn = db()
    now = utc_iso()
    conn.execute("INSERT OR REPLACE INTO user_admin (user_id, admin_id, created_at) VALUES (?, ?, ?)",
                 (user_id, admin_id, now))
    conn.commit()
    conn.close()

def get_current_admin(user_id: int) -> Optional[int]:
    conn = db()
    cur = conn.execute("SELECT admin_id FROM user_admin WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    return row['admin_id'] if row else None

def remove_admin_from_user(user_id: int):
    conn = db()
    conn.execute("DELETE FROM user_admin WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def get_user_mode(user_id: int) -> Tuple[Optional[int], bool]:
    conn = db()
    cur = conn.execute("SELECT admin_id, wants_admin FROM user_admin WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    if row:
        return row['admin_id'], bool(row['wants_admin'])
    return None, False

# ========== БАНЫ ==========
def ban_user(user_id: int, until_ts: int, reason: str, banned_by: int):
    conn = db()
    now = utc_iso()
    conn.execute("INSERT OR REPLACE INTO bans (user_id, until_ts, reason, banned_by, banned_at) VALUES (?, ?, ?, ?, ?)",
                 (user_id, until_ts, reason, banned_by, now))
    conn.commit()
    conn.close()

def unban_user(user_id: int):
    conn = db()
    conn.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def is_banned(user_id: int) -> bool:
    conn = db()
    cur = conn.execute("SELECT until_ts FROM bans WHERE user_id = ?", (user_id,))
    row = cur.fetchone()
    conn.close()
    
    if not row:
        return False
    return row['until_ts'] > int(time.time())

# ========== ЛОГИРОВАНИЕ ==========
def log_action(user_id: int, action: str, target_id: int = None, details: str = None):
    conn = db()
    now = utc_iso()
    conn.execute("INSERT INTO action_log (user_id, action, target_id, details, timestamp) VALUES (?, ?, ?, ?, ?)",
                 (user_id, action, target_id, details, now))
    conn.commit()
    conn.close()

async def log_dialog(message: Message, from_user: bool, admin_id: int = None, user_id: int = None):
    try:
        direction = "👤→🦹♂️" if from_user else "🦹♂️→👤"
        tag = get_admin_tag(admin_id) if admin_id else ""
        admin_name = f"#{tag}" if tag else f"ID:{admin_id}"
        log_text = f"{direction} {admin_name} ↔ {user_id}\n"
        
        if message.text:
            log_text += f"📝 {message.text}"
        elif message.photo:
            log_text += f"🖼 Фото: {message.caption or 'без подписи'}"
        elif message.video:
            log_text += f"🎥 Видео: {message.caption or 'без подписи'}"
        elif message.document:
            log_text += f"📄 Документ: {message.document.file_name or 'без имени'}"
        elif message.sticker:
            log_text += f"🎭 Стикер"
        else:
            log_text += f"📎 Медиа"
        
        await bot.send_message(LOG_GROUP_ID, log_text)
    except Exception as e:
        logging.error(f"Ошибка логирования: {e}")

# ========== ОТПРАВКА СООБЩЕНИЙ ==========
async def send_any(target_id: int, source: Message, *, prefix: str = "", kb: InlineKeyboardMarkup | None = None):
    """Универсальная функция для отправки любого типа сообщения"""
    try:
        if source.text:
            await bot.send_message(target_id, f"{prefix}{source.text}", reply_markup=kb)
        elif source.photo:
            await bot.send_photo(target_id, source.photo[-1].file_id, caption=f"{prefix}{source.caption or ''}", reply_markup=kb)
        elif source.video:
            await bot.send_video(target_id, source.video.file_id, caption=f"{prefix}{source.caption or ''}", reply_markup=kb)
        elif source.document:
            await bot.send_document(target_id, source.document.file_id, caption=f"{prefix}{source.caption or ''}", reply_markup=kb)
        elif source.audio:
            await bot.send_audio(target_id, source.audio.file_id, caption=f"{prefix}{source.caption or ''}", reply_markup=kb)
        elif source.voice:
            if prefix:
                await bot.send_message(target_id, prefix)
            await bot.send_voice(target_id, source.voice.file_id, reply_markup=kb)
        elif source.sticker:
            if prefix:
                await bot.send_message(target_id, prefix)
            await bot.send_sticker(target_id, source.sticker.file_id, reply_markup=kb)
        elif source.video_note:
            if prefix:
                await bot.send_message(target_id, prefix)
            await bot.send_video_note(target_id, source.video_note.file_id, reply_markup=kb)
        elif source.animation:
            await bot.send_animation(target_id, source.animation.file_id, caption=f"{prefix}{source.caption or ''}", reply_markup=kb)
        elif source.location:
            if prefix:
                await bot.send_message(target_id, prefix)
            await bot.send_location(target_id, source.location.latitude, source.location.longitude, reply_markup=kb)
        elif source.contact:
            if prefix:
                await bot.send_message(target_id, prefix)
            await bot.send_contact(target_id, source.contact.phone_number, source.contact.first_name,
                                   last_name=source.contact.last_name, reply_markup=kb)
        else:
            await bot.send_message(target_id, f"{prefix}Тип сообщения не поддерживается", reply_markup=kb)
            
    except Exception as e:
        logging.error(f"Ошибка отправки сообщения для {target_id}: {str(e)}")
        try:
            if prefix:
                await bot.send_message(target_id, f"{prefix}Не удалось отправить сообщение. Текст: {source.text or source.caption or 'нет текста'}")
        except Exception:
            pass

def format_user_info(u: Message.from_user) -> str:
    return f"🆔 ID: `{u.from_user.id}`\n👤 Имя: {u.from_user.full_name}\n🔗 Username: @{u.from_user.username or 'нет'}"

def extract_uid_from_text(s: str) -> Optional[int]:
    m = re.search(r"ID:\s*`(\d+)`", s)
    if m:
        return int(m.group(1))
    m2 = re.search(r"ID:\s*(\d+)", s)
    if m2:
        return int(m2.group(1))
    return None

# ========== Хендлеры команд ==========

@dp.message(Command("start"))
async def cmd_start(message: Message):
    add_or_touch_user(message.from_user.id, message.from_user.username)
    inc_stat("start_count")
    
    # Новое приветственное сообщение
    welcome_text = """Хай! 👋 Мы рады видеть тебя в нашем боте и желаем провести здесь незабываемое время. Мы всегда готовы помочь даже в самых непростых ситуациях. Нам можно доверять. ❤️

🔥 Чтобы обратиться к администратору, используй команду /admin

💡 Доступные команды:
/help - Справка по командам
/profile - Твой профиль
/admin - Обратиться к администратору"""
    
    await message.answer(welcome_text)

@dp.message(Command("help"))
async def cmd_help(message: Message):
    help_text = """🔧 **Доступные команды:**

👤 **Для всех пользователей:**
/start - Начать работу с ботом
/help - Показать это сообщение
/profile - Показать информацию о профиле
/admin - Обратиться к администратору

🔧 **Для админов:**
/list_admins - Список всех администраторов
/admin_panel - Открыть панель управления
/user_info [user_id] - Информация о пользователе
/soo <текст> - Рассылка всем пользователям

👑 **Для владельца:**
/tag <тег> - Установить пользовательский тег
/set_tag <тег> - Установить себе тег
/set_role <роль> - Установить роль пользователю
/admin_tag <тег> - Установить админский тег
/tags - Показать все теги
/ban <срок> <причина> - Заблокировать пользователя
/unban - Разблокировать пользователя
/stats - Статистика бота"""
    
    await message.answer(help_text)

@dp.message(Command("profile"))
async def cmd_profile(message: Message):
    uid = message.from_user.id
    role = get_user_role(uid)
    tag = get_user_tag(uid)
    admin_tag = get_admin_tag(uid) if is_admin(uid) else None
    current_admin_id, wants_admin = get_user_mode(uid)
    admin_status = f"Привязан к админу #{get_admin_tag(current_admin_id) or current_admin_id}" if current_admin_id else "Не привязан"
    
    out = [
        f"👤 Профиль пользователя:",
        f"🆔 ID: `{uid}`",
        f"📛 Имя: {message.from_user.full_name}",
        f"🔗 Username: @{message.from_user.username or 'отсутствует'}",
        f"🎭 Роль: {role}",
        f"📊 Статус: {admin_status}"
    ]
    
    if tag:
        out.append(f"🏷️ Пользовательский тег: #{tag}")
    if admin_tag:
        out.append(f"🦹♂️ Админский тег: #{admin_tag}")
    
    await message.answer("\n".join(out))

# ========== WEBAPP ХЕНДЛЕРЫ ==========

@dp.message(Command("admin"))
async def cmd_admin(message: Message):
    """Команда для обращения к администратору с WebApp"""
    # Проверяем, есть ли уже активный запрос
    existing_request = get_admin_request(message.from_user.id)
    if existing_request:
        admin_tag = get_admin_tag(existing_request['admin_id']) or f"ID:{existing_request['admin_id']}"
        await message.answer(f"⏳ У вас уже есть активный запрос к администратору #{admin_tag}. Дождитесь ответа.")
        return
    
    # Показываем кнопки для выбора способа обращения
    webapp = WebAppInfo(url=USER_WEBAPP_URL)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡️ Выбрать администратора", web_app=webapp)],
        [InlineKeyboardButton(text="📞 Быстрая поддержка", callback_data="quick_support")]
    ])
    
    await message.answer(
        "👥 **Обращение к администратору**\n\n"
        "🎯 Выберите администратора через удобное приложение\n"
        "⚡ Или воспользуйтесь быстрой поддержкой",
        reply_markup=kb
    )

@dp.message(Command("admin_panel"))
async def cmd_admin_panel(message: Message):
    """Админ панель для управления запросами"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Эта команда доступна только администраторам.")
        return
    
    webapp = WebAppInfo(url=ADMIN_WEBAPP_URL)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛡️ Открыть админ панель", web_app=webapp)]
    ])
    
    admin_tag = get_admin_tag(message.from_user.id)
    await message.answer(
        f"🦹♂️ **Админ панель #{admin_tag or 'admin'}**\n\n"
        "📊 Управляйте запросами и диалогами\n"
        "📈 Просматривайте статистику\n"
        "⚙️ Настраивайте профиль",
        reply_markup=kb
    )

# Обработчик данных от мини-приложения
@dp.message(F.web_app_data)
async def handle_webapp_data(message: Message):
    """Обработка данных от WebApp"""
    try:
        data = json.loads(message.web_app_data.data)
        action = data.get('action')
        
        if action == 'select_admin':
            # Пользователь выбрал админа через мини-приложение
            admin_id = data.get('admin_id')
            if not admin_id:
                await message.answer("❌ Ошибка: не указан администратор")
                return
            
            # Проверяем, что админ существует
            if not is_admin(admin_id):
                await message.answer("❌ Выбранный администратор не найден")
                return
            
            # Создаем запрос
            create_admin_request(message.from_user.id, admin_id)
            
            # Отправляем запрос админу
            admin_tag = get_admin_tag(admin_id)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{message.from_user.id}")],
                [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{message.from_user.id}")]
            ])
            
            try:
                user_tag = get_user_tag(message.from_user.id)
                await bot.send_message(admin_id,
                    f"🔔 **Новый запрос через мини-приложение**\n\n" +
                    f"🆔 ID: `{message.from_user.id}`\n" +
                    f"👤 Имя: {message.from_user.full_name}\n" +
                    f"🔗 Username: @{message.from_user.username or 'нет'}\n" +
                    f"🏷️ Тег: #{user_tag or 'нет'}\n\n" +
                    f"💭 Пользователь выбрал вас через приложение.",
                    reply_markup=kb
                )
                
                await message.answer(f"📨 Запрос отправлен администратору **#{admin_tag}**\n⏳ Ожидайте ответа...")
                inc_stat("webapp_requests")
                
            except Exception as e:
                logging.error(f"Ошибка отправки запроса админу {admin_id}: {e}")
                await message.answer("❌ Ошибка отправки запроса. Попробуйте позже.")
        
        elif action == 'admin_action':
            # Действие от админ панели
            if not is_admin(message.from_user.id):
                await message.answer("❌ Недостаточно прав")
                return
            
            sub_action = data.get('sub_action')
            target_user = data.get('user_id')
            
            if sub_action == 'accept_request' and target_user:
                # Принять запрос через админ панель
                process_admin_request(target_user, "accepted")
                assign_admin_to_user(target_user, message.from_user.id)
                admin_tag = get_admin_tag(message.from_user.id)
                
                kb = InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🚫 Завершить диалог", callback_data="user_cancel")]
                ])
                
                try:
                    await bot.send_message(target_user,
                        f"✅ Администратор **#{admin_tag}** принял ваш запрос!\n\n"
                        f"💬 Теперь вы можете писать сообщения\n"
                        f"🎯 Отправляйте текст, фото, видео - все будет доставлено!",
                        reply_markup=kb
                    )
                    await message.answer("✅ Запрос принят через админ панель")
                except Exception as e:
                    await message.answer("❌ Ошибка уведомления пользователя")
            
            elif sub_action == 'reject_request' and target_user:
                # Отклонить запрос через админ панель
                process_admin_request(target_user, "rejected")
                try:
                    await bot.send_message(target_user,
                        "❌ Администратор не может принять ваш запрос сейчас.\n"
                        "Попробуйте обратиться позже или выберите другого администратора."
                    )
                    await message.answer("❌ Запрос отклонен через админ панель")
                except Exception as e:
                    await message.answer("❌ Ошибка уведомления пользователя")
        
        # Сохраняем данные сессии
        save_webapp_session(message.from_user.id, data)
        
    except json.JSONDecodeError:
        await message.answer("❌ Ошибка обработки данных приложения")
    except Exception as e:
        logging.error(f"Ошибка обработки WebApp данных: {e}")
        await message.answer("❌ Произошла ошибка")

# API для получения данных мини-приложениями
@dp.message(Command("webapp_data"))
async def cmd_webapp_data(message: Message):
    """API команда для получения данных для WebApp"""
    user_id = message.from_user.id
    if is_admin(user_id):
        # Данные для админ панели
        data = get_admin_panel_data(user_id)
    else:
        # Данные для пользовательского приложения
        data = get_admins_for_webapp()
    
    # Отправляем данные как JSON
    await message.answer(f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```")

@dp.callback_query(F.data == "quick_support")
async def cb_quick_support(query: CallbackQuery):
    """Быстрая поддержка - автоматический выбор свободного админа"""
    admins = get_all_admins()
    if not admins:
        await query.answer("❌ Нет доступных администраторов", show_alert=True)
        return
    
    # Выбираем первого доступного админа
    admin = admins[0]
    admin_id = admin['user_id']
    
    # Создаем запрос
    create_admin_request(query.from_user.id, admin_id)
    
    # Отправляем запрос админу
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Принять", callback_data=f"accept_{query.from_user.id}")],
        [InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{query.from_user.id}")]
    ])
    
    try:
        admin_tag = get_admin_tag(admin_id)
        user_tag = get_user_tag(query.from_user.id)
        await bot.send_message(admin_id,
            f"⚡ **Быстрый запрос поддержки**\n\n" +
            f"🆔 ID: `{query.from_user.id}`\n" +
            f"👤 Имя: {query.from_user.full_name}\n" +
            f"🔗 Username: @{query.from_user.username or 'нет'}\n" +
            f"🏷️ Тег: #{user_tag or 'нет'}\n\n" +
            f"💭 Быстрая поддержка - автовыбор.",
            reply_markup=kb
        )
        
        await query.message.edit_text(f"📨 Запрос отправлен администратору **#{admin_tag}**\n⏳ Ожидайте ответа...")
        inc_stat("quick_support_requests")
        
    except Exception as e:
        logging.error(f"Ошибка быстрой поддержки: {e}")
        await query.answer("❌ Ошибка отправки запроса", show_alert=True)

# Обновленные коллбеки с тегами
@dp.callback_query(F.data.startswith("accept_"))
async def cb_accept(query: CallbackQuery):
    user_id = int(query.data.split("_")[1])
    admin_id = query.from_user.id
    
    request = get_admin_request(user_id)
    if not request or request['admin_id'] != admin_id:
        await query.answer("❌ Запрос не найден или уже обработан", show_alert=True)
        return
    
    process_admin_request(user_id, "accepted")
    assign_admin_to_user(user_id, admin_id)
    
    await query.message.edit_text(
        query.message.text + "\n\n✅ **Запрос принят**",
        reply_markup=None
    )
    
    admin_tag = get_admin_tag(admin_id) or f"admin_{admin_id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 Завершить диалог", callback_data="user_cancel")]
    ])
    
    try:
        await bot.send_message(user_id,
            f"✅ Администратор **#{admin_tag}** принял ваш запрос!\n\n"
            f"💬 Теперь вы можете писать сообщения\n"
            f"🎯 Отправляйте текст, фото, видео - все будет доставлено!",
            reply_markup=kb)
        inc_stat("admin_accepts")
        log_action(admin_id, "accept_request", user_id)
    except Exception as e:
        logging.error(f"Ошибка уведомления пользователя {user_id}: {e}")
    
    await query.answer("✅ Запрос принят")

@dp.callback_query(F.data.startswith("reject_"))
async def cb_admin_reject(query: CallbackQuery):
    user_id = int(query.data.split("_")[1])
    admin_id = query.from_user.id
    
    request = get_admin_request(user_id)
    if not request or request['admin_id'] != admin_id:
        await query.answer("❌ Запрос не найден или уже обработан", show_alert=True)
        return
    
    process_admin_request(user_id, "rejected")
    
    await query.message.edit_text(
        query.message.text + "\n\n❌ **Запрос отклонен**",
        reply_markup=None
    )
    
    try:
        await bot.send_message(user_id,
            "❌ Администратор не может принять ваш запрос сейчас.\n"
            "Попробуйте обратиться позже или выберите другого администратора командой /admin")
        log_action(admin_id, "reject_request", user_id)
    except Exception as e:
        logging.error(f"Ошибка уведомления пользователя {user_id}: {e}")
    
    await query.answer("❌ Запрос отклонен")

@dp.callback_query(F.data == "user_cancel")
async def cb_user_cancel(query: CallbackQuery):
    user_id = query.from_user.id
    admin_id = get_current_admin(user_id)
    
    if admin_id:
        remove_admin_from_user(user_id)
        try:
            user_tag = get_user_tag(user_id) or f"user_{user_id}"
            await bot.send_message(admin_id, f"🚫 Пользователь **#{user_tag}** завершил диалог")
            log_action(user_id, "user_cancel", admin_id)
        except Exception as e:
            logging.error(f"Ошибка уведомления админа {admin_id}: {e}")
    
    await query.message.edit_text("🚫 Диалог завершен. Используйте /admin для нового обращения.", reply_markup=None)
    await query.answer("Диалог завершен")

# ========== АДМИНСКИЕ КОМАНДЫ ==========

@dp.message(Command("list_admins"))
async def cmd_list_admins(message: Message):
    admins = get_all_admins()
    if not admins:
        await message.answer("📋 Список админов пуст.")
        return
    
    lines = ["📋 **Список администраторов:**"]
    for r in admins:
        lines.append(f"🆔 `{r['user_id']}` | @{r['username'] or 'нет'} | #{r['tag'] or 'без тега'}")
    
    await message.answer("\n".join(lines))

@dp.message(Command("user_info"))
async def cmd_user_info(message: Message):
    """Показать информацию о пользователе (для админов)"""
    if not is_admin(message.from_user.id):
        await message.answer("❌ Недостаточно прав.")
        return
    
    # Если это ответ на сообщение, используем ID из сообщения
    if message.reply_to_message:
        user_id = message.reply_to_message.from_user.id
    else:
        parts = message.text.split()
        if len(parts) < 2:
            await message.answer("Использование: /user_info user_id или ответьте на сообщение пользователя")
            return
        try:
            user_id = int(parts[1])
        except ValueError:
            await message.answer("❌ Неверный user_id")
            return
    
    # Получаем информацию о пользователе
    user = get_user_info(user_id)
    if not user:
        await message.answer("❌ Пользователь не найден.")
        return
    
    role = user["role"]
    tag = get_user_tag(user_id)
    admin_tag = get_admin_tag(user_id) if is_admin(user_id) else None
    banned = is_banned(user_id)
    current_admin_id, wants_admin = get_user_mode(user_id)
    admin_status = f"Привязан к админу {current_admin_id}" if current_admin_id else "Не привязан"
    
    out = [
        f"👤 **Информация о пользователе:**",
        f"🆔 ID: `{user_id}`",
        f"📛 Имя: {user['username'] or 'Неизвестно'}",
        f"🎭 Роль: {role}",
        f"📊 Статус: {admin_status}",
        f"⛔ Заблокирован: {'Да' if banned else 'Нет'}",
        f"📅 Первый визит: {user['first_seen']}",
        f"📅 Последний визит: {user['last_seen']}"
    ]
    
    if tag:
        out.append(f"🏷️ Пользовательский тег: #{tag}")
    if admin_tag:
        out.append(f"🦹♂️ Админский тег: #{admin_tag}")
    
    await message.answer("\n".join(out))

@dp.message(Command("soo"))
async def cmd_soo(message: Message):
    if message.from_user.id != OWNER_ID and not get_user_tag(message.from_user.id):
        await message.answer("❌ Недостаточно прав для рассылки.")
        return
    
    sent = 0
    if message.reply_to_message:
        for uid in get_all_user_ids():
            try:
                await bot.copy_message(uid, message.chat.id, message.reply_to_message.message_id)
                sent += 1
            except Exception:
                pass
    else:
        parts = (message.text or "").split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("Использование: /soo текст или ответьте на сообщение.")
            return
        
        text = parts[1]
        for uid in get_all_user_ids():
            try:
                await bot.send_message(uid, f"📢 Рассылка:\n\n{text}")
                sent += 1
            except Exception:
                pass
    
    await message.answer(f"✅ Разослано: {sent}")
    inc_stat("broadcasts")
    log_action(message.from_user.id, "broadcast", details=f"Отправлено: {sent}")

# ========== КОМАНДЫ ВЛАДЕЛЬЦА ==========

@dp.message(Command("tag"))
async def cmd_tag(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Недостаточно прав.")
        return
    
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование: /tag user_id тег")
        return
    
    try:
        user_id = int(parts[1])
        tag = parts[2].strip().lstrip("#@")
        set_user_tag(user_id, tag)
        log_action(message.from_user.id, "set_user_tag", user_id, tag)
        await message.answer(f"✅ Пользователю {user_id} установлен тег: #{tag}")
    except ValueError:
        await message.answer("❌ Неверный user_id")

@dp.message(Command("set_tag"))
async def cmd_set_tag(message: Message):
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /set_tag тег")
        return
    
    tag = parts[1].strip().lstrip("#@")
    set_user_tag(message.from_user.id, tag)
    log_action(message.from_user.id, "set_own_tag", details=tag)
    await message.answer(f"✅ Вам установлен тег: #{tag}")

@dp.message(Command("set_role"))
async def cmd_set_role(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Недостаточно прав.")
        return
    
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование: /set_role user_id роль")
        return
    
    try:
        user_id = int(parts[1])
        role = parts[2]
        set_user_role(user_id, role)
        log_action(message.from_user.id, "set_role", user_id, role)
        await message.answer(f"✅ Пользователю {user_id} установлена роль: {role}")
    except ValueError:
        await message.answer("❌ Неверный user_id")

@dp.message(Command("admin_tag"))
async def cmd_admin_tag(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Недостаточно прав.")
        return
    
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.answer("Использование: /admin_tag user_id тег")
        return
    
    try:
        user_id = int(parts[1])
        tag = parts[2].strip().lstrip("#@")
        set_admin_tag(user_id, tag)
        log_action(message.from_user.id, "set_admin_tag", user_id, tag)
        await message.answer(f"✅ Администратору {user_id} установлен тег: #{tag}")
    except ValueError:
        await message.answer("❌ Неверный user_id")

@dp.message(Command("tags"))
async def cmd_tags(message: Message):
    tags = get_all_tags()
    if not tags:
        await message.answer("🏷️ Активных тегов нет.")
        return
    
    await message.answer("🏷️ Активные теги:\n" + "\n".join([f"#{tag}" for tag in tags]))

@dp.message(Command("ban"))
async def cmd_ban(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Недостаточно прав.")
        return
    
    parts = (message.text or "").split()
    if len(parts) < 4:
        await message.answer("Использование: /ban user_id срок причина (например: /ban 123456 1d спам)")
        return
    
    try:
        user_id = int(parts[1])
        duration = parts[2]
        reason = " ".join(parts[3:])
        
        if duration.endswith("d"):
            hours = int(duration[:-1]) * 24
        elif duration.endswith("h"):
            hours = int(duration[:-1])
        else:
            await message.answer("❌ Неверный формат срока. Используйте 1d или 2h")
            return
        
        until_ts = int(time.time()) + hours * 3600
        ban_user(user_id, until_ts, reason, message.from_user.id)
        log_action(message.from_user.id, "ban", user_id, f"До {until_ts}, причина: {reason}")
        
        await message.answer(f"✅ Пользователь {user_id} забанен до {datetime.fromtimestamp(until_ts).strftime('%d.%m.%Y %H:%M')}\nПричина: {reason}")
    except ValueError:
        await message.answer("❌ Неверный user_id")

@dp.message(Command("unban"))
async def cmd_unban(message: Message):
    if message.from_user.id != OWNER_ID:
        await message.answer("❌ Недостаточно прав.")
        return
    
    parts = (message.text or "").split()
    if len(parts) < 2:
        await message.answer("Использование: /unban user_id")
        return
    
    try:
        user_id = int(parts[1])
        unban_user(user_id)
        log_action(message.from_user.id, "unban", user_id)
        await message.answer(f"✅ Пользователь {user_id} разбанен")
    except ValueError:
        await message.answer("❌ Неверный user_id")

@dp.message(Command("stats"))
async def cmd_stats(message: Message):
    users_count = len(get_all_user_ids())
    tags_count = len(get_all_tags())
    
    conn = db()
    cur = conn.execute("SELECT COUNT(*) AS c FROM bans WHERE until_ts > ?", (int(time.time()),))
    bans_count = cur.fetchone()["c"]
    conn.close()
    
    stats_text = (
        "📊 **Статистика бота:**\n\n"
        f"👥 Всего пользователей: {users_count}\n"
        f"🏷️ Активных тегов: {tags_count}\n"
        f"⛔ Забанено: {bans_count}\n"
        f"🎯 Запусков: {get_stat('start_count')}\n"
        f"📢 Рассылок: {get_stat('broadcasts')}\n"
        f"✅ Принятых запросов: {get_stat('admin_accepts')}\n"
        f"🛡️ WebApp запросов: {get_stat('webapp_requests')}\n"
        f"⚡ Быстрых запросов: {get_stat('quick_support_requests')}"
    )
    
    await message.answer(stats_text)

# ========== ОБРАБОТКА ЛИЧНЫХ СООБЩЕНИЙ ==========

@dp.message()
async def on_private_message(message: Message):
    """Обработка всех личных сообщений"""
    if is_banned(message.from_user.id):
        await message.answer("⛔ Вы заблокированы и не можете пользоваться ботом.")
        return
    
    add_or_touch_user(message.from_user.id, message.from_user.username)
    
    if is_admin(message.from_user.id):
        # Логика для админа
        admin_id = message.from_user.id
        
        if message.reply_to_message:
            # Админ отвечает на сообщение
            uid = extract_uid_from_text(message.reply_to_message.text or "")
            if uid and get_current_admin(uid) == admin_id:
                admin_tag = get_admin_tag(admin_id) or f"admin_{admin_id}"
                prefix = f"✉️ **#{admin_tag}**\n\n"
                try:
                    await send_any(uid, message, prefix=prefix)
                    await log_dialog(message, False, admin_id, uid)
                    log_action(admin_id, "reply_to_user", uid)
                except Exception as e:
                    logging.error(f"Ошибка отправки ответа пользователю {uid}: {e}")
                    await message.answer("❌ Не удалось отправить сообщение пользователю.")
            else:
                await message.answer("❌ Пользователь не найден или не привязан к вам.")
        else:
            # Обычное сообщение от админа
            await message.answer("ℹ️ Чтобы ответить пользователю, используйте Reply на его сообщение.")
    else:
        # Логика для обычного пользователя
        uid = message.from_user.id
        admin_id = get_current_admin(uid)
        
        if admin_id:
            # Пользователь в диалоге с админом
            user_tag = get_user_tag(uid) or f"user_{uid}"
            prefix = f"💬 **Сообщение от пользователя:**\n🆔 ID: `{uid}` (#{user_tag})\n\n"
            try:
                await send_any(admin_id, message, prefix=prefix)
                await log_dialog(message, True, admin_id, uid)
            except Exception as e:
                logging.error(f"Ошибка отправки сообщения админу {admin_id}: {e}")
                await message.answer("❌ Не удалось отправить сообщение администратору.")
        else:
            # Пользователь не в диалоге
            await message.answer(
                "ℹ️ Для обращения к администратору используйте команду /admin\n\n"
                "🔥 Доступные команды:\n"
                "/admin - Обратиться к администратору\n"
                "/help - Справка по командам\n"
                "/profile - Информация о профиле"
            )

# ========== ЗАПУСК БОТА ==========

async def main():
    logging.info("Инициализация базы данных...")
    init_db()
    run_migrations()
    
    logging.info("Запуск бота...")
    try:
        await dp.start_polling(bot, skip_updates=True)
    except KeyboardInterrupt:
        logging.info("Бот остановлен")
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Программа завершена пользователем")