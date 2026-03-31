#!/usr/bin/env python3
"""
💎 Finora — Твой личный финансовый друг
Telegram bot: учёт финансов + AI советы + умный онбординг + уведомления
"""

import os, json, logging, tempfile, base64, asyncio, threading, hashlib, hmac
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants, MenuButtonWebApp, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from flask import Flask, render_template, request, session, redirect, url_for, jsonify

VOICE_OK = True  # always True — using Whisper API

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN      = os.getenv('BOT_TOKEN', '')
OPENROUTER_KEY = os.getenv('OPENROUTER_KEY', '')
OPENAI_KEY     = os.getenv('OPENAI_KEY', '')   # for Whisper voice transcription (optional)
GROQ_KEY       = os.getenv('GROQ_KEY', '')     # for Groq Whisper — free! (preferred)
DATABASE_URL        = os.getenv('DATABASE_URL', '')         # PostgreSQL internal URL
DATABASE_PUBLIC_URL = os.getenv('DATABASE_PUBLIC_URL', '')  # PostgreSQL public URL (preferred)
OR_MODEL       = os.getenv('OR_MODEL', 'anthropic/claude-sonnet-4-5')
TZ             = ZoneInfo('Asia/Tashkent')

client = OpenAI(
    api_key=OPENROUTER_KEY,
    base_url='https://openrouter.ai/api/v1',
    default_headers={
        'HTTP-Referer': 'https://finora.app',
        'X-Title': 'Finora Finance Bot',
    }
)

# ────────────────────────── ONBOARDING STATES ─────────────────────
STATE_LANG        = 'lang'
STATE_NAME        = 'name'
STATE_INCOME_FREQ = 'income_freq'
STATE_INCOME_AMT  = 'income_amt'
STATE_CURRENCY    = 'currency'
STATE_SIDE_HUSTLE = 'side_hustle'
STATE_SIDE_AMT    = 'side_amt'
STATE_GOAL        = 'goal'
STATE_GOAL_CUSTOM = 'goal_custom'
STATE_NOTIFY_WHY  = 'notify_why'
STATE_NOTIFY_TIME = 'notify_time'
STATE_DONE        = 'done'

