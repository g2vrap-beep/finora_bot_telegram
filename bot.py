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
STATE_NAME_CONFIRM = 'name_confirm'
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
STATE_BUG_REPORT = 'bug_report'

# ─── DEBT STATES ───
STATE_DEBT_COUNT    = 'debt_count'    # Сколько кредитов?
STATE_DEBT_BANK     = 'debt_bank'     # Название банка
STATE_DEBT_AMT      = 'debt_amt'      # Сумма долга
STATE_DEBT_RATE     = 'debt_rate'     # Процентная ставка
STATE_DEBT_MONTHLY  = 'debt_monthly'  # Ежемесячный платёж
STATE_DEBT_DEADLINE = 'debt_deadline' # Срок погашения

# Карта навигации: текущий state → предыдущий state
ONBOARDING_BACK_MAP = {
    'name_confirm':  STATE_NAME,
    STATE_INCOME_FREQ: 'name_confirm',
    STATE_INCOME_AMT:  STATE_INCOME_FREQ,
    STATE_CURRENCY:    STATE_INCOME_AMT,
    STATE_SIDE_HUSTLE: STATE_CURRENCY,
    STATE_SIDE_AMT:    STATE_SIDE_HUSTLE,
    STATE_GOAL_CUSTOM: STATE_GOAL,
    STATE_NOTIFY_TIME: STATE_NOTIFY_WHY,
    STATE_DEBT_BANK:   STATE_DEBT_COUNT,
    STATE_DEBT_AMT:    STATE_DEBT_BANK,
    STATE_DEBT_RATE:   STATE_DEBT_AMT,
    STATE_DEBT_MONTHLY: STATE_DEBT_RATE,
    STATE_DEBT_DEADLINE: STATE_DEBT_MONTHLY,
}