# ────────────────────────── TRANSLATIONS ──────────────────────────
T = {
    'ru': {
        'choose_lang'      : '👋 Привет! Я *Finora* — твой личный финансовый друг 💎\n\nВыбери язык:',
        'ask_name'         : '✨ Отлично! Как тебя зовут? Напиши своё имя:',
        'ask_income_freq'  : ('🎉 Приятно познакомиться, *{name}*!\n\n'
                              'Скажи мне — как часто ты получаешь доход?'),
        'freq_daily'       : '📅 Каждый день',
        'freq_weekly'      : '📆 Раз в неделю',
        'freq_monthly'     : '🗓 Раз в месяц',
        'freq_irregular'   : '🔀 Нерегулярно',
        'ask_income_amt'   : '💰 Сколько примерно зарабатываешь? Напиши сумму (например: *500000*):',
        'ask_currency'     : '💱 В какой валюте?',
        'cur_uzs'          : '🇺🇿 Сум (UZS)',
        'cur_usd'          : '🇺🇸 Доллар (USD)',
        'cur_rub'          : '🇷🇺 Рубль (RUB)',
        'ask_side_hustle'  : ('👀 Понял!\n\n'
                              'А есть подработка, бизнес или что-то ещё '
                              'откуда приходят деньги помимо основного дохода?'),
        'yes'              : '✅ Да',
        'no'               : '❌ Нет',
        'ask_side_amt'     : '💼 Сколько в среднем приходит с подработки/бизнеса? (напиши сумму):',
        'ask_goal'         : ('🎯 *{name}*, а какая у тебя финансовая цель?\n\n'
                              'Это очень важно — цель даёт смысл каждой записанной трате. '
                              'Без цели деньги просто утекают, и ты не понимаешь куда. '
                              'С целью — каждый сэкономленный сум становится шагом к мечте 🚀'),
        'goal_save'        : '🏦 Накопить деньги',
        'goal_buy'         : '🛒 Купить что-то конкретное',
        'goal_invest'      : '📈 Начать инвестировать',
        'goal_debt'        : '💳 Закрыть долги/кредиты',
        'goal_business'    : '🏪 Открыть/развить бизнес',
        'goal_none'        : '🤷 Пока нет цели',
        'ask_goal_custom'  : '✏️ Напиши свою цель (например: *Купить машину к декабрю*):',
        'no_goal_speech'   : ('Хм, *{name}*, я тебя понимаю 😊\n\n'
                              'Но вот в чём фишка — люди без финансовой цели тратят в среднем на 40% больше, '
                              'чем те у кого цель есть. Просто потому что нет ориентира.\n\n'
                              'Давай я помогу сформулировать? Напиши что-нибудь, пусть даже размыто — '
                              'например *"хочу не жить от зарплаты до зарплаты"* или *"хочу поехать в отпуск"*:'),
        'ask_notify_why'   : ('🔔 *{name}*, последний вопрос!\n\n'
                              'Я хочу каждый день напоминать тебе записывать траты. '
                              'Это важно, потому что 80% людей забывают мелкие расходы — '
                              'а именно они и "съедают" деньги незаметно.\n\n'
                              'В какое время тебе удобнее получать напоминание вечером?'),
        'notify_18'        : '🕕 18:00',
        'notify_19'        : '🕖 19:00',
        'notify_20'        : '🕗 20:00',
        'notify_21'        : '🕘 21:00',
        'notify_22'        : '🕙 22:00',
        'notify_23'        : '🕙 23:00',
        'notify_custom'    : '✏️ Другое время',
        'ask_notify_time'  : '⌚ Напиши удобное время в формате *ЧЧ:ММ* (например: *20:30*):',
        'notify_set'       : '✅ Буду напоминать каждый день в *{time}*',
        'welcome_done'     : ('🎉 *{name}*, теперь я знаю тебя!\n\n'
                              'Давай начнём вместе следить за твоими финансами.\n\n'
                              '*Как пользоваться:*\n'
                              '📝 Напиши что потратил/заработал — я всё запишу\n'
                              '🎤 Скажи голосом\n'
                              '📷 Отправь фото чека\n\n'
                              '*Команды:*\n'
                              '/stats — 📊 Статистика\n'
                              '/history — 📋 История\n'
                              '/advice — 🤖 Совет от Finora\n'
                              '/rate — 💱 Курс валют\n'
                              '/settings — ⚙️ Настройки\n'
                              '/help — ❓ Помощь'),
        'processing'       : '⏳ Думаю...',
        'added'            : '✅ Записала!',
        'type_inc'         : '📈 Доход',
        'type_exp'         : '📉 Расход',
        'no_data'          : '📭 Пока нет записей. Напиши что потратил — я запишу!',
        'voice_error'      : '❌ Не смогла разобрать голос. Попробуй ещё раз или напиши текстом.',
        'photo_error'      : '❌ Не смогла прочитать чек. Попробуй более чёткое фото.',
        'parse_error'      : '🤔 Не поняла. Попробуй написать подробнее, например: *Купил хлеб 3000 сум*',
        'fix_prompt'       : '✏️ Что исправить? Напиши новую сумму или описание:',
        'fixed'            : '✅ Исправила!',
        'cancelled'        : '❌ Отменено.',
        'rate_err'         : '❌ Не удалось получить курс.',
        'advice_wait'      : '🤔 Анализирую твои финансы...',
        'confirm_clear'    : '⚠️ Удалить *все* данные?',
        'cleared'          : '🗑 Данные удалены.',
        'yes_del'          : '🗑 Да, удалить',
        'no_cancel'        : '← Отмена',
        'stats_hdr'        : '📊 *Финансовый отчёт, {name}*',
        'all_time'         : 'За всё время',
        'this_month'       : 'Этот месяц',
        'income_lbl'       : '📈 Доходы',
        'expense_lbl'      : '📉 Расходы',
        'balance_lbl'      : 'Баланс',
        'records_lbl'      : 'Записей',
        'hist_hdr'         : '📋 *Последние записи*',
        'rate_hdr'         : '💱 *Курс ЦБ Узбекистана*',
        'advice_hdr'       : '💎 *Finora советует:*',
        'updated'          : 'Обновлено',
        'remind_msg'       : ('Привет, *{name}*! 👋\n\n'
                              'Как прошёл день? Не забудь записать сегодняшние '
                              'траты — даже самые мелкие.\n\n'
                              'Именно из мелочей складывается полная картина куда '
                              'уходят деньги 💸 А без неё сложно двигаться к цели!\n\n'
                              '_Просто напиши что потратил — я всё запишу_ ✍️'),
        'help_text'        : ('❓ *Как пользоваться Finora:*\n\n'
                              '*Добавить запись:*\n'
                              '• Напиши: _"Купил продукты 35 000"_\n'
                              '• 🎤 Скажи голосом\n'
                              '• 📷 Отправь фото чека\n\n'
                              '*Исправить:* напиши _"исправь"_ или _"отмени"_\n\n'
                              '*Команды:*\n'
                              '/stats — 📊 Статистика\n'
                              '/history — 📋 История\n'
                              '/advice — 🤖 AI-совет\n'
                              '/rate — 💱 Курс валют\n'
                              '/settings — ⚙️ Настройки\n'
                              '/clear — 🗑 Очистить данные'),
        'settings_hdr'     : '⚙️ *Настройки*\n\nЧто хочешь изменить?',
        'set_notify'       : '🔔 Время уведомлений',
        'set_goal'         : '🎯 Финансовая цель',
        'set_name'         : '👤 Своё имя',
        'cancel_notify'    : '🔕 Отключить уведомления',
        'notify_disabled'  : '🔕 Уведомления отключены.',
    },
    'uz': {
        'choose_lang'      : '👋 Salom! Men *Finora* — sizning shaxsiy moliyaviy do\'stingiz 💎\n\nTilni tanlang:',
        'ask_name'         : '✨ Ajoyib! Ismingiz nima? Yozing:',
        'ask_income_freq'  : ('🎉 Tanishganimdan xursandman, *{name}*!\n\n'
                              'Aytingchi — daromad qancha vaqt oralig\'ida kelib turadi?'),
        'freq_daily'       : '📅 Har kun',
        'freq_weekly'      : '📆 Haftada bir',
        'freq_monthly'     : '🗓 Oyda bir',
        'freq_irregular'   : '🔀 Tartibsiz',
        'ask_income_amt'   : '💰 Taxminan qancha ishlanadi? Miqdorni yozing (masalan: *500000*):',
        'ask_currency'     : '💱 Qaysi valyutada?',
        'cur_uzs'          : '🇺🇿 So\'m (UZS)',
        'cur_usd'          : '🇺🇸 Dollar (USD)',
        'cur_rub'          : '🇷🇺 Rubl (RUB)',
        'ask_side_hustle'  : ('👀 Tushundim!\n\n'
                              'Asosiy daromaddan tashqari qo\'shimcha ish, biznes yoki '
                              'boshqa pul manbai bormi?'),
        'yes'              : '✅ Ha',
        'no'               : '❌ Yo\'q',
        'ask_side_amt'     : '💼 Qo\'shimcha ish/biznesdan o\'rtacha qancha keladi? (miqdorni yozing):',
        'ask_goal'         : ('🎯 *{name}*, moliyaviy maqsadingiz nima?\n\n'
                              'Bu juda muhim — maqsad har bir yozilgan xarajatga ma\'no beradi. '
                              'Maqsadsiz pul shunchaki ketib qoladi. '
                              'Maqsad bilan — tejagan har bir so\'m orzuingizga qadam bo\'ladi 🚀'),
        'goal_save'        : '🏦 Pul to\'plash',
        'goal_buy'         : '🛒 Biror narsa sotib olish',
        'goal_invest'      : '📈 Investitsiya boshlash',
        'goal_debt'        : '💳 Qarz/kreditni to\'lash',
        'goal_business'    : '🏪 Biznesni ochish/rivojlantirish',
        'goal_none'        : '🤷 Hozircha yo\'q',
        'ask_goal_custom'  : '✏️ Maqsadingizni yozing (masalan: *Dekabrgacha mashina olish*):',
        'no_goal_speech'   : ('Hmm, *{name}*, sizi tushunaman 😊\n\n'
                              'Lekin bir gap bor — moliyaviy maqsadi bo\'lmaganlar o\'rtacha 40% ko\'proq sarflaydi. '
                              'Chunki yo\'nalish yo\'q.\n\n'
                              'Yordam beraymi? Hatto noaniq bo\'lsa ham yozing — '
                              'masalan *"maosh oyiga yetib borsin"* yoki *"ta\'tilga borish"*:'),
        'ask_notify_why'   : ('🔔 *{name}*, oxirgi savol!\n\n'
                              'Men har kuni xarajatlarni yozib borishingizni eslatib turmoqchiman. '
                              'Odamlarning 80% mayda xarajatlarni unutadi — '
                              'aynan ular pulni "yeb qo\'yadi".\n\n'
                              'Kechqurun qaysi vaqt qulay?'),
        'notify_18'        : '🕕 18:00',
        'notify_19'        : '🕖 19:00',
        'notify_20'        : '🕗 20:00',
        'notify_21'        : '🕘 21:00',
        'notify_22'        : '🕙 22:00',
        'notify_23'        : '🕙 23:00',
        'notify_custom'    : '✏️ Boshqa vaqt',
        'ask_notify_time'  : '⌚ Qulay vaqtni *SS:DD* formatida yozing (masalan: *20:30*):',
        'notify_set'       : '✅ Har kuni *{time}* da eslatib turaman',
        'welcome_done'     : ('🎉 *{name}*, endi sizni tanidim!\n\n'
                              'Keling moliyangizni birgalikda kuzataylik.\n\n'
                              '*Qanday foydalanish:*\n'
                              '📝 Nima sarflaganingizni/topganingizni yozing\n'
                              '🎤 Ovoz bilan ayting\n'
                              '📷 Chek rasmini yuboring\n\n'
                              '*Buyruqlar:*\n'
                              '/stats — 📊 Statistika\n'
                              '/history — 📋 Tarix\n'
                              '/advice — 🤖 Maslahat\n'
                              '/rate — 💱 Valyuta kursi\n'
                              '/settings — ⚙️ Sozlamalar\n'
                              '/help — ❓ Yordam'),
        'processing'       : '⏳ O\'ylamoqda...',
        'added'            : '✅ Yozib oldim!',
        'type_inc'         : '📈 Daromad',
        'type_exp'         : '📉 Xarajat',
        'no_data'          : '📭 Hali yozuv yo\'q. Nimaga sarflaganingizni yozing!',
        'voice_error'      : '❌ Ovozni tushunmadim. Qayta urinib ko\'ring yoki matn yuboring.',
        'photo_error'      : '❌ Chekni o\'qib bo\'lmadi. Aniqroq rasm yuborib ko\'ring.',
        'parse_error'      : '🤔 Tushunmadim. Batafsilroq yozing, masalan: *Non sotib oldim 3000 so\'m*',
        'fix_prompt'       : '✏️ Nimani tuzatish kerak? Yangi miqdor yoki tavsifni yozing:',
        'fixed'            : '✅ Tuzatdim!',
        'cancelled'        : '❌ Bekor qilindi.',
        'rate_err'         : '❌ Kursni ololmadim.',
        'advice_wait'      : '🤔 Moliyangizni tahlil qilyapman...',
        'confirm_clear'    : '⚠️ *Barcha* ma\'lumotlarni o\'chirasizmi?',
        'cleared'          : '🗑 Ma\'lumotlar o\'chirildi.',
        'yes_del'          : '🗑 Ha, o\'chir',
        'no_cancel'        : '← Bekor qilish',
        'stats_hdr'        : '📊 *Moliyaviy hisobot, {name}*',
        'all_time'         : 'Jami',
        'this_month'       : 'Bu oy',
        'income_lbl'       : '📈 Daromad',
        'expense_lbl'      : '📉 Xarajat',
        'balance_lbl'      : 'Balans',
        'records_lbl'      : 'Yozuvlar',
        'hist_hdr'         : '📋 *Oxirgi yozuvlar*',
        'rate_hdr'         : '💱 *O\'zbekiston MBi kursi*',
        'advice_hdr'       : '💎 *Finora maslahat beradi:*',
        'updated'          : 'Yangilandi',
        'remind_msg'       : ('Salom, *{name}*! 👋\n\n'
                              'Kun qanday o\'tdi? Bugungi xarajatlarni yozishni unutmang — '
                              'hatto maydalari ham.\n\n'
                              'Aynan mayda xarajatlar pul ketayotgan joyni ko\'rsatadi 💸 '
                              'Ularsiz maqsadga yetish mushkul!\n\n'
                              '_Nima sarflaganingizni yozing — men yozib olaman_ ✍️'),
        'help_text'        : ('❓ *Finoradan qanday foydalanish:*\n\n'
                              '*Yozuv qo\'shish:*\n'
                              '• Yozing: _"Non sotib oldim 3 000"_\n'
                              '• 🎤 Ovoz bilan\n'
                              '• 📷 Chek rasmi\n\n'
                              '*Tuzatish:* _"tuzat"_ yoki _"bekor qil"_ yozing\n\n'
                              '*Buyruqlar:*\n'
                              '/stats — 📊 Statistika\n'
                              '/history — 📋 Tarix\n'
                              '/advice — 🤖 AI maslahat\n'
                              '/rate — 💱 Valyuta kursi\n'
                              '/settings — ⚙️ Sozlamalar\n'
                              '/clear — 🗑 Ma\'lumotlarni o\'chirish'),
        'settings_hdr'     : '⚙️ *Sozlamalar*\n\nNimani o\'zgartiroqsiz?',
        'set_notify'       : '🔔 Eslatma vaqti',
        'set_goal'         : '🎯 Moliyaviy maqsad',
        'set_name'         : '👤 Ismingiz',
        'cancel_notify'    : '🔕 Eslatmalarni o\'chirish',
        'notify_disabled'  : '🔕 Eslatmalar o\'chirildi.',
    }
}

def tx(uid_or_lang, key: str, **kwargs) -> str:
    lang = uid_or_lang if uid_or_lang in ('ru', 'uz') else get_lang(uid_or_lang)
    text = T.get(lang, T['ru']).get(key, key)
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text

# ────────────────────────── DATABASE (PostgreSQL) ─────────────────
def _normalize_pg_url(url: str) -> str:
    """Normalize postgres:// → postgresql:// for psycopg2."""
    if url.startswith('postgres://'):
        return 'postgresql://' + url[len('postgres://'):]
    return url

def get_conn():
    # 1️⃣ Prefer public URL (works from Docker, no private networking issues)
    if DATABASE_PUBLIC_URL:
        return psycopg2.connect(_normalize_pg_url(DATABASE_PUBLIC_URL))
    # 2️⃣ Try internal URL
    if DATABASE_URL:
        return psycopg2.connect(_normalize_pg_url(DATABASE_URL))
    raise ValueError('Set DATABASE_PUBLIC_URL or DATABASE_URL in Railway Variables.')

def init_db():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('''CREATE TABLE IF NOT EXISTS transactions(
                id SERIAL PRIMARY KEY,
                user_id BIGINT, type TEXT, amount FLOAT,
                description TEXT, category TEXT, items TEXT,
                currency TEXT DEFAULT 'UZS',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS users(
                user_id BIGINT PRIMARY KEY,
                language TEXT DEFAULT 'ru',
                name TEXT DEFAULT '',
                income_freq TEXT DEFAULT '',
                income_amt FLOAT DEFAULT 0,
                income_currency TEXT DEFAULT 'UZS',
                side_income FLOAT DEFAULT 0,
                goal TEXT DEFAULT '',
                notify_time TEXT DEFAULT '21:00',
                notify_enabled INTEGER DEFAULT 1,
                onboarding_state TEXT DEFAULT 'lang',
                onboarding_done INTEGER DEFAULT 0
            )''')
        conn.commit()

def get_user(uid: int) -> dict:
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute('INSERT INTO users(user_id) VALUES(%s) ON CONFLICT DO NOTHING', (uid,))
            conn.commit()
            c.execute('SELECT * FROM users WHERE user_id=%s', (uid,))
            row = c.fetchone()
    return dict(row) if row else {}

def set_user(uid: int, **kwargs):
    if not kwargs: return
    fields = ', '.join(f'{k}=%s' for k in kwargs)
    vals   = list(kwargs.values()) + [uid]
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(f'UPDATE users SET {fields} WHERE user_id=%s', vals)
        conn.commit()

def get_lang(uid: int) -> str:
    return get_user(uid).get('language', 'ru')

def get_state(uid: int) -> str:
    return get_user(uid).get('onboarding_state', STATE_LANG)

def add_tx(uid: int, type_: str, amount: float, desc: str, cat: str,
           cur: str = 'UZS', items: str = ''):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'INSERT INTO transactions(user_id,type,amount,description,category,items,currency) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (uid, type_, amount, desc, cat, items, cur)
            )
            row_id = c.fetchone()[0]
        conn.commit()
    return row_id

def get_last_tx(uid: int):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'SELECT id,type,amount,description,category,currency FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT 1',
                (uid,)
            )
            return c.fetchone()

def update_tx(tx_id: int, amount: float = None, description: str = None):
    with get_conn() as conn:
        with conn.cursor() as c:
            if amount is not None:
                c.execute('UPDATE transactions SET amount=%s WHERE id=%s', (amount, tx_id))
            if description is not None:
                c.execute('UPDATE transactions SET description=%s WHERE id=%s', (description, tx_id))
        conn.commit()

def delete_last_tx(uid: int):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT id FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT 1', (uid,))
            row = c.fetchone()
            if row:
                c.execute('DELETE FROM transactions WHERE id=%s', (row[0],))
                conn.commit()
                return True
    return False

def get_stats(uid: int) -> dict:
    month = datetime.now(TZ).strftime('%Y-%m')
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT type, SUM(amount) FROM transactions WHERE user_id=%s GROUP BY type', (uid,))
            rows = c.fetchall()
            c.execute(
                "SELECT type, SUM(amount) FROM transactions WHERE user_id=%s AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s GROUP BY type",
                (uid, month)
            )
            mrows = c.fetchall()
            c.execute('SELECT COUNT(*) FROM transactions WHERE user_id=%s', (uid,))
            cnt = c.fetchone()[0]
            c.execute(
                "SELECT category, SUM(amount) FROM transactions WHERE user_id=%s AND type='exp' AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s GROUP BY category ORDER BY SUM(amount) DESC LIMIT 5",
                (uid, month)
            )
            cats = c.fetchall()
    s = {'inc': 0, 'exp': 0, 'count': cnt, 'm_inc': 0, 'm_exp': 0, 'cats': cats}
    for r in rows:
        s['inc' if r[0] == 'inc' else 'exp'] = float(r[1] or 0)
    for r in mrows:
        s['m_inc' if r[0] == 'inc' else 'm_exp'] = float(r[1] or 0)
    return s

def get_history(uid: int, limit=15):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'SELECT id,type,amount,description,category,currency,created_at FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT %s',
                (uid, limit)
            )
            return c.fetchall()

def get_recent(uid: int, limit=30):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'SELECT type,amount,description,category,created_at FROM transactions WHERE user_id=%s ORDER BY id DESC LIMIT %s',
                (uid, limit)
            )
            return c.fetchall()

def clear_data(uid: int):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('DELETE FROM transactions WHERE user_id=%s', (uid,))
        conn.commit()

def get_all_users_with_notify():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id, name, language, notify_time FROM users WHERE notify_enabled=1 AND onboarding_done=1 AND notify_time != ''"
            )
            return c.fetchall()

# ────────────────────────── CURRENCY ──────────────────────────────
def get_rates() -> dict:
    try:
        data = requests.get('https://cbu.uz/oz/arkhiv-kursov-valyut/json/', timeout=8).json()
        return {d['Ccy']: {'rate': float(d['Rate']), 'diff': float(d.get('Diff', 0))}
                for d in data if d.get('Ccy') in ('USD', 'EUR', 'RUB')}
    except:
        return {}

def uzs(n: float) -> str:
    return f"{n:,.0f}".replace(',', ' ') + " so'm"

def fmt_amount(amount: float, cur: str, rates: dict) -> str:
    if cur == 'USD':
        r = rates.get('USD', {}).get('rate', 0)
        return f"${amount:,.2f}" + (f" ({uzs(amount * r)})" if r else '')
    elif cur == 'RUB':
        return f"₽{amount:,.0f}"
    else:
        r = rates.get('USD', {}).get('rate', 0)
        return uzs(amount) + (f" ≈ ${amount / r:.2f}" if r else '')