def get_prev_state(uid: int, current_state: str, user_data: dict) -> str | None:
    """Получить предыдущий state с учётом динамической логики."""
    u = get_user(uid)

    if current_state == STATE_GOAL:
        return STATE_SIDE_AMT if u.get('side_income', 0) > 0 else STATE_SIDE_HUSTLE

    if current_state == STATE_NOTIFY_WHY:
        return STATE_GOAL_CUSTOM if u.get('goal', '') else STATE_GOAL

    return ONBOARDING_BACK_MAP.get(current_state)

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
        'welcome_done'     : ('🎉 *{name}*, добро пожаловать в Finora!\n\n'
                              'Теперь я знаю тебя и буду помогать управлять деньгами 💎\n\n'
                              '*Как это работает:*\n'
                              '📝 Напиши что потратил/заработал — я запишу\n'
                              '🎤 Скажи голосом — я пойму\n'
                              '📷 Отправь фото чека — прочитаю сам\n\n'
                              '*Команды:*\n'
                              '/stats — 📊 Статистика\n'
                              '/history — 📋 История\n'
                              '/advice — 🤖 Персональный совет\n'
                              '/rate — 💱 Курс валют\n'
                              '/settings — ⚙️ Настройки\n'
                              '/bug — 🐛 Сообщить об ошибке\n'
                              '/help — ❓ Помощь\n\n'
                              '_Просто напиши мне — и мы начнём!_ 🚀'),
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
        'welcome_done'     : ('🎉 *{name}*, Finoraga xush kelibsiz!\n\n'
                              'Endi sizni tanidim va pul boshqarishda yordam beraman 💎\n\n'
                              '*Qanday ishlaydi:*\n'
                              '📝 Nima sarflaganingizni yozing — yozib olaman\n'
                              '🎤 Ovoz bilan ayting — tushunaman\n'
                              '📷 Chek rasmini yuboring — o\'zim o\'qiyman\n\n'
                              '*Buyruqlar:*\n'
                              '/stats — 📊 Statistika\n'
                              '/history — 📋 Tarix\n'
                              '/advice — 🤖 Shaxsiy maslahat\n'
                              '/rate — 💱 Valyuta kursi\n'
                              '/settings — ⚙️ Sozlamalar\n'
                              '/bug — 🐛 Xatolik haqida xabar\n'
                              '/help — ❓ Yordam\n\n'
                              '_Menga yozing — boshlaymiz!_ 🚀'),
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
            c.execute('''CREATE TABLE IF NOT EXISTS bug_reports(
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                username TEXT,
                description TEXT,
                status TEXT DEFAULT 'open',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                resolved_at TIMESTAMPTZ
            )''')
            c.execute('''CREATE TABLE IF NOT EXISTS debts(
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                bank TEXT,
                amount FLOAT,
                rate FLOAT,
                monthly_payment FLOAT,
                deadline TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
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
    """Получить курсы с dollaruz.net (реальные рыночные). Fallback: CBU."""
    # 1️⃣ Попытка получить реальные курсы с dollaruz.net
    try:
        resp = requests.get(
            'https://dollaruz.net/api/currency',
            timeout=8,
            headers={'User-Agent': 'Mozilla/5.0'}
        )
        if resp.status_code == 200:
            data = resp.json()
            result = {}
            for ccy in ('USD', 'EUR', 'RUB'):
                entry = data.get(ccy, {})
                buy  = float(entry.get('buy', 0) or 0)
                sell = float(entry.get('sell', 0) or 0)
                if buy > 0 or sell > 0:
                    result[ccy] = {
                        'buy':  buy,
                        'sell': sell,
                        'avg':  round((buy + sell) / 2, 2) if buy and sell else (buy or sell),
                        'diff': 0,
                        'source': 'dollaruz'
                    }
            if result:
                return result
    except Exception as e:
        logger.warning(f'dollaruz.net error: {e}')

    # 2️⃣ Fallback: официальный курс ЦБ
    try:
        data = requests.get('https://cbu.uz/oz/arkhiv-kursov-valyut/json/', timeout=8).json()
        return {
            d['Ccy']: {
                'buy':  float(d['Rate']),
                'sell': float(d['Rate']),
                'avg':  float(d['Rate']),
                'diff': float(d.get('Diff', 0)),
                'source': 'cbu'
            }
            for d in data if d.get('Ccy') in ('USD', 'EUR', 'RUB')
        }
    except:
        return {}

def uzs(n: float) -> str:
    return f"{n:,.0f}".replace(',', ' ') + " so'm"

def fmt_amount(amount: float, cur: str, rates: dict) -> str:
    if cur == 'USD':
        r = rates.get('USD', {}).get('avg', 0)
        return f"${amount:,.2f}" + (f" ({uzs(amount * r)})" if r else '')
    elif cur == 'RUB':
        return f"₽{amount:,.0f}"
    else:
        r = rates.get('USD', {}).get('avg', 0)
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
    name   = user.get('name', '')
    goal   = user.get('goal', '')
    income = user.get('income_amt', 0)
    cur    = user.get('income_currency', 'UZS')
    side   = user.get('side_income', 0)
    freq   = user.get('income_freq', '')

    if lang == 'uz':
        goal_uz = goal if goal else "yo'q"
        return (
            f"Siz Finora — {name} ning shaxsiy moliyaviy do'stisiz. "
            f"Birgalikda moliyaviy erkinlikka intilasiz.\n\n"
            f"MA'LUMOT:\n"
            f"• Daromad: {income} {cur} ({freq})" +
            (f"\n• Qo'shimcha: {side} {cur}" if side else '') +
            f"\n• Maqsad: {goal_uz}\n\n"
            f"SIZNING ROLINGIZ:\n"
            f"Siz faqat sovetchidan emas, balki samimiy do'stsiz. "
            f"O'zbek tilida gaplashing. Qisqa, amaliy, do'stona, emotsional. "
            f"Emoji ishlating 💬. Qo'llab-quvvatlang, tushunib bering, ilhomlantiring.\n\n"
            f"MASLAHATLAR:\n"
            f"O'zbekiston uchun real moliyaviy vositalar: "
            f"depozitlar (Kapitalbank, Hamkorbank), UZSE aksiyalari, oltin, ko'chmas mulk."
        )
    else:
        return (
            f"Ты — Finora, личный финансовый друг {name}. "
            f"Вы вместе идёте к финансовой свободе.\n\n"
            f"ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:\n"
            f"• Доход: {income} {cur} ({freq})" +
            (f"\n• Доп. доход: {side} {cur}" if side else '') +
            f"\n• Финансовая цель: {goal or 'не задана'}\n\n"
            f"ТВОЯ РОЛЬ:\n"
            f"Ты не просто советник, а настоящий друг. Говори по-русски. "
            f"Коротко, практично, по-дружески, с теплом и эмоциями. "
            f"Используй эмодзи 💬. Поддерживай, понимай, вдохновляй. "
            f"Помни контекст разговора — ты уже знаешь этого человека.\n\n"
            f"СОВЕТУЙ РЕАЛЬНЫЕ ИНСТРУМЕНТЫ ДЛЯ УЗБЕКИСТАНА:\n"
            f"Депозиты (Kapitalbank, Hamkorbank 18-22%), акции UZSE, золото, недвижимость."
        )

_NAME_EXTRACT_SYS = """Extract ONLY the person's name from the text.
Return ONLY valid JSON, no markdown:
{"name": "Влад"}

Examples:
"Меня зовут Влад" → {"name": "Влад"}
"Я Алишер" → {"name": "Алишер"}
"Влад" → {"name": "Влад"}
"Sardor Toshmatov" → {"name": "Sardor Toshmatov"}
"мое имя влад иванов" → {"name": "Влад Иванов"}

If unclear or no name found, return: {"name": null}"""

async def ai_extract_name(text: str) -> str | None:
    """Извлечь имя из произвольного текста через AI."""
    try:
        raw = await asyncio.to_thread(_chat, _NAME_EXTRACT_SYS, text, 100)
        raw = raw.replace('```json', '').replace('```', '').strip()
        data = json.loads(raw)
        return data.get('name')
    except Exception as e:
        logger.warning(f'Name extraction failed: {e}')
        # Fallback: первое слово с большой буквы
        words = text.strip().split()
        return words[0].capitalize() if words else None

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

async def ai_chat(uid: int, lang: str, text: str, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    """Дружеский разговор с AI с сохранением истории диалога."""
    user  = get_user(uid)
    stats = get_stats(uid)

    fin_ctx = (
        f"Финансы: доходы {uzs(stats['inc'])}, расходы {uzs(stats['exp'])}, "
        f"баланс {uzs(stats['inc'] - stats['exp'])}. "
        f"Этот месяц: доходы {uzs(stats['m_inc'])}, расходы {uzs(stats['m_exp'])}."
    )
    sys_prompt = build_advisor_system(user, lang) + f"\n\nТекущая финансовая ситуация: {fin_ctx}"

    # История диалога из context.user_data
    chat_history = []
    if context is not None:
        chat_history = context.user_data.get('chat_history', [])

    # Построить messages
    messages = [{'role': 'system', 'content': sys_prompt}]
    for h in chat_history[-10:]:   # последние 10 сообщений
        messages.append(h)
    messages.append({'role': 'user', 'content': text})

    try:
        response = client.chat.completions.create(
            model=OR_MODEL,
            max_tokens=600,
            messages=messages
        )
        reply = response.choices[0].message.content.strip().replace('**', '*')

        # Сохранить в историю
        if context is not None:
            chat_history.append({'role': 'user',      'content': text})
            chat_history.append({'role': 'assistant', 'content': reply})
            # Ограничить до 20 сообщений (10 пар)
            if len(chat_history) > 20:
                chat_history = chat_history[-20:]
            context.user_data['chat_history'] = chat_history

        return reply
    except Exception as e:
        logger.error(f'AI chat error: {e}')
        return '❌ Извини, что-то пошло не так. Попробуй ещё раз!' if lang == 'ru' else '❌ Kechirasiz, xatolik. Qayta urinib ko\'ring!'

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


import random as _random

MOTIVATIONS = {
    'ru': [
        "\n\n💪 *Молодец!* Каждая запись приближает к цели!",
        "\n\n🔥 Так держать! Твоя финансовая дисциплина впечатляет!",
        "\n\n⭐ Отлично! Ты контролируешь свои финансы — это редкость!",
        "\n\n💎 Круто! Продолжай в том же духе — результат не заставит ждать!",
        "\n\n🚀 +1 к финансовой осознанности! Ты движешься к своей цели!",
        "\n\n✨ Именно такие маленькие шаги складываются в большие перемены!",
    ],
    'uz': [
        "\n\n💪 *Ajoyib!* Har bir yozuv maqsadga yaqinlashtiradi!",
        "\n\n🔥 Davom eting! Moliyaviy intizomingiz ta'sirli!",
        "\n\n⭐ Zo'r! Moliyangizni nazorat qilish — bu kamdan-kam mahorat!",
        "\n\n💎 Ajoyib! Shunday davom eting — natija kutdirmaydi!",
        "\n\n🚀 Moliyaviy ongingiz oshmoqda! Maqsadingizga yaqinlashyapsiz!",
        "\n\n✨ Aynan shunday kichik qadamlar katta o'zgarishlarga olib keladi!",
    ]
}

def maybe_motivate(lang: str) -> str:
    """Вернуть случайную мотивацию с вероятностью 10%, или пустую строку."""
    if _random.random() < 0.10:
        return _random.choice(MOTIVATIONS.get(lang, MOTIVATIONS['ru']))
    return ''

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

    back_text = '← Назад' if lang == 'ru' else '← Orqaga'
    back_btn  = InlineKeyboardButton(back_text, callback_data='onb_back')

    if state == STATE_NAME:
        await context.bot.send_message(chat_id, tx(lang, 'ask_name'), parse_mode='Markdown')

    elif state == 'name_confirm':
        if lang == 'ru':
            confirm_msg = f"Тебя зовут *{name}*?"
            yes_btn  = '✅ Да, всё верно'
            edit_btn = '✏️ Изменить'
        else:
            confirm_msg = f"Ismingiz *{name}*mi?"
            yes_btn  = "✅ Ha, to'g'ri"
            edit_btn = "✏️ O'zgartirish"
        kb = [[
            InlineKeyboardButton(yes_btn,  callback_data='name_ok'),
            InlineKeyboardButton(edit_btn, callback_data='name_edit'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, confirm_msg,
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_INCOME_FREQ:
        kb = [[
            InlineKeyboardButton(tx(lang, 'freq_daily'),    callback_data='freq_daily'),
            InlineKeyboardButton(tx(lang, 'freq_weekly'),   callback_data='freq_weekly'),
        ], [
            InlineKeyboardButton(tx(lang, 'freq_monthly'),  callback_data='freq_monthly'),
            InlineKeyboardButton(tx(lang, 'freq_irregular'),callback_data='freq_irregular'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_income_freq', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_INCOME_AMT:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_income_amt'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_CURRENCY:
        kb = [[
            InlineKeyboardButton(tx(lang, 'cur_uzs'), callback_data='cur_UZS'),
            InlineKeyboardButton(tx(lang, 'cur_usd'), callback_data='cur_USD'),
            InlineKeyboardButton(tx(lang, 'cur_rub'), callback_data='cur_RUB'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_currency'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_SIDE_HUSTLE:
        kb = [[
            InlineKeyboardButton(tx(lang, 'yes'), callback_data='side_yes'),
            InlineKeyboardButton(tx(lang, 'no'),  callback_data='side_no'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_side_hustle'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_SIDE_AMT:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_side_amt'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_GOAL:
        kb = [
            [InlineKeyboardButton(tx(lang, 'goal_save'),     callback_data='goal_save'),
             InlineKeyboardButton(tx(lang, 'goal_buy'),      callback_data='goal_buy')],
            [InlineKeyboardButton(tx(lang, 'goal_invest'),   callback_data='goal_invest'),
             InlineKeyboardButton(tx(lang, 'goal_debt'),     callback_data='goal_debt')],
            [InlineKeyboardButton(tx(lang, 'goal_business'), callback_data='goal_business')],
            [InlineKeyboardButton(tx(lang, 'goal_none'),     callback_data='goal_none')],
            [back_btn]
        ]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_goal', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_GOAL_CUSTOM:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_goal_custom'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_NOTIFY_WHY:
        kb = [
            [InlineKeyboardButton(tx(lang, 'notify_18'), callback_data='notify_18:00'),
             InlineKeyboardButton(tx(lang, 'notify_19'), callback_data='notify_19:00')],
            [InlineKeyboardButton(tx(lang, 'notify_20'), callback_data='notify_20:00'),
             InlineKeyboardButton(tx(lang, 'notify_21'), callback_data='notify_21:00')],
            [InlineKeyboardButton(tx(lang, 'notify_22'), callback_data='notify_22:00'),
             InlineKeyboardButton(tx(lang, 'notify_23'), callback_data='notify_23:00')],
            [InlineKeyboardButton(tx(lang, 'notify_custom'), callback_data='notify_custom')],
            [back_btn]
        ]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_notify_why', name=name),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_NOTIFY_TIME:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_notify_time'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

async def handle_bug_report(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, is_voice: bool = False):
    """Обработать и сохранить баг-репорт, уведомить админа."""
    uid      = upd.effective_user.id
    user     = get_user(uid)
    lang     = user.get('language', 'ru')
    name     = user.get('name', 'Пользователь')
    username = upd.effective_user.username or 'no_username'

    # Сохранить в БД
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'INSERT INTO bug_reports(user_id, username, description) VALUES(%s,%s,%s) RETURNING id',
                (uid, username, text)
            )
            report_id = c.fetchone()[0]
        conn.commit()

    # Контекст пользователя
    stats = get_stats(uid)
    state = user.get('onboarding_state', 'unknown')

    # Отправить Admin
    admin_id  = int(os.getenv('ADMIN_USER_ID', '1326256223'))
    admin_msg = (
        f"🐛 *НОВЫЙ БАГ-РЕПОРТ #{report_id}*\n\n"
        f"👤 {name} (@{username})\n"
        f"🆔 User ID: `{uid}`\n"
        f"📅 {datetime.now(TZ).strftime('%d.%m.%Y %H:%M')}\n"
        f"🎤 Голосовое: {'Да' if is_voice else 'Нет'}\n\n"
        f"📝 *ОПИСАНИЕ:*\n{text}\n\n"
        f"📊 *КОНТЕКСТ:*\n"
        f"• Язык: {lang}\n"
        f"• State: {state}\n"
        f"• Транзакций: {stats['count']}\n"
        f"• Баланс: {uzs(stats['inc'] - stats['exp'])}"
    )
    kb_admin = [[
        InlineKeyboardButton('✅ Решено', callback_data=f'resolve_{report_id}'),
    ]]
    try:
        await ctx.bot.send_message(
            admin_id, admin_msg,
            reply_markup=InlineKeyboardMarkup(kb_admin),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Failed to send bug report to admin: {e}')

    # Сбросить state пользователя
    set_user(uid, onboarding_state=STATE_DONE)

    # Подтверждение пользователю
    if lang == 'ru':
        confirm = (
            f"✅ *Спасибо за репорт!*\n\n"
            f"Твоё сообщение отправлено разработчику.\n"
            f"Мы исправим это как можно быстрее! 🚀\n\n"
            f"ID репорта: *#{report_id}*\n\n"
            f"💬 Обычно отвечаем в течение 24 часов."
        )
    else:
        confirm = (
            f"✅ *Hisobot uchun rahmat!*\n\n"
            f"Xabaringiz dasturchiga yuborildi.\n"
            f"Buni tezda tuzatamiz! 🚀\n\n"
            f"Hisobot ID: *#{report_id}*\n\n"
            f"💬 Odatda 24 soat ichida javob beramiz."
        )
    await upd.message.reply_text(confirm, parse_mode='Markdown')

async def generate_debt_strategy(uid: int, lang: str, chat_id: int, context: ContextTypes.DEFAULT_TYPE):
    """Генерирует AI-стратегию погашения долгов и переходит к следующему шагу онбординга."""
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'SELECT bank, amount, rate, monthly_payment, deadline FROM debts WHERE user_id=%s ORDER BY rate DESC',
                (uid,)
            )
            debts = c.fetchall()

    if not debts:
        set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, context)
        return

    debt_list = '\n'.join(
        f"  {i+1}. {bank}: {uzs(amount)} (ставка {rate}%/год, платёж {uzs(monthly)}/мес, срок: {deadline})"
        for i, (bank, amount, rate, monthly, deadline) in enumerate(debts)
    )
    total_debt    = sum(d[1] for d in debts)
    total_monthly = sum(d[3] for d in debts)
    user   = get_user(uid)
    income = user.get('income_amt', 0)

    prompt = (
        f"Пользователь в кредитной кабале. Помоги составить план выхода.\n\n"
        f"КРЕДИТЫ ({len(debts)} шт):\n{debt_list}\n\n"
        f"ИТОГО долгов: {uzs(total_debt)}\n"
        f"Платежей в месяц: {uzs(total_monthly)}\n"
        f"Доход: {uzs(income)}\n\n"
        f"ЗАДАЧА:\n"
        f"1. Определи какой кредит закрывать ПЕРВЫМ (метод лавины — самый высокий %)\n"
        f"2. Дай конкретный план на 3-6 месяцев\n"
        f"3. Мотивируй — покажи что выход есть!\n"
        f"4. Если доход меньше платежей — дай совет\n"
        f"5. Коротко, по-дружески, с эмодзи, на {'русском' if lang == 'ru' else 'узбекском'}"
    )
    sys_prompt = (
        f"Ты Finora — финансовый советник. Помоги выбраться из долгов. "
        f"Говори {'по-русски' if lang == 'ru' else 'на узбекском'}. "
        f"Коротко, тепло, практично, с надеждой. Используй эмодзи."
    )

    try:
        strategy = await asyncio.to_thread(_chat, sys_prompt, prompt, 800)
        strategy = strategy.replace('**', '*')
        footer = (
            "_Стратегию можно просмотреть командой /debts_"
            if lang == 'ru' else
            "_Strategiyani /debts buyrug'i bilan ko'rish mumkin_"
        )
        await context.bot.send_message(
            chat_id,
            f"💎 *Твоя персональная стратегия погашения долгов:*\n\n{strategy}\n\n{footer}"
            if lang == 'ru' else
            f"💎 *Sizning kreditlarni to'lash strategiyangiz:*\n\n{strategy}\n\n{footer}",
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Debt strategy generation failed: {e}')
        msg = '✅ Кредиты записаны! Стратегию смотри в /debts' if lang == 'ru' else '✅ Kreditlar yozildi! Strategiyani /debts da ko\'ring'
        await context.bot.send_message(chat_id, msg)

    set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
    await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, context)

async def cmd_bug(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    user = get_user(uid)
    lang = user.get('language', 'ru')
    
    # Prompt user to describe the bug
    await upd.message.reply_text(
        '🐛 Опишите проблему подробно:' if lang == 'ru' else '🐛 Muammoni batafsil yozing:',
        parse_mode='Markdown'
    )
    
    # Set state for bug reporting
    set_user(uid, onboarding_state=STATE_BUG_REPORT)

async def setup_bot_ui(app: Application):
    """Настройка пользовательского интерфейса бота."""
    # Регистрация команд
    commands = [
        BotCommand('start', '🚀 Начать'),
        BotCommand('stats', '📊 Статистика'),
        BotCommand('history', '📋 История'),
        BotCommand('advice', '🤖 Персональный совет'),
        BotCommand('rate', '💱 Курс валют'),
        BotCommand('settings', '⚙️ Настройки'),
        BotCommand('bug', '🐛 Сообщить об ошибке'),
        BotCommand('help', '❓ Помощь'),
        BotCommand('debts', '💳 Управление кредитами'),
        BotCommand('clear', '🗑 Очистить данные')
    ]
    await app.bot.set_my_commands(commands)

def main():
    """Основная функция запуска бота."""
    # Инициализация базы данных
    init_db()

    # Настройка приложения
    async def post_init(app: Application):
        await setup_bot_ui(app)
        # Запустить планировщик уведомлений — каждые 60 секунд
        app.job_queue.run_repeating(send_daily_notifications, interval=60, first=10)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # Регистрация обработчиков
    app.add_handler(CommandHandler('start', cmd_start))
    app.add_handler(CommandHandler('stats', cmd_stats))
    app.add_handler(CommandHandler('history', cmd_history))
    app.add_handler(CommandHandler('advice', cmd_advice))
    app.add_handler(CommandHandler('rate', cmd_rate))
    app.add_handler(CommandHandler('settings', cmd_settings))
    app.add_handler(CommandHandler('bug', cmd_bug))
    app.add_handler(CommandHandler('help', cmd_help))
    app.add_handler(CommandHandler('debts', cmd_debts))
    app.add_handler(CommandHandler('clear', cmd_clear))

    # Обработчики сообщений и колбэков
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(CallbackQueryHandler(on_callback))

    # Запуск бота
    app.run_polling(drop_pending_updates=True)

# ─── DEBT MANAGEMENT CALLBACKS ───
async def on_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = upd.callback_query
    data = q.data
    uid = upd.effective_user.id
    chat_id = upd.effective_chat.id
    lang = get_lang(uid)

    # ─── DEBT MANAGEMENT ───
    if data == 'debt_add':
        ctx.user_data['debt_target']  = 1
        ctx.user_data['debt_current'] = 0
        ctx.user_data['debt_temp']    = {}
        ctx.user_data['debt_mode']    = 'add'
        set_user(uid, onboarding_state=STATE_DEBT_BANK)
        await q.answer('➕ Добавляем кредит...' if lang == 'ru' else "➕ Kredit qo'shilmoqda...")
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
        return

    if data == 'debt_pay':
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('SELECT id, bank, amount FROM debts WHERE user_id=%s ORDER BY rate DESC', (uid,))
                debts = c.fetchall()
        if not debts:
            await q.answer('❌ Нет кредитов!' if lang == 'ru' else "❌ Kreditlar yo'q!"); return
        kb = [[InlineKeyboardButton(f"{bank} ({uzs(amt)})", callback_data=f'pay_{id_}')]
              for id_, bank, amt in debts]
        kb.append([InlineKeyboardButton('← Отмена' if lang == 'ru' else '← Bekor qilish', callback_data='debts_cancel')])
        await q.edit_message_text(
            '✅ *Какой кредит платишь?*' if lang == 'ru' else "✅ *Qaysi kreditni to'layapsiz?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        return

    if data.startswith('pay_'):
        debt_id = int(data[4:])
        ctx.user_data['paying_debt_id'] = debt_id
        set_user(uid, onboarding_state='debt_payment')
        await q.edit_message_text(
            '💰 *Сколько заплатил?*\n\nНапиши сумму платежа:' if lang == 'ru'
            else "💰 *Qancha to'ladingiz?*\n\nTo'lov summasini yozing:",
            parse_mode='Markdown'
        )
        return

    if data == 'debt_close':
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('SELECT id, bank, amount FROM debts WHERE user_id=%s', (uid,))
                debts = c.fetchall()
        if not debts:
            await q.answer('❌ Нет кредитов!' if lang == 'ru' else "❌ Kreditlar yo'q!"); return
        kb = [[InlineKeyboardButton(f"🎉 {bank}", callback_data=f'close_{id_}')]
              for id_, bank, amt in debts]
        kb.append([InlineKeyboardButton('← Отмена' if lang == 'ru' else '← Bekor qilish', callback_data='debts_cancel')])
        await q.edit_message_text(
            '🎉 *Какой кредит закрыл полностью?*' if lang == 'ru' else "🎉 *Qaysi kreditni to'liq yopdingiz?*",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        return

    if data.startswith('close_'):
        debt_id = int(data[6:])
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('SELECT bank, amount FROM debts WHERE id=%s', (debt_id,))
                result = c.fetchone()
                if result:
                    bank, amount = result
                    c.execute('DELETE FROM debts WHERE id=%s', (debt_id,))
                else:
                    bank, amount = '?', 0
            conn.commit()
        await q.answer('🎉 Поздравляю!' if lang == 'ru' else '🎉 Tabriklayman!')
        await q.edit_message_text(
            f"🎉🎉🎉 *КРАСАВА!!!*\n\nТы закрыл кредит *{bank}* на {uzs(amount)}!\n\nЭто ОГРОМНОЕ достижение! 💪💎\n\n_Используй /debts чтобы посмотреть остальные._"
            if lang == 'ru' else
            f"🎉🎉🎉 *AJOYIB!!!*\n\n*{bank}* kreditini {uzs(amount)} ga yopdingiz!\n\nBu KATTA yutuq! 💪💎\n\n_/debts bilan qolganlarni ko'ring._",
            parse_mode='Markdown'
        )
        return

    if data == 'debts_cancel':
        try: await q.message.delete()
        except: pass
        return

    # ─── LANGUAGE SELECTION ───
    if data in ('lang_ru', 'lang_uz'):
        chosen = 'ru' if data == 'lang_ru' else 'uz'
        set_user(uid, language=chosen, onboarding_state=STATE_NAME)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_NAME, ctx)
        return

    # ─── NAME CONFIRM ───
    if data == 'name_ok':
        set_user(uid, onboarding_state=STATE_INCOME_FREQ)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_INCOME_FREQ, ctx)
        return

    if data == 'name_edit':
        set_user(uid, onboarding_state=STATE_NAME)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_NAME, ctx)
        return

    # ─── INCOME FREQUENCY ───
    if data.startswith('freq_'):
        freq_map = {
            'freq_daily':     ('Каждый день',  'Har kun'),
            'freq_weekly':    ('Раз в неделю', 'Haftada bir'),
            'freq_monthly':   ('Раз в месяц',  'Oyda bir'),
            'freq_irregular': ('Нерегулярно',  'Tartibsiz'),
        }
        freq_val = freq_map.get(data, ('', ''))[0 if lang == 'ru' else 1]
        set_user(uid, income_freq=freq_val, onboarding_state=STATE_INCOME_AMT)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_INCOME_AMT, ctx)
        return

    # ─── CURRENCY ───
    if data.startswith('cur_'):
        set_user(uid, income_currency=data[4:], onboarding_state=STATE_SIDE_HUSTLE)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_SIDE_HUSTLE, ctx)
        return

    # ─── SIDE HUSTLE ───
    if data == 'side_yes':
        set_user(uid, onboarding_state=STATE_SIDE_AMT)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_SIDE_AMT, ctx)
        return

    if data == 'side_no':
        set_user(uid, side_income=0, onboarding_state=STATE_GOAL)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_GOAL, ctx)
        return

    # ─── GOAL ───
    if data in ('goal_save', 'goal_buy', 'goal_invest', 'goal_debt', 'goal_business'):
        goal_map = {
            'goal_save':     ('Накопить деньги',           "Pul to'plash"),
            'goal_buy':      ('Купить что-то конкретное',  'Biror narsa sotib olish'),
            'goal_invest':   ('Начать инвестировать',      'Investitsiya boshlash'),
            'goal_debt':     ('Закрыть долги/кредиты',     "Qarz/kreditni to'lash"),
            'goal_business': ('Открыть/развить бизнес',    'Biznesni ochish/rivojlantirish'),
        }
        goal_val = goal_map[data][0 if lang == 'ru' else 1]
        set_user(uid, goal=goal_val)
        await q.answer()
        try: await q.message.delete()
        except: pass
        if data == 'goal_debt':
            set_user(uid, onboarding_state=STATE_DEBT_COUNT)
            await ctx.bot.send_message(
                chat_id,
                '💳 Сколько у тебя кредитов?\n\nНапиши число (например: *2*):' if lang == 'ru'
                else "💳 Nechta kreditingiz bor?\n\nRaqam yozing (masalan: *2*):",
                parse_mode='Markdown'
            )
        else:
            set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
            await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
        return

    if data == 'goal_none':
        u4 = get_user(uid)
        set_user(uid, goal='', onboarding_state=STATE_GOAL_CUSTOM)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id, tx(lang, 'no_goal_speech', name=u4.get('name', '')), parse_mode='Markdown'
        )
        return

    # ─── NOTIFY TIME BUTTONS ───
    if data.startswith('notify_') and ':' in data:
        time_str = data[len('notify_'):]
        u5 = get_user(uid)
        set_user(uid, notify_time=time_str, notify_enabled=1,
                 onboarding_state=STATE_DONE, onboarding_done=1)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(chat_id, tx(lang, 'notify_set', time=time_str), parse_mode='Markdown')
        await ctx.bot.send_message(chat_id, tx(lang, 'welcome_done', name=u5.get('name', '')), parse_mode='Markdown')
        return

    if data == 'notify_custom':
        set_user(uid, onboarding_state=STATE_NOTIFY_TIME)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_TIME, ctx)
        return

    # ─── BACK BUTTON ───
    if data == 'onb_back':
        current_st = get_state(uid)
        prev_st    = get_prev_state(uid, current_st, ctx.user_data)
        if prev_st:
            set_user(uid, onboarding_state=prev_st)
            await q.answer()
            try: await q.message.delete()
            except: pass
            await send_onboarding_step(chat_id, uid, prev_st, ctx)
        else:
            await q.answer('↩️ Нельзя вернуться' if lang == 'ru' else "↩️ Qaytib bo'lmaydi")
        return

    # ─── CLEAR DATA ───
    if data == 'confirm_clear':
        clear_data(uid)
        await q.answer()
        await q.edit_message_text(tx(lang, 'cleared'), parse_mode='Markdown')
        return

    if data == 'cancel_clear':
        await q.answer()
        await q.edit_message_text(tx(lang, 'cancelled'), parse_mode='Markdown')
        return

    # ─── RESOLVE BUG (admin) ───
    if data.startswith('resolve_'):
        report_id = int(data[8:])
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    "UPDATE bug_reports SET status='resolved', resolved_at=NOW() WHERE id=%s",
                    (report_id,)
                )
            conn.commit()
        await q.answer('✅ Отмечено как решённое')
        try:
            await q.edit_message_text(
                (q.message.text or '') + f'\n\n✅ *Решено!* ({datetime.now(TZ).strftime("%d.%m %H:%M")})',
                parse_mode='Markdown'
            )
        except Exception:
            pass
        return

    # ─── SETTINGS ───
    if data == 'set_notify':
        set_user(uid, onboarding_state='set_notify_time')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id,
            '⌚ Напиши новое время уведомления (*ЧЧ:ММ*):' if lang == 'ru'
            else '⌚ Yangi eslatma vaqtini yozing (*SS:DD*):',
            parse_mode='Markdown'
        )
        return

    if data == 'set_goal':
        set_user(uid, onboarding_state='set_goal')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id,
            '🎯 Напиши новую финансовую цель:' if lang == 'ru' else '🎯 Yangi moliyaviy maqsadingizni yozing:',
            parse_mode='Markdown'
        )
        return

    if data == 'set_name':
        set_user(uid, onboarding_state='set_name')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id,
            '👤 Напиши своё имя:' if lang == 'ru' else '👤 Ismingizni yozing:',
            parse_mode='Markdown'
        )
        return

    if data == 'cancel_notify':
        set_user(uid, notify_enabled=0)
        await q.answer()
        await q.edit_message_text(tx(lang, 'notify_disabled'), parse_mode='Markdown')
        return

    await q.answer()

# ─── DEBT PAYMENT HANDLING ───
async def on_text(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = upd.message.text.strip()
    uid = upd.effective_user.id
    chat_id = upd.effective_chat.id
    lang = get_lang(uid)
    state = get_state(uid)

    # ─── DEBT MANAGEMENT ───
    if state == STATE_DEBT_COUNT:
        try:
            count = int(text.strip())
            if count <= 0: raise ValueError
            set_user(uid, onboarding_state=STATE_DEBT_BANK)
            ctx.user_data['debt_target']  = count
            ctx.user_data['debt_current'] = 0
            ctx.user_data['debt_temp']    = {}
            await upd.message.reply_text('✅ Отлично! Начинаю собирать данные...' if lang == 'ru' else "✅ Zo'r! Ma'lumotlarni to'playapman...")
            await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число, например: *3*' if lang == 'ru' else "❌ Raqam yozing, masalan: *3*", parse_mode='Markdown')
        return

    elif state == STATE_DEBT_BANK:
        ctx.user_data.setdefault('debt_temp', {})['bank'] = text
        set_user(uid, onboarding_state=STATE_DEBT_AMT)
        await send_onboarding_step(chat_id, uid, STATE_DEBT_AMT, ctx)
        return

    elif state == STATE_DEBT_AMT:
        try:
            amt = float(text.replace(' ', '').replace(',', '.'))
            ctx.user_data['debt_temp']['amount'] = amt
            set_user(uid, onboarding_state=STATE_DEBT_RATE)
            await send_onboarding_step(chat_id, uid, STATE_DEBT_RATE, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    elif state == STATE_DEBT_RATE:
        try:
            rate = float(text.replace('%', '').replace(' ', '').replace(',', '.'))
            ctx.user_data['debt_temp']['rate'] = rate
            set_user(uid, onboarding_state=STATE_DEBT_MONTHLY)
            await send_onboarding_step(chat_id, uid, STATE_DEBT_MONTHLY, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число, например: *24*' if lang == 'ru' else "❌ Raqam yozing, masalan: *24*", parse_mode='Markdown')
        return

    elif state == STATE_DEBT_MONTHLY:
        try:
            monthly = float(text.replace(' ', '').replace(',', '.'))
            ctx.user_data['debt_temp']['monthly_payment'] = monthly
            set_user(uid, onboarding_state=STATE_DEBT_DEADLINE)
            await send_onboarding_step(chat_id, uid, STATE_DEBT_DEADLINE, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    elif state == STATE_DEBT_DEADLINE:
        ctx.user_data['debt_temp']['deadline'] = text
        # Сохранить кредит в БД
        d = ctx.user_data.get('debt_temp', {})
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    'INSERT INTO debts(user_id, bank, amount, rate, monthly_payment, deadline) VALUES(%s,%s,%s,%s,%s,%s)',
                    (uid, d.get('bank', '?'), d.get('amount', 0), d.get('rate', 0),
                     d.get('monthly_payment', 0), d.get('deadline', '?'))
                )
            conn.commit()
        current = ctx.user_data.get('debt_current', 0) + 1
        target  = ctx.user_data.get('debt_target', 1)
        ctx.user_data['debt_current'] = current
        ctx.user_data['debt_temp']    = {}
        if current < target:
            set_user(uid, onboarding_state=STATE_DEBT_BANK)
            await upd.message.reply_text(
                f'✅ Записал!\n\n▶️ Следующий кредит ({current + 1} из {target})...'
                if lang == 'ru' else
                f"✅ Yozib oldim!\n\n▶️ Keyingi kredit ({current + 1} / {target})...",
                parse_mode='Markdown'
            )
            await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
        else:
            await upd.message.reply_text(
                '✅ Все кредиты записаны!\n\n⏳ Анализирую и создаю стратегию...'
                if lang == 'ru' else
                "✅ Barcha kreditlar yozildi!\n\n⏳ Tahlil qilyapman va strategiya tuzmoqdaman...",
                parse_mode='Markdown'
            )
            await generate_debt_strategy(uid, lang, chat_id, ctx)
        return

    elif state == 'debt_payment':
        try:
            payment = float(text.replace(' ', '').replace(',', '.'))
            debt_id = ctx.user_data.get('paying_debt_id')
            with get_conn() as conn:
                with conn.cursor() as c:
                    c.execute('SELECT amount, bank FROM debts WHERE id=%s', (debt_id,))
                    row = c.fetchone()
                    if row:
                        old_amt, bank = row
                        new_amt = old_amt - payment
                        if new_amt <= 0:
                            c.execute('DELETE FROM debts WHERE id=%s', (debt_id,))
                            reply = (f"🎉🎉🎉 *ПОЗДРАВЛЯЮ!!!*\n\nТы полностью закрыл кредит *{bank}*!\n\nЭто огромный шаг к финансовой свободе! 💪\n\n_/debts для оставшихся кредитов_"
                                     if lang == 'ru' else
                                     f"🎉🎉🎉 *TABRIKLAYMAN!!!*\n\n*{bank}* kreditini to'liq yopdingiz!\n\nBu moliyaviy erkinlikka katta qadam! 💪\n\n_/debts — qolgan kreditlar_")
                        else:
                            c.execute('UPDATE debts SET amount=%s WHERE id=%s', (new_amt, debt_id))
                            reply = (f"✅ *Платёж записан!*\n\n{bank}:\nБыло: `{uzs(old_amt)}`\nСтало: `{uzs(new_amt)}`\n\n💪 Так держать!"
                                     if lang == 'ru' else
                                     f"✅ *To'lov yozildi!*\n\n{bank}:\nAvval: `{uzs(old_amt)}`\nEndi: `{uzs(new_amt)}`\n\n💪 Davom eting!")
                    else:
                        reply = '❌ Кредит не найден' if lang == 'ru' else "❌ Kredit topilmadi"
                conn.commit()
            set_user(uid, onboarding_state=STATE_DONE)
            ctx.user_data.pop('paying_debt_id', None)
            await upd.message.reply_text(reply, parse_mode='Markdown')
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing')
        return

    # ─── ONBOARDING TEXT INPUT ───
    elif state == STATE_NAME:
        name_val = await ai_extract_name(text)
        if not name_val:
            name_val = text.strip().split()[0].capitalize() if text.strip() else 'Друг'
        set_user(uid, name=name_val, onboarding_state=STATE_NAME_CONFIRM)
        await send_onboarding_step(chat_id, uid, STATE_NAME_CONFIRM, ctx)
        return

    elif state == STATE_INCOME_AMT:
        try:
            cleaned = text.replace(' ', '').replace(',', '.').replace("so'm", '').replace('сум', '').replace('$', '')
            set_user(uid, income_amt=float(cleaned), onboarding_state=STATE_CURRENCY)
            await send_onboarding_step(chat_id, uid, STATE_CURRENCY, ctx)
        except Exception:
            await upd.message.reply_text(
                '❌ Напиши число, например: *500000*' if lang == 'ru' else '❌ Raqam yozing, masalan: *500000*',
                parse_mode='Markdown'
            )
        return

    elif state == STATE_SIDE_AMT:
        try:
            set_user(uid, side_income=float(text.replace(' ', '').replace(',', '.')), onboarding_state=STATE_GOAL)
            await send_onboarding_step(chat_id, uid, STATE_GOAL, ctx)
        except Exception:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    elif state == STATE_GOAL_CUSTOM:
        set_user(uid, goal=text.strip(), onboarding_state=STATE_NOTIFY_WHY)
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
        return

    elif state == STATE_NOTIFY_TIME:
        import re as _re
        m = _re.match(r'^(\d{1,2}):(\d{2})$', text.strip())
        if m:
            h_, mn_ = int(m.group(1)), int(m.group(2))
            if 0 <= h_ <= 23 and 0 <= mn_ <= 59:
                time_str = f'{h_:02d}:{mn_:02d}'
                u2 = get_user(uid)
                set_user(uid, notify_time=time_str, notify_enabled=1,
                         onboarding_state=STATE_DONE, onboarding_done=1)
                await upd.message.reply_text(tx(lang, 'notify_set', time=time_str), parse_mode='Markdown')
                await upd.message.reply_text(tx(lang, 'welcome_done', name=u2.get('name', '')), parse_mode='Markdown')
                return
        await upd.message.reply_text('❌ Формат: *20:30*' if lang == 'ru' else '❌ Format: *20:30*', parse_mode='Markdown')
        return

    elif state == STATE_BUG_REPORT:
        await handle_bug_report(upd, ctx, text)
        return

    # ─── SETTINGS EDIT STATES ───
    elif state == 'set_name':
        name_new = await ai_extract_name(text)
        if not name_new:
            name_new = text.strip().split()[0].capitalize() if text.strip() else text.strip()
        set_user(uid, name=name_new, onboarding_state=STATE_DONE)
        await upd.message.reply_text(
            f"✅ Имя изменено на *{name_new}*" if lang == 'ru' else f"✅ Ism *{name_new}* ga o'zgartirildi",
            parse_mode='Markdown'
        )
        return

    elif state == 'set_goal':
        set_user(uid, goal=text.strip(), onboarding_state=STATE_DONE)
        await upd.message.reply_text(
            f"✅ Цель обновлена: _{text.strip()}_" if lang == 'ru' else f"✅ Maqsad yangilandi: _{text.strip()}_",
            parse_mode='Markdown'
        )
        return

    elif state == 'set_notify_time':
        import re as _re2
        m2 = _re2.match(r'^(\d{1,2}):(\d{2})$', text.strip())
        if m2:
            h2, mn2 = int(m2.group(1)), int(m2.group(2))
            if 0 <= h2 <= 23 and 0 <= mn2 <= 59:
                ts2 = f'{h2:02d}:{mn2:02d}'
                set_user(uid, notify_time=ts2, notify_enabled=1, onboarding_state=STATE_DONE)
                await upd.message.reply_text(tx(lang, 'notify_set', time=ts2), parse_mode='Markdown')
                return
        await upd.message.reply_text('❌ Формат: *20:30*' if lang == 'ru' else '❌ Format: *20:30*', parse_mode='Markdown')
        return

    # ─── MAIN TRANSACTION (STATE_DONE) ───
    else:
        u3 = get_user(uid)
        if not u3.get('onboarding_done'):
            await upd.message.reply_text(
                '❓ Напиши /start чтобы начать' if lang == 'ru' else '❓ /start yozing'
            )
            return

        wait_msg = await upd.message.reply_text(tx(lang, 'processing'), parse_mode='Markdown')
        parsed = await ai_parse(text)
        try:
            await wait_msg.delete()
        except Exception:
            pass

        if not parsed:
            await upd.message.reply_text(tx(lang, 'parse_error'), parse_mode='Markdown')
            return

        action = parsed.get('action')

        if action == 'cancel':
            if delete_last_tx(uid):
                await upd.message.reply_text(tx(lang, 'cancelled'), parse_mode='Markdown')
            else:
                await upd.message.reply_text(tx(lang, 'no_data'), parse_mode='Markdown')
            return

        if action == 'fix':
            last = get_last_tx(uid)
            if not last:
                await upd.message.reply_text(tx(lang, 'no_data'), parse_mode='Markdown')
                return
            update_tx(last[0], amount=parsed.get('amount'), description=parsed.get('description'))
            await upd.message.reply_text(tx(lang, 'fixed'), parse_mode='Markdown')
            return

        if 'type' not in parsed or 'amount' not in parsed:
            await upd.message.reply_text(tx(lang, 'parse_error'), parse_mode='Markdown')
            return

        rates     = await asyncio.to_thread(get_rates)
        cur       = parsed.get('currency', 'UZS')
        items_lst = parsed.get('items', [])
        items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
        add_tx(uid, parsed['type'], parsed['amount'],
               parsed.get('description', ''), parsed.get('category', '❓ Другое'),
               cur, items_str)

        reply = fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang)
        await upd.message.reply_text(reply, parse_mode='Markdown')


# ────────────────────────── COMMAND HANDLERS ──────────────────────
async def cmd_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    u   = get_user(uid)
    lang = u.get('language', 'ru')

    if u.get('onboarding_done'):
        name = u.get('name', '')
        greet = (f"👋 Привет, *{name}*! Я здесь 💎\n\nНапиши что потратил или заработал"
                 if lang == 'ru' else
                 f"👋 Salom, *{name}*! Men bu yerdaman 💎\n\nNima sarflaganingizni yozing")
        await upd.message.reply_text(greet, parse_mode='Markdown')
        return

    set_user(uid, onboarding_state=STATE_LANG)
    kb = [[
        InlineKeyboardButton('🇷🇺 Русский', callback_data='lang_ru'),
        InlineKeyboardButton("🇺🇿 O'zbek",  callback_data='lang_uz'),
    ]]
    await upd.message.reply_text(
        '👋 Привет! Я *Finora* — твой личный финансовый друг 💎\n\nВыбери язык / Tilni tanlang:',
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


async def cmd_stats(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    u    = get_user(uid)
    name = u.get('name', '')
    s    = get_stats(uid)

    if s['count'] == 0:
        await upd.message.reply_text(tx(lang, 'no_data'), parse_mode='Markdown')
        return

    bal   = s['inc'] - s['exp']
    m_bal = s['m_inc'] - s['m_exp']
    bal_s = f"+{uzs(bal)}" if bal >= 0 else f"-{uzs(abs(bal))}"
    m_s   = f"+{uzs(m_bal)}" if m_bal >= 0 else f"-{uzs(abs(m_bal))}"

    msg = (f"{tx(lang, 'stats_hdr', name=name)}\n\n"
           f"*{tx(lang, 'all_time')}:*\n"
           f"{tx(lang, 'income_lbl')}: `{uzs(s['inc'])}`\n"
           f"{tx(lang, 'expense_lbl')}: `{uzs(s['exp'])}`\n"
           f"{tx(lang, 'balance_lbl')}: `{bal_s}`\n"
           f"{tx(lang, 'records_lbl')}: {s['count']}\n\n"
           f"*{tx(lang, 'this_month')}:*\n"
           f"{tx(lang, 'income_lbl')}: `{uzs(s['m_inc'])}`\n"
           f"{tx(lang, 'expense_lbl')}: `{uzs(s['m_exp'])}`\n"
           f"{tx(lang, 'balance_lbl')}: `{m_s}`")

    if s['cats']:
        msg += f"\n\n📊 *{'Топ расходов' if lang == 'ru' else 'Top xarajatlar'}:*\n"
        for i, (cat, amt) in enumerate(s['cats'], 1):
            msg += f"  {i}. {cat}: `{uzs(amt)}`\n"

    await upd.message.reply_text(msg, parse_mode='Markdown')


async def cmd_history(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    rows = get_history(uid, limit=15)

    if not rows:
        await upd.message.reply_text(tx(lang, 'no_data'), parse_mode='Markdown')
        return

    lines = [tx(lang, 'hist_hdr')]
    for _, type_, amount, desc, cat, cur, created_at in rows:
        sign  = '➕' if type_ == 'inc' else '➖'
        date  = created_at.astimezone(TZ).strftime('%d.%m %H:%M') if hasattr(created_at, 'astimezone') else str(created_at)[:16]
        cur_s = f' {cur}' if cur != 'UZS' else ''
        lines.append(f"{sign} `{amount:,.0f}{cur_s}` — {desc} _{date}_")

    await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_advice(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    s    = get_stats(uid)

    if s['count'] == 0:
        await upd.message.reply_text(tx(lang, 'no_data'), parse_mode='Markdown')
        return

    wait = await upd.message.reply_text(tx(lang, 'advice_wait'), parse_mode='Markdown')
    advice = await ai_advice(uid, lang)
    try: await wait.delete()
    except: pass
    await upd.message.reply_text(
        f"{tx(lang, 'advice_hdr')}\n\n{advice}", parse_mode='Markdown'
    )


async def cmd_rate(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)

    wait = await upd.message.reply_text('⏳ ...', parse_mode='Markdown')
    rates = await asyncio.to_thread(get_rates)
    try: await wait.delete()
    except: pass

    if not rates:
        await upd.message.reply_text(tx(lang, 'rate_err'), parse_mode='Markdown')
        return

    now_str = datetime.now(TZ).strftime('%d.%m.%Y %H:%M')
    lines   = [tx(lang, 'rate_hdr'), '']
    for ccy in ('USD', 'EUR', 'RUB'):
        r = rates.get(ccy)
        if not r:
            continue
        src = '🏦' if r.get('source') == 'cbu' else '💹'
        if r['buy'] != r['sell']:
            lines.append(f"{src} *{ccy}*: {'покупка' if lang == 'ru' else 'sotib olish'} `{r['buy']:,.0f}` / {'продажа' if lang == 'ru' else 'sotish'} `{r['sell']:,.0f}`")
        else:
            lines.append(f"{src} *{ccy}*: `{r['avg']:,.0f}` so'm")
    lines.append(f"\n_{tx(lang, 'updated')}: {now_str}_")

    await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_settings(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    kb = [
        [InlineKeyboardButton(tx(lang, 'set_notify'),     callback_data='set_notify')],
        [InlineKeyboardButton(tx(lang, 'set_goal'),       callback_data='set_goal')],
        [InlineKeyboardButton(tx(lang, 'set_name'),       callback_data='set_name')],
        [InlineKeyboardButton(tx(lang, 'cancel_notify'),  callback_data='cancel_notify')],
    ]
    await upd.message.reply_text(
        tx(lang, 'settings_hdr'),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


async def cmd_help(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    await upd.message.reply_text(tx(lang, 'help_text'), parse_mode='Markdown')


async def cmd_debts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)

    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'SELECT id, bank, amount, rate, monthly_payment, deadline FROM debts WHERE user_id=%s ORDER BY rate DESC',
                (uid,)
            )
            debts = c.fetchall()

    if not debts:
        body = '💳 *Кредиты*\n\nАктивных кредитов нет 🎉' if lang == 'ru' else "💳 *Kreditlar*\n\nFaol kreditlar yo'q 🎉"
    else:
        lines = ['💳 *Кредиты:*\n' if lang == 'ru' else '💳 *Kreditlar:*\n']
        total = 0
        for _, bank, amount, rate, monthly, deadline in debts:
            total += amount
            lines.append(
                f"🏦 *{bank}*\n"
                f"  {'Долг' if lang == 'ru' else 'Qarz'}: `{uzs(amount)}`\n"
                f"  {'Ставка' if lang == 'ru' else 'Foiz'}: {rate}%\n"
                f"  {'Платёж/мес' if lang == 'ru' else 'Oylik'}: `{uzs(monthly)}`\n"
                f"  {'Срок' if lang == 'ru' else 'Muddat'}: {deadline}\n"
            )
        lines.append(f"\n💰 *{'Итого' if lang == 'ru' else 'Jami'}: `{uzs(total)}`*")
        body = '\n'.join(lines)

    kb = [
        [InlineKeyboardButton('➕ ' + ('Добавить кредит' if lang == 'ru' else "Kredit qo'shish"),  callback_data='debt_add')],
        [InlineKeyboardButton('💸 ' + ('Внести платёж'  if lang == 'ru' else "To'lov qilish"),    callback_data='debt_pay')],
        [InlineKeyboardButton('🎉 ' + ('Закрыть кредит' if lang == 'ru' else "Kreditni yopish"),  callback_data='debt_close')],
    ]
    await upd.message.reply_text(body, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


async def cmd_clear(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    kb = [[
        InlineKeyboardButton(tx(lang, 'yes_del'),   callback_data='confirm_clear'),
        InlineKeyboardButton(tx(lang, 'no_cancel'), callback_data='cancel_clear'),
    ]]
    await upd.message.reply_text(
        tx(lang, 'confirm_clear'),
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )


# ────────────────────────── PHOTO HANDLER ─────────────────────────
async def on_photo(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)

    wait = await upd.message.reply_text(tx(lang, 'processing'), parse_mode='Markdown')
    try:
        photo     = upd.message.photo[-1]
        file_obj  = await ctx.bot.get_file(photo.file_id)
        img_bytes = await file_obj.download_as_bytearray()
        parsed    = await ai_parse_photo(bytes(img_bytes), 'image/jpeg')
    except Exception as e:
        logger.error(f'Photo handler error: {e}')
        parsed = None
    try: await wait.delete()
    except: pass

    if not parsed or 'type' not in parsed or 'amount' not in parsed:
        await upd.message.reply_text(tx(lang, 'photo_error'), parse_mode='Markdown')
        return

    rates     = await asyncio.to_thread(get_rates)
    cur       = parsed.get('currency', 'UZS')
    items_lst = parsed.get('items', [])
    items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description', ''), parsed.get('category', '🛒 Магазин'),
           cur, items_str)

    reply = fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang)
    await upd.message.reply_text(reply, parse_mode='Markdown')


# ────────────────────────── VOICE HANDLER ─────────────────────────
async def on_voice(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid   = upd.effective_user.id
    lang  = get_lang(uid)
    state = get_state(uid)

    wait = await upd.message.reply_text(tx(lang, 'processing'), parse_mode='Markdown')
    try:
        voice    = upd.message.voice
        file_obj = await ctx.bot.get_file(voice.file_id)
        with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as tmp:
            tmp_path = tmp.name
        await file_obj.download_to_drive(tmp_path)
        text_result = await transcribe(tmp_path, lang)
        Path(tmp_path).unlink(missing_ok=True)
    except Exception as e:
        logger.error(f'Voice handler error: {e}')
        text_result = None
    try: await wait.delete()
    except: pass

    if not text_result:
        await upd.message.reply_text(tx(lang, 'voice_error'), parse_mode='Markdown')
        return

    # Route to bug report if in that state
    if state == STATE_BUG_REPORT:
        await handle_bug_report(upd, ctx, text_result, is_voice=True)
        return

    wait2  = await upd.message.reply_text(f'🎤 _{text_result}_\n\n{tx(lang, "processing")}', parse_mode='Markdown')
    parsed = await ai_parse(text_result)
    try: await wait2.delete()
    except: pass

    if not parsed or 'type' not in parsed or 'amount' not in parsed:
        await upd.message.reply_text(tx(lang, 'parse_error'), parse_mode='Markdown')
        return

    rates     = await asyncio.to_thread(get_rates)
    cur       = parsed.get('currency', 'UZS')
    items_lst = parsed.get('items', [])
    items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description', ''), parsed.get('category', '❓ Другое'),
           cur, items_str)

    reply = fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang)
    await upd.message.reply_text(reply, parse_mode='Markdown')


# ────────────────────────── NOTIFICATION SCHEDULER ────────────────
async def send_daily_notifications(context: ContextTypes.DEFAULT_TYPE):
    """Рассылка ежедневных напоминаний — запускается каждую минуту."""
    now_str = datetime.now(TZ).strftime('%H:%M')
    try:
        users = get_all_users_with_notify()
    except Exception as e:
        logger.error(f'Notification fetch error: {e}')
        return

    for uid, name, lang, notify_time in users:
        if notify_time != now_str:
            continue
        try:
            await context.bot.send_message(
                uid,
                tx(lang, 'remind_msg', name=name or ('друг' if lang == 'ru' else 'do\'st')),
                parse_mode='Markdown'
            )
            logger.info(f'Sent reminder to {uid}')
        except Exception as e:
            logger.warning(f'Failed to send reminder to {uid}: {e}')


if __name__ == '__main__':
    main()