# ────────────────────────── AI ────────────────────────────────────
_PARSE_SYS = """You parse financial transactions from Russian or Uzbek text.
Return ONLY valid JSON, no markdown fences:
{"type":"exp","amount":50000,"description":"brief name","category":"🍔 Еда","currency":"UZS","items":["item - price or just item"]}
- type: "inc" or "exp"
- currency: "USD" if dollars, "RUB" if rubles, else "UZS"
- category pick best from: 🍔 Еда, 🚗 Транспорт, 🏠 Жильё, 💊 Здоровье, 👗 Одежда, 🎮 Развлечения, 📱 Связь, 🛒 Магазин, 💡 Коммуналка, 📚 Образование, ⛽ Бензин, 💼 Бизнес, 🎁 Подарок, 💰 Зарплата, 🤝 Фриланс, 📈 Инвестиции, ❓ Другое
- items: list of individual items if multiple mentioned, else empty list
If text contains correction keywords (исправь/тузат/ошибся/неправильно) return:
{"action":"fix","amount":NEW_AMOUNT_OR_NULL,"description":"NEW_DESC_OR_NULL"}
If text contains cancellation (отмени/отменить/bekor) return:
{"action":"cancel"}"""

_PHOTO_SYS = """Read this receipt or financial document.
Return ONLY valid JSON:
{"type":"exp","amount":50000,"description":"Store name","category":"🛒 Магазин","currency":"UZS","items":["item - price"]}"""

def _chat(system: str, user_content, max_tokens=400) -> str:
    r = client.chat.completions.create(
        model=OR_MODEL,
        max_tokens=max_tokens,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user',   'content': user_content},
        ]
    )
    return r.choices[0].message.content.strip()

def build_advisor_system(user: dict, lang: str) -> str:
    name     = user.get('name', '')
    goal     = user.get('goal', '')
    income   = user.get('income_amt', 0)
    cur      = user.get('income_currency', 'UZS')
    side     = user.get('side_income', 0)
    freq     = user.get('income_freq', '')

    if lang == 'uz':
        goal_uz = goal if goal else "yo'q"
        return (f"Siz Finora — {name} ning shaxsiy moliyaviy do'stisiz. "
                f"Uning daromadi: {income} {cur} ({freq})" +
                (f", qo'shimcha: {side} {cur}" if side else '') +
                f". Maqsadi: {goal_uz}. "
                f"O'zbek tilida gapiring. Qisqa, amaliy, do'stona. "
                f"Emoji ishlating. O'zbekistondagi real moliyaviy vositalarni (Kapitalbank, Hamkorbank, UZSE, oltin) maslahat bering.")
    else:
        return (f"Ты — Finora, личный финансовый друг {name}. "
                f"Его/её доход: {income} {cur} ({freq})" +
                (f", доп. доход: {side} {cur}" if side else '') +
                f". Цель: {goal or 'не задана'}. "
                f"Говори по-русски. Коротко, практично, по-дружески, с теплом. "
                f"Используй эмодзи. Советуй реальные инструменты Узбекистана: "
                f"депозиты (Kapitalbank, Hamkorbank), UZSE, золото, недвижимость.")

async def ai_parse(text: str) -> dict | None:
    try:
        raw = await asyncio.to_thread(_chat, _PARSE_SYS, text, 300)
        raw = raw.replace('```json', '').replace('```', '').strip()
        return json.loads(raw)
    except:
        return None

async def ai_parse_photo(img_bytes: bytes, mime: str) -> dict | None:
    try:
        b64     = base64.b64encode(img_bytes).decode()
        content = [
            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}},
            {'type': 'text', 'text': 'Read this receipt'},
        ]
        raw = await asyncio.to_thread(_chat, _PHOTO_SYS, content, 300)
        raw = raw.replace('```json', '').replace('```', '').strip()
        return json.loads(raw)
    except:
        return None

async def ai_advice(uid: int, lang: str) -> str:
    user  = get_user(uid)
    rows  = get_recent(uid)
    stats = get_stats(uid)
    rates = await asyncio.to_thread(get_rates)

    prompt = (f"Данные пользователя:\n"
              f"Доходы всего: {uzs(stats['inc'])}\n"
              f"Расходы всего: {uzs(stats['exp'])}\n"
              f"Баланс: {uzs(stats['inc'] - stats['exp'])}\n"
              f"Этот месяц — доходы: {uzs(stats['m_inc'])}, расходы: {uzs(stats['m_exp'])}\n\n"
              f"Топ категории расходов этого месяца:\n" +
              '\n'.join(f"  {cat}: {uzs(amt)}" for cat, amt in stats['cats']) +
              f"\n\nПоследние транзакции:\n" +
              '\n'.join(f"{'➕' if r[0] == 'inc' else '➖'} {uzs(r[1])} — {r[2]} ({r[3]})" for r in rows[:20]))
    try:
        sys_prompt = build_advisor_system(user, lang)
        return await asyncio.to_thread(_chat, sys_prompt, prompt, 700)
    except:
        return '❌ Ошибка.' if lang == 'ru' else '❌ Xatolik.'

async def ai_chat(uid: int, lang: str, text: str) -> str:
    user  = get_user(uid)
    stats = get_stats(uid)

    ctx = (f"Финансы: доходы {uzs(stats['inc'])}, расходы {uzs(stats['exp'])}, "
           f"баланс {uzs(stats['inc'] - stats['exp'])}. "
           f"Этот месяц: -{uzs(stats['m_exp'])} / +{uzs(stats['m_inc'])}.")
    sys_prompt = build_advisor_system(user, lang) + f"\n\nКонтекст: {ctx}"
    try:
        return await asyncio.to_thread(_chat, sys_prompt, text, 600)
    except:
        return '❌ Ошибка.' if lang == 'ru' else '❌ Xatolik.'

# ────────────────────────── VOICE (Groq Whisper — free!) ──────────
def _transcribe_sync(ogg_path: str, lang: str) -> str | None:
    """Sync transcribe — runs in thread pool to avoid blocking event loop."""
    with open(ogg_path, 'rb') as f:
        ogg_bytes = f.read()
    hint = 'ru' if lang == 'ru' else 'uz'

    # 1️⃣ Try Groq (free, fast — whisper-large-v3)
    if GROQ_KEY:
        try:
            resp = requests.post(
                'https://api.groq.com/openai/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {GROQ_KEY}'},
                files={'file': ('voice.ogg', ogg_bytes, 'audio/ogg; codecs=opus')},
                data={'model': 'whisper-large-v3', 'language': hint, 'response_format': 'json'},
                timeout=30
            )
            if resp.status_code == 200:
                text = resp.json().get('text', '').strip()
                if text:
                    return text
            logger.warning(f'Groq Whisper error: {resp.status_code} {resp.text[:200]}')
        except Exception as e:
            logger.warning(f'Groq transcribe error: {e}')

    # 2️⃣ Fallback: OpenAI Whisper
    if OPENAI_KEY:
        try:
            resp = requests.post(
                'https://api.openai.com/v1/audio/transcriptions',
                headers={'Authorization': f'Bearer {OPENAI_KEY}'},
                files={'file': ('voice.ogg', ogg_bytes, 'audio/ogg')},
                data={'model': 'whisper-1', 'language': hint},
                timeout=30
            )
            if resp.status_code == 200:
                return resp.json().get('text', '').strip() or None
            logger.warning(f'OpenAI Whisper error: {resp.status_code} {resp.text[:200]}')
        except Exception as e:
            logger.warning(f'OpenAI transcribe error: {e}')

    return None

async def transcribe(ogg_path: str, lang: str) -> str | None:
    return await asyncio.to_thread(_transcribe_sync, ogg_path, lang)

# ────────────────────────── FORMATTERS ────────────────────────────
def fmt_tx_msg(parsed: dict, lang: str, rates: dict) -> str:
    cur   = parsed.get('currency', 'UZS')
    amt   = parsed['amount']
    sign  = '+' if parsed['type'] == 'inc' else '-'
    label = tx('ru', 'type_inc') if parsed['type'] == 'inc' else tx('ru', 'type_exp')
    if lang == 'uz':
        label = tx('uz', 'type_inc') if parsed['type'] == 'inc' else tx('uz', 'type_exp')

    amt_str = fmt_amount(amt, cur, rates)
    text = (f"✅ {'Yozib oldim' if lang == 'uz' else 'Записала'}!\n\n"
            f"{label}: `{sign}{amt_str}`\n"
            f"📝 {parsed.get('description', '')}\n"
            f"🏷 {parsed.get('category', '')}")

    items = parsed.get('items', [])
    if items:
        text += '\n\n📄 ' + '\n'.join(f"• {i}" for i in items[:8])
    return text

# ────────────────────────── ONBOARDING ────────────────────────────
async def send_onboarding_step(chat_id: int, uid: int, state: str, context):
    u    = get_user(uid)
    lang = u.get('language', 'ru')
    name = u.get('name', '')

    if state == STATE_NAME:
        await context.bot.send_message(chat_id, tx(lang, 'ask_name'), parse_mode='Markdown')

    elif state == STATE_INCOME_FREQ:
        kb = [[
            InlineKeyboardButton(tx(lang, 'freq_daily'),    callback_data='freq_daily'),
            InlineKeyboardButton(tx(lang, 'freq_weekly'),   callback_data='freq_weekly'),
        ], [
            InlineKeyboardButton(tx(lang, 'freq_monthly'),  callback_data='freq_monthly'),
            InlineKeyboardButton(tx(lang, 'freq_irregular'),callback_data='freq_irregular'),
        ]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_income_freq', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_INCOME_AMT:
        await context.bot.send_message(chat_id, tx(lang, 'ask_income_amt'), parse_mode='Markdown')

    elif state == STATE_CURRENCY:
        kb = [[
            InlineKeyboardButton(tx(lang, 'cur_uzs'), callback_data='cur_UZS'),
            InlineKeyboardButton(tx(lang, 'cur_usd'), callback_data='cur_USD'),
            InlineKeyboardButton(tx(lang, 'cur_rub'), callback_data='cur_RUB'),
        ]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_currency'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_SIDE_HUSTLE:
        kb = [[
            InlineKeyboardButton(tx(lang, 'yes'), callback_data='side_yes'),
            InlineKeyboardButton(tx(lang, 'no'),  callback_data='side_no'),
        ]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_side_hustle'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_SIDE_AMT:
        await context.bot.send_message(chat_id, tx(lang, 'ask_side_amt'), parse_mode='Markdown')

    elif state == STATE_GOAL:
        kb = [
            [InlineKeyboardButton(tx(lang, 'goal_save'),     callback_data='goal_save'),
             InlineKeyboardButton(tx(lang, 'goal_buy'),      callback_data='goal_buy')],
            [InlineKeyboardButton(tx(lang, 'goal_invest'),   callback_data='goal_invest'),
             InlineKeyboardButton(tx(lang, 'goal_debt'),     callback_data='goal_debt')],
            [InlineKeyboardButton(tx(lang, 'goal_business'), callback_data='goal_business')],
            [InlineKeyboardButton(tx(lang, 'goal_none'),     callback_data='goal_none')],
        ]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_goal', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_GOAL_CUSTOM:
        await context.bot.send_message(chat_id, tx(lang, 'ask_goal_custom'), parse_mode='Markdown')

    elif state == STATE_NOTIFY_WHY:
        kb = [
            [InlineKeyboardButton(tx(lang, 'notify_18'), callback_data='notify_18:00'),
             InlineKeyboardButton(tx(lang, 'notify_19'), callback_data='notify_19:00')],
            [InlineKeyboardButton(tx(lang, 'notify_20'), callback_data='notify_20:00'),
             InlineKeyboardButton(tx(lang, 'notify_21'), callback_data='notify_21:00')],
            [InlineKeyboardButton(tx(lang, 'notify_22'), callback_data='notify_22:00'),
             InlineKeyboardButton(tx(lang, 'notify_23'), callback_data='notify_23:00')],
            [InlineKeyboardButton(tx(lang, 'notify_custom'), callback_data='notify_custom')],
        ]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_notify_why', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_NOTIFY_TIME:
        await context.bot.send_message(chat_id, tx(lang, 'ask_notify_time'), parse_mode='Markdown')

# ────────────────────────── HANDLERS ──────────────────────────────
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = upd.effective_user.id
    chat_id = upd.effective_chat.id
    get_user(uid)  # ensure row exists
    set_user(uid, onboarding_state=STATE_LANG, onboarding_done=0)

    kb = [[
        InlineKeyboardButton('🇷🇺 Русский', callback_data='lang_ru'),
        InlineKeyboardButton('🇺🇿 O\'zbek',  callback_data='lang_uz'),
    ]]
    await upd.message.reply_text(
        T['ru']['choose_lang'],
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )

async def cmd_help(upd: Update, _):
    uid = upd.effective_user.id
    await upd.message.reply_text(tx(uid, 'help_text'), parse_mode='Markdown')

async def cmd_stats(upd: Update, _):
    uid  = upd.effective_user.id
    u    = get_user(uid)
    lang = u.get('language', 'ru')
    name = u.get('name', '')
    s    = get_stats(uid)

    if s['count'] == 0:
        await upd.message.reply_text(tx(lang, 'no_data')); return

    rates = get_rates()
    bal   = s['inc'] - s['exp']
    icon  = '✅' if bal >= 0 else '⚠️'

    cat_lines = ''
    if s['cats']:
        cat_lines = '\n\n📂 *Топ расходов месяца:*\n' + '\n'.join(
            f"  {cat}: `{uzs(amt)}`" for cat, amt in s['cats']
        )

    goal = u.get('goal', '')
    goal_line = f"\n\n🎯 Цель: _{goal}_" if goal else ''

    msg = (f"{tx(lang, 'stats_hdr', name=name)}\n\n"
           f"*{tx(lang, 'all_time')}*\n"
           f"{tx(lang, 'income_lbl')}: `{uzs(s['inc'])}`\n"
           f"{tx(lang, 'expense_lbl')}: `{uzs(s['exp'])}`\n"
           f"{icon} {tx(lang, 'balance_lbl')}: `{uzs(bal)}`\n\n"
           f"*{tx(lang, 'this_month')}*\n"
           f"{tx(lang, 'income_lbl')}: `{uzs(s['m_inc'])}`\n"
           f"{tx(lang, 'expense_lbl')}: `{uzs(s['m_exp'])}`\n\n"
           f"📋 {tx(lang, 'records_lbl')}: {s['count']}"
           f"{cat_lines}{goal_line}")
    await upd.message.reply_text(msg, parse_mode='Markdown')

async def cmd_history(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    rows = get_history(uid)
    if not rows:
        await upd.message.reply_text(tx(lang, 'no_data')); return

    lines = [f"{tx(lang, 'hist_hdr')}\n"]
    for id_, type_, amount, desc, cat, cur, dt in rows:
        sign  = '➕' if type_ == 'inc' else '➖'
        amt_s = fmt_amount(amount, cur or 'UZS', {})
        date  = (dt or '')[:10]
        lines.append(f"{sign} `{amt_s}` — {desc or cat}\n   _{cat}_ · {date}\n")
    await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')

async def cmd_rate(upd: Update, _):
    uid   = upd.effective_user.id
    lang  = get_lang(uid)
    rates = get_rates()
    if not rates:
        await upd.message.reply_text(tx(lang, 'rate_err')); return

    lines = [f"{tx(lang, 'rate_hdr')}\n"]
    for ccy, d in rates.items():
        arrow = '🔺' if d['diff'] > 0 else ('🔻' if d['diff'] < 0 else '➡️')
        lines.append(f"{arrow} *{ccy}* = `{uzs(d['rate'])}` ({d['diff']:+.2f})")
    lines.append(f"\n_{tx(lang, 'updated')}: {datetime.now().strftime('%d.%m %H:%M')}_")
    await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')

async def cmd_advice(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    await ctx.bot.send_chat_action(upd.effective_chat.id, constants.ChatAction.TYPING)
    msg  = await upd.message.reply_text(tx(lang, 'advice_wait'))
    text = await ai_advice(uid, lang)
    text = text.replace('**', '*')
    await msg.edit_text(f"{tx(lang, 'advice_hdr')}\n\n{text}", parse_mode='Markdown')

async def cmd_clear(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    kb   = [[
        InlineKeyboardButton(tx(lang, 'yes_del'),    callback_data='clear_yes'),
        InlineKeyboardButton(tx(lang, 'no_cancel'),  callback_data='clear_no'),
    ]]
    await upd.message.reply_text(
        tx(lang, 'confirm_clear'),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )

async def cmd_settings(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    kb = [
        [InlineKeyboardButton(tx(lang, 'set_notify'),   callback_data='settings_notify')],
        [InlineKeyboardButton(tx(lang, 'set_goal'),     callback_data='settings_goal')],
        [InlineKeyboardButton(tx(lang, 'set_name'),     callback_data='settings_name')],
        [InlineKeyboardButton(tx(lang, 'cancel_notify'),callback_data='settings_no_notify')],
    ]
    await upd.message.reply_text(
        tx(lang, 'settings_hdr'),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )

async def cmd_dashboard(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    dashboard_url = os.getenv('DASHBOARD_URL', 'https://finora-bot.up.railway.app')
    
    text = ('💎 *Твой личный дашборд готов!*\n\n'
            'Нажми кнопку ниже чтобы открыть прямо в Telegram!\n\n'
            '📊 Подробная статистика\n'
            '📈 Графики доходов и расходов\n'
            '🎯 Прогресс к финансовой цели\n'
            '📋 Все транзакции')
    
    if lang == 'uz':
        text = ('💎 *Shaxsiy dashboard tayyor!*\n\n'
                'Telegramda ochish uchun pastdagi tugmani bosing!\n\n'
                '📊 Batafsil statistika\n'
                '📈 Daromad va xarajat grafiklari\n'
                '🎯 Maqsadga erishish jarayoni\n'
                '📋 Barcha tranzaktsiyalar')
    
    from telegram import WebAppInfo
    kb = [[InlineKeyboardButton(
        '📊 Открыть Dashboard' if lang == 'ru' else '📊 Dashboardni ochish',
        web_app=WebAppInfo(url=dashboard_url)
    )]]
    
    await upd.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )

async def cmd_reset(upd: Update, _):
    uid = upd.effective_user.id
    # Только для админа (твой ID)
    if uid != 1326256223:
        await upd.message.reply_text('❌ У тебя нет доступа к этой команде.')
        return
    
    # Сброс онбординга
    set_user(uid, onboarding_state=STATE_LANG, onboarding_done=0)
    await upd.message.reply_text(
        '🔄 *Онбординг сброшен!*\n\n'
        'Теперь можешь заново пройти регистрацию.\n'
        'Напиши /start чтобы начать!',
        parse_mode='Markdown'
    )

async def on_text(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = upd.effective_user.id
    chat_id = upd.effective_chat.id
    u       = get_user(uid)
    text    = upd.message.text.strip()

    if text.startswith('/'): return

    state = u.get('onboarding_state', STATE_LANG)
    done  = u.get('onboarding_done', 0)
    lang  = u.get('language', 'ru')

    # ─── ONBOARDING TEXT STEPS ───
    if not done:
        if state == STATE_NAME:
            set_user(uid, name=text, onboarding_state=STATE_INCOME_FREQ)
            await send_onboarding_step(chat_id, uid, STATE_INCOME_FREQ, ctx)
            return

        elif state == STATE_INCOME_AMT:
            try:
                amt = float(text.replace(' ', '').replace(',', '.'))
                set_user(uid, income_amt=amt, onboarding_state=STATE_CURRENCY)
                await send_onboarding_step(chat_id, uid, STATE_CURRENCY, ctx)
            except:
                await upd.message.reply_text('❌ Напиши число, например: *500000*', parse_mode='Markdown')
            return

        elif state == STATE_SIDE_AMT:
            try:
                amt = float(text.replace(' ', '').replace(',', '.'))
                set_user(uid, side_income=amt, onboarding_state=STATE_GOAL)
                await send_onboarding_step(chat_id, uid, STATE_GOAL, ctx)
            except:
                await upd.message.reply_text('❌ Напиши число', parse_mode='Markdown')
            return

        elif state == STATE_GOAL_CUSTOM:
            set_user(uid, goal=text, onboarding_state=STATE_NOTIFY_WHY)
            await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
            return

        elif state == STATE_NOTIFY_TIME:
            # validate HH:MM
            try:
                parts = text.strip().split(':')
                h, m  = int(parts[0]), int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    t_str = f"{h:02d}:{m:02d}"
                    set_user(uid, notify_time=t_str, notify_enabled=1, onboarding_state=STATE_DONE, onboarding_done=1)
                    name  = get_user(uid).get('name', '')
                    await upd.message.reply_text(tx(lang, 'notify_set', time=t_str), parse_mode='Markdown')
                    await upd.message.reply_text(tx(lang, 'welcome_done', name=name), parse_mode='Markdown')
                else:
                    raise ValueError
            except:
                await upd.message.reply_text('❌ Укажи время в формате *ЧЧ:ММ*, например *20:30*', parse_mode='Markdown')
            return

        return  # wait for button press in other states

    # ─── MAIN BOT LOGIC ───
    await ctx.bot.send_chat_action(chat_id, constants.ChatAction.TYPING)
    msg    = await upd.message.reply_text(tx(lang, 'processing'))
    parsed = await ai_parse(text)

    if not parsed:
        await msg.edit_text(tx(lang, 'parse_error'), parse_mode='Markdown'); return

    # Handle fix/cancel actions
    action = parsed.get('action')
    if action == 'cancel':
        deleted = delete_last_tx(uid)
        reply   = ('✅ Последняя запись удалена.' if lang == 'ru' else '✅ Oxirgi yozuv o\'chirildi.') if deleted else tx(lang, 'no_data')
        await msg.edit_text(reply); return

    if action == 'fix':
        last = get_last_tx(uid)
        if not last:
            await msg.edit_text(tx(lang, 'no_data')); return
        tx_id  = last[0]
        new_amt = parsed.get('amount')
        new_desc= parsed.get('description')
        update_tx(tx_id, amount=new_amt, description=new_desc)
        await msg.edit_text(tx(lang, 'fixed')); return

    if 'amount' not in parsed:
        # Try as a free AI chat message
        reply = await ai_chat(uid, lang, text)
        reply = reply.replace('**', '*')
        await msg.edit_text(reply, parse_mode='Markdown'); return

    rates = get_rates()
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description', ''), parsed.get('category', '❓'),
           parsed.get('currency', 'UZS'),
           json.dumps(parsed.get('items', []), ensure_ascii=False))
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')

async def on_photo(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    u    = get_user(uid)
    lang = u.get('language', 'ru')
    await ctx.bot.send_chat_action(upd.effective_chat.id, constants.ChatAction.TYPING)
    msg  = await upd.message.reply_text(tx(lang, 'processing'))

    photo = upd.message.photo[-1]
    file  = await photo.get_file()
    data  = bytes(await file.download_as_bytearray())

    parsed = await ai_parse_photo(data, 'image/jpeg')
    if not parsed or 'amount' not in parsed:
        await msg.edit_text(tx(lang, 'photo_error')); return

    rates = get_rates()
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description', ''), parsed.get('category', '🛒 Магазин'),
           parsed.get('currency', 'UZS'),
           json.dumps(parsed.get('items', []), ensure_ascii=False))
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')

async def on_voice(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid     = upd.effective_user.id
    chat_id = upd.effective_chat.id
    u       = get_user(uid)
    lang    = u.get('language', 'ru')
    state   = u.get('onboarding_state', STATE_LANG)
    done    = u.get('onboarding_done', 0)
    
    await ctx.bot.send_chat_action(chat_id, constants.ChatAction.TYPING)
    msg = await upd.message.reply_text(tx(lang, 'processing'))

    vfile = await upd.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as f:
        await vfile.download_to_drive(f.name)
        ogg = f.name

    transcript = await transcribe(ogg, lang)
    Path(ogg).unlink(missing_ok=True)

    if not transcript:
        await msg.edit_text(tx(lang, 'voice_error')); return

    # ─── ONBOARDING VOICE HANDLING ───
    if not done:
        # If still choosing language, prompt to use buttons
        if state == STATE_LANG:
            await msg.edit_text(
                f'🎤 _{transcript}_\n\n'
                '👆 Сначала выбери язык кнопками выше!\n\n'
                '👆 Avval yuqoridagi tugmalar bilan tilni tanlang!',
                parse_mode='Markdown'
            )
            return
        
        if state == STATE_NAME:
            set_user(uid, name=transcript, onboarding_state=STATE_INCOME_FREQ)
            await msg.edit_text(f'🎤 _{transcript}_\n\n✅ Отлично!', parse_mode='Markdown')
            await send_onboarding_step(chat_id, uid, STATE_INCOME_FREQ, ctx)
            return

        elif state == STATE_INCOME_AMT:
            # Extract number from voice using AI
            parsed = await ai_parse(f"Сумма дохода: {transcript}")
            if parsed and 'amount' in parsed:
                amt = parsed['amount']
                set_user(uid, income_amt=amt, onboarding_state=STATE_CURRENCY)
                await msg.edit_text(f'🎤 _{transcript}_\n\n✅ Записал: {amt}', parse_mode='Markdown')
                await send_onboarding_step(chat_id, uid, STATE_CURRENCY, ctx)
            else:
                await msg.edit_text(f'🎤 _{transcript}_\n\n❌ Не понял сумму. Попробуй ещё раз или напиши текстом.', parse_mode='Markdown')
            return

        elif state == STATE_SIDE_AMT:
            parsed = await ai_parse(f"Сумма дохода: {transcript}")
            if parsed and 'amount' in parsed:
                amt = parsed['amount']
                set_user(uid, side_income=amt, onboarding_state=STATE_GOAL)
                await msg.edit_text(f'🎤 _{transcript}_\n\n✅ Записал: {amt}', parse_mode='Markdown')
                await send_onboarding_step(chat_id, uid, STATE_GOAL, ctx)
            else:
                await msg.edit_text(f'🎤 _{transcript}_\n\n❌ Не понял сумму. Попробуй ещё раз.', parse_mode='Markdown')
            return

        elif state == STATE_GOAL_CUSTOM:
            set_user(uid, goal=transcript, onboarding_state=STATE_NOTIFY_WHY)
            await msg.edit_text(f'🎤 _{transcript}_\n\n✅ Отлично!', parse_mode='Markdown')
            await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
            return

        elif state == STATE_NOTIFY_TIME:
            # Extract time from voice
            import re
            time_match = re.search(r'(\d{1,2})[:\s](\d{2})', transcript)
            if time_match:
                h, m = int(time_match.group(1)), int(time_match.group(2))
                if 0 <= h <= 23 and 0 <= m <= 59:
                    t_str = f"{h:02d}:{m:02d}"
                    set_user(uid, notify_time=t_str, notify_enabled=1, onboarding_state=STATE_DONE, onboarding_done=1)
                    name = get_user(uid).get('name', '')
                    await msg.edit_text(f'🎤 _{transcript}_\n\n' + tx(lang, 'notify_set', time=t_str), parse_mode='Markdown')
                    await ctx.bot.send_message(chat_id, tx(lang, 'welcome_done', name=name), parse_mode='Markdown')
                    return
            await msg.edit_text(f'🎤 _{transcript}_\n\n❌ Не понял время. Скажи например "двадцать один ноль ноль" или напиши 21:00', parse_mode='Markdown')
            return

        # For other onboarding states, prompt to use buttons
        await msg.edit_text(f'🎤 _{transcript}_\n\n👆 Пожалуйста, выбери один из вариантов кнопками выше', parse_mode='Markdown')
        return

    # ─── MAIN BOT LOGIC (after onboarding) ───
    parsed = await ai_parse(transcript)
    if not parsed:
        await msg.edit_text(f'🎤 _{transcript}_\n\n{tx(lang, "parse_error")}', parse_mode='Markdown'); return

    # handle fix/cancel via voice
    action = parsed.get('action')
    if action == 'cancel':
        delete_last_tx(uid)
        await msg.edit_text(f'🎤 _{transcript}_\n\n✅ Удалено.', parse_mode='Markdown'); return
    if action == 'fix':
        last = get_last_tx(uid)
        if last:
            update_tx(last[0], amount=parsed.get('amount'), description=parsed.get('description'))
        await msg.edit_text(f'🎤 _{transcript}_\n\n{tx(lang, "fixed")}', parse_mode='Markdown'); return

    if 'amount' not in parsed:
        reply = await ai_chat(uid, lang, transcript)
        reply = reply.replace('**', '*')
        await msg.edit_text(f'🎤 _{transcript}_\n\n{reply}', parse_mode='Markdown'); return

    rates = get_rates()
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description', ''), parsed.get('category', '❓'),
           parsed.get('currency', 'UZS'),
           json.dumps(parsed.get('items', []), ensure_ascii=False))
    await msg.edit_text(f'🎤 _{transcript}_\n\n' + fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')

async def on_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q       = upd.callback_query
    uid     = q.from_user.id
    chat_id = q.message.chat_id
    data    = q.data
    await q.answer()

    u    = get_user(uid)
    lang = u.get('language', 'ru')
    state= u.get('onboarding_state', STATE_LANG)
    done = u.get('onboarding_done', 0)

    # ─── LANGUAGE SELECTION ───
    if data.startswith('lang_'):
        chosen = data[5:]
        set_user(uid, language=chosen, onboarding_state=STATE_NAME)
        await q.edit_message_text(T[chosen]['ask_name'], parse_mode='Markdown')
        return

    # ─── ONBOARDING CALLBACKS ───
    if not done:
        if data.startswith('freq_'):
            freq_map = {
                'freq_daily':    ('каждый день', 'har kun'),
                'freq_weekly':   ('раз в неделю', 'haftada bir'),
                'freq_monthly':  ('раз в месяц', 'oyda bir'),
                'freq_irregular':('нерегулярно', 'tartibsiz'),
            }
            freq_val = freq_map.get(data, ('', ''))[0 if lang == 'ru' else 1]
            set_user(uid, income_freq=freq_val, onboarding_state=STATE_INCOME_AMT)
            await q.edit_message_text(tx(lang, 'ask_income_amt'), parse_mode='Markdown')
            return

        if data.startswith('cur_'):
            cur = data[4:]
            set_user(uid, income_currency=cur, onboarding_state=STATE_SIDE_HUSTLE)
            await q.edit_message_text('✅')
            await send_onboarding_step(chat_id, uid, STATE_SIDE_HUSTLE, ctx)
            return

        if data == 'side_yes':
            set_user(uid, onboarding_state=STATE_SIDE_AMT)
            await q.edit_message_text(tx(lang, 'ask_side_amt'), parse_mode='Markdown')
            return

        if data == 'side_no':
            set_user(uid, side_income=0, onboarding_state=STATE_GOAL)
            await q.edit_message_text('✅')
            await send_onboarding_step(chat_id, uid, STATE_GOAL, ctx)
            return

        if data.startswith('goal_'):
            goal_map = {
                'goal_save':     ('Накопить деньги', 'Pul to\'plash'),
                'goal_buy':      ('Купить что-то конкретное', 'Biror narsa sotib olish'),
                'goal_invest':   ('Начать инвестировать', 'Investitsiya boshlash'),
                'goal_debt':     ('Закрыть долги/кредиты', 'Qarz to\'lash'),
                'goal_business': ('Развить бизнес', 'Biznesni rivojlantirish'),
            }
            if data == 'goal_none':
                name = u.get('name', '')
                set_user(uid, onboarding_state=STATE_GOAL_CUSTOM)
                await q.edit_message_text(tx(lang, 'no_goal_speech', name=name), parse_mode='Markdown')
                return
            elif data in goal_map:
                goal_val = goal_map[data][0 if lang == 'ru' else 1]
                set_user(uid, goal=goal_val, onboarding_state=STATE_NOTIFY_WHY)
                await q.edit_message_text('✅')
                await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
                return

        if data.startswith('notify_') and data != 'notify_custom':
            t_str = data[7:]  # e.g. "21:00"
            set_user(uid, notify_time=t_str, notify_enabled=1, onboarding_state=STATE_DONE, onboarding_done=1)
            name = u.get('name', '')
            await q.edit_message_text(tx(lang, 'notify_set', time=t_str), parse_mode='Markdown')
            await ctx.bot.send_message(chat_id, tx(lang, 'welcome_done', name=name), parse_mode='Markdown')
            return

        if data == 'notify_custom':
            set_user(uid, onboarding_state=STATE_NOTIFY_TIME)
            await q.edit_message_text(tx(lang, 'ask_notify_time'), parse_mode='Markdown')
            return

    # ─── SETTINGS CALLBACKS ───
    if data == 'settings_notify':
        set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
        await q.edit_message_text('✅')
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
        return

    if data == 'settings_goal':
        set_user(uid, onboarding_state=STATE_GOAL)
        await q.edit_message_text('✅')
        await send_onboarding_step(chat_id, uid, STATE_GOAL, ctx)
        return

    if data == 'settings_name':
        set_user(uid, onboarding_state=STATE_NAME, onboarding_done=0)
        await q.edit_message_text(tx(lang, 'ask_name'), parse_mode='Markdown')
        return

    if data == 'settings_no_notify':
        set_user(uid, notify_enabled=0)
        await q.edit_message_text(tx(lang, 'notify_disabled'), parse_mode='Markdown')
        return

    # ─── MAIN CALLBACKS ───
    if data == 'clear_yes':
        clear_data(uid)
        await q.edit_message_text(tx(lang, 'cleared'))
    elif data == 'clear_no':
        await q.edit_message_text(tx(lang, 'cancelled'))

# ────────────────────────── DAILY REMINDER JOB ────────────────────
async def send_reminders(context: ContextTypes.DEFAULT_TYPE):
    now_tz  = datetime.now(TZ)
    now_hm  = now_tz.strftime('%H:%M')
    users   = get_all_users_with_notify()
    for uid, name, lang, notify_time in users:
        if notify_time == now_hm:
            try:
                await context.bot.send_message(
                    uid,
                    tx(lang, 'remind_msg', name=name or 'друг'),
                    parse_mode='Markdown'
                )
            except Exception as e:
                logger.warning(f'Reminder failed for {uid}: {e}')

# ──────────────────────── FLASK WEB DASHBOARD ────────────────────
flask_app = Flask(__name__, template_folder='templates')
flask_app.secret_key = os.getenv('FLASK_SECRET_KEY', '7f3b9d2a8e5c1f6a4b7e9c3d5f8a2b4c6e1d3f5a7b9c2e4f6a8b1c3d5e7f9a2b')

def verify_telegram_auth(auth_data: dict) -> bool:
    """Проверка данных от Telegram Login Widget"""
    check_hash = auth_data.get('hash')
    if not check_hash:
        return False
    
    auth_copy = dict(auth_data)
    del auth_copy['hash']
    
    data_check_arr = [f'{k}={v}' for k, v in sorted(auth_copy.items())]
    data_check_string = '\n'.join(data_check_arr)
    
    secret_key = hashlib.sha256(BOT_TOKEN.encode()).digest()
    hash_value = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    
    if hash_value != check_hash:
        return False
    
    # Проверка времени (данные действительны 24 часа)
    auth_date = int(auth_data.get('auth_date', 0))
    if datetime.now().timestamp() - auth_date > 86400:
        return False
    
    return True

def get_user_stats_web(user_id: int) -> dict:
    """Получить статистику для веб-дашборда"""
    month = datetime.now(TZ).strftime('%Y-%m')
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            # Общая статистика
            c.execute('SELECT type, SUM(amount) as total FROM transactions WHERE user_id=%s GROUP BY type', (user_id,))
            totals = {row['type']: float(row['total']) for row in c.fetchall()}
            
            # Статистика за месяц
            c.execute(
                "SELECT type, SUM(amount) as total FROM transactions WHERE user_id=%s AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s GROUP BY type",
                (user_id, month)
            )
            month_totals = {row['type']: float(row['total']) for row in c.fetchall()}
            
            # Топ категории расходов
            c.execute(
                "SELECT category, SUM(amount) as total FROM transactions WHERE user_id=%s AND type='exp' AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s GROUP BY category ORDER BY total DESC LIMIT 10",
                (user_id, month)
            )
            categories = [dict(row) for row in c.fetchall()]
            
            # Последние транзакции
            c.execute(
                'SELECT id, type, amount, description, category, currency, created_at FROM transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT 50',
                (user_id,)
            )
            transactions = [dict(row) for row in c.fetchall()]
            
            # Информация о пользователе
            c.execute('SELECT * FROM users WHERE user_id=%s', (user_id,))
            user_info = dict(c.fetchone() or {})
    
    return {
        'total_income': totals.get('inc', 0),
        'total_expense': totals.get('exp', 0),
        'balance': totals.get('inc', 0) - totals.get('exp', 0),
        'month_income': month_totals.get('inc', 0),
        'month_expense': month_totals.get('exp', 0),
        'month_balance': month_totals.get('inc', 0) - month_totals.get('exp', 0),
        'categories': categories,
        'transactions': transactions,
        'user_info': user_info
    }

@flask_app.route('/')
def web_index():
    """Главная страница - дашборд для Telegram WebApp"""
    # For Telegram WebApp - user data comes from initData
    return render_template('dashboard.html')

@flask_app.route('/api/stats')
def api_user_stats():
    """API для получения статистики пользователя"""
    import urllib.parse
    
    # Get initData from Telegram WebApp
    init_data = request.args.get('initData', '')
    if not init_data:
        return jsonify({'error': 'No initData'}), 401
    
    # Parse initData
    try:
        params = urllib.parse.parse_qs(init_data)
        user_json = params.get('user', [''])[0]
        if not user_json:
            return jsonify({'error': 'No user data'}), 401
        
        user_data = json.loads(user_json)
        user_id = int(user_data['id'])
        user_name = user_data.get('first_name', 'Пользователь')
        
        # Get stats
        stats = get_user_stats_web(user_id)
        stats['user_name'] = user_name
        
        # Convert datetime to strings
        for tx in stats['transactions']:
            if tx.get('created_at'):
                tx['created_at'] = tx['created_at'].strftime('%d.%m.%Y %H:%M')
        
        return jsonify(stats)
    except Exception as e:
        logger.error(f'Error getting stats: {e}')
        return jsonify({'error': str(e)}), 500

def run_flask():
    """Run Flask in a separate thread"""
    port = int(os.getenv('PORT', 5000))
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ──────────────────────── BOT UI SETUP ────────────────────────────
async def setup_bot_ui(app: Application):
    """Setup bot commands and menu button"""
    dashboard_url = os.getenv('DASHBOARD_URL', 'https://finora-bot.up.railway.app')
    
    # Ensure URL starts with https://
    if not dashboard_url.startswith('http'):
        dashboard_url = f'https://{dashboard_url}'
    
    # Set bot commands (подсказки команд)
    commands = [
        BotCommand('start', '🚀 Начать / Перезапустить'),
        BotCommand('stats', '📊 Статистика'),
        BotCommand('history', '📋 История транзакций'),
        BotCommand('advice', '🤖 AI-совет по финансам'),
        BotCommand('rate', '💱 Курс валют'),
        BotCommand('settings', '⚙️ Настройки'),
        BotCommand('help', '❓ Помощь'),
    ]
    await app.bot.set_my_commands(commands, language_code='ru')
    await app.bot.set_my_commands(commands, language_code='uz')
    
    # Set menu button (кнопка дашборда рядом с полем ввода)
    from telegram import WebAppInfo
    try:
        menu_button = MenuButtonWebApp(text='📊 Dashboard', web_app=WebAppInfo(url=dashboard_url))
        await app.bot.set_chat_menu_button(menu_button=menu_button)
        logger.info(f'✅ Bot UI configured: commands + menu button ({dashboard_url})')
    except Exception as e:
        logger.warning(f'Failed to set menu button: {e}')
        logger.info('✅ Bot UI configured: commands only')

# ────────────────────────── MAIN ──────────────────────────────────
def main():
    init_db()
    if not BOT_TOKEN:      raise ValueError('BOT_TOKEN not set')
    if not OPENROUTER_KEY: raise ValueError('OPENROUTER_KEY not set')
    if not DATABASE_URL and not DATABASE_PUBLIC_URL:
        raise ValueError('Set DATABASE_PUBLIC_URL or DATABASE_URL in Railway Variables')

    # Start Flask web server in background thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info('🌐 Flask dashboard started in background')

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler('start',     cmd_start))
    app.add_handler(CommandHandler('help',      cmd_help))
    app.add_handler(CommandHandler('stats',     cmd_stats))
    app.add_handler(CommandHandler('history',   cmd_history))
    app.add_handler(CommandHandler('rate',      cmd_rate))
    app.add_handler(CommandHandler('advice',    cmd_advice))
    app.add_handler(CommandHandler('clear',     cmd_clear))
    app.add_handler(CommandHandler('settings',  cmd_settings))
    app.add_handler(CommandHandler('dashboard', cmd_dashboard))
    app.add_handler(CommandHandler('reset',     cmd_reset))

    # Message handlers
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    # Daily reminder — runs every minute, checks time
    app.job_queue.run_repeating(send_reminders, interval=60, first=10)

    # Setup bot UI (commands + menu button)
    async def post_init(application: Application):
        await setup_bot_ui(application)
    
    app.post_init = post_init

    logger.info('🚀 Finora Bot is running!')
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
