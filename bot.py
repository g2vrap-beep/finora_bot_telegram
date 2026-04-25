#!/usr/bin/env python3
"""
💎 Finora — Твой личный финансовый друг
Telegram bot: учёт финансов + AI советы + умный онбординг + уведомления + Flask dashboard
"""

import os, json, logging, tempfile, base64, asyncio, threading, hashlib, hmac
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl

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
OR_MODEL       = os.getenv('OR_MODEL', 'anthropic/claude-sonnet-4-6')
TZ             = ZoneInfo('Asia/Tashkent')
ADMIN_ID       = int(os.getenv('ADMIN_USER_ID', '1326256223'))

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
STATE_GENDER     = 'gender'

# ─── DEBT STATES ───
STATE_DEBT_COUNT    = 'debt_count'
STATE_DEBT_BANK     = 'debt_bank'
STATE_DEBT_AMT      = 'debt_amt'
STATE_DEBT_RATE     = 'debt_rate'
STATE_DEBT_MONTHLY  = 'debt_monthly'
STATE_DEBT_DEADLINE = 'debt_deadline'

# States that accept text input (voice should be routed here too)
ONBOARDING_TEXT_STATES = {
    STATE_NAME, STATE_INCOME_AMT, STATE_SIDE_AMT, STATE_GOAL_CUSTOM,
    STATE_NOTIFY_TIME, STATE_DEBT_COUNT, STATE_DEBT_BANK, STATE_DEBT_AMT,
    STATE_DEBT_RATE, STATE_DEBT_MONTHLY, STATE_DEBT_DEADLINE,
    'set_name', 'set_goal', 'set_notify_time', 'debt_payment', 'set_income',
    'voice_fix_pending'
}

# Карта навигации: текущий state → предыдущий state
ONBOARDING_BACK_MAP = {
    'name_confirm':    STATE_NAME,
    STATE_GENDER:      'name_confirm',
    STATE_INCOME_FREQ: STATE_GENDER,
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
                              '/debts — 💳 Кредиты\n'
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
                              '/debts — 💳 Кредиты\n'
                              '/clear — 🗑 Очистить данные'),
        'settings_hdr'     : '⚙️ *Настройки*\n\nЧто хочешь изменить?',
        'set_notify'       : '🔔 Время уведомлений',
        'set_goal'         : '🎯 Финансовая цель',
        'set_name'         : '👤 Своё имя',
        'set_income'       : '💰 Изменить доход',
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
                              '/debts — 💳 Kreditlar\n'
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
                              '/debts — 💳 Kreditlar\n'
                              '/clear — 🗑 Ma\'lumotlarni o\'chirish'),
        'settings_hdr'     : '⚙️ *Sozlamalar*\n\nNimani o\'zgartiroqsiz?',
        'set_notify'       : '🔔 Eslatma vaqti',
        'set_goal'         : '🎯 Moliyaviy maqsad',
        'set_name'         : '👤 Ismingiz',
        'set_income'       : '💰 Daromadni o\'zgartirish',
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
    if url.startswith('postgres://'):
        return 'postgresql://' + url[len('postgres://'):]
    return url

def get_conn():
    if DATABASE_PUBLIC_URL:
        return psycopg2.connect(_normalize_pg_url(DATABASE_PUBLIC_URL))
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
                onboarding_done INTEGER DEFAULT 0,
                debt_target INTEGER DEFAULT 0,
                debt_current INTEGER DEFAULT 0,
                debt_temp_json TEXT DEFAULT '{}'
            )''')
            # Add columns for existing tables (safe migration)
            for col, definition in [
                ('debt_target',   'INTEGER DEFAULT 0'),
                ('debt_current',  'INTEGER DEFAULT 0'),
                ('debt_temp_json','TEXT DEFAULT \'{}\''),
            ]:
                try:
                    c.execute(f'ALTER TABLE users ADD COLUMN IF NOT EXISTS {col} {definition}')
                except Exception:
                    pass
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
            c.execute('''CREATE TABLE IF NOT EXISTS category_budgets(
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                category TEXT NOT NULL,
                monthly_limit FLOAT NOT NULL,
                period TEXT DEFAULT 'monthly',
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, category)
            )''')
            # Таблица для истории чата
            c.execute('''CREATE TABLE IF NOT EXISTS chat_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )''')
            c.execute('CREATE INDEX IF NOT EXISTS idx_chat_history_user ON chat_history(user_id, created_at DESC)')
            # Счётчик транзакций для инсайтов
            c.execute('ALTER TABLE users ADD COLUMN IF NOT EXISTS tx_count_since_insight INT DEFAULT 0')
            # Колонка для настроения
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_mood TEXT DEFAULT ''")
            # Колонка для пола
            c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS gender TEXT DEFAULT 'unknown'")
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

# ─── DEBT STATE HELPERS (DB-based, survives restarts) ───
def get_debt_state(uid: int) -> dict:
    u = get_user(uid)
    temp_raw = u.get('debt_temp_json', '{}') or '{}'
    try:
        temp = json.loads(temp_raw)
    except Exception:
        temp = {}
    return {
        'target':  u.get('debt_target', 0) or 0,
        'current': u.get('debt_current', 0) or 0,
        'temp':    temp,
    }

def set_debt_state(uid: int, target=None, current=None, temp=None):
    kwargs = {}
    if target  is not None: kwargs['debt_target']   = target
    if current is not None: kwargs['debt_current']  = current
    if temp    is not None: kwargs['debt_temp_json'] = json.dumps(temp, ensure_ascii=False)
    if kwargs:
        set_user(uid, **kwargs)

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
    # Изменение 5: Увеличивать счётчик транзакций
    try:
        with get_conn() as conn2:
            with conn2.cursor() as c2:
                c2.execute(
                    'UPDATE users SET tx_count_since_insight = COALESCE(tx_count_since_insight, 0) + 1 WHERE user_id=%s',
                    (uid,)
                )
            conn2.commit()
    except Exception:
        pass
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
    db_clear_chat_history(uid)
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('DELETE FROM transactions WHERE user_id=%s', (uid,))
        conn.commit()

def full_reset_user(uid: int):
    """Полный сброс пользователя: онбординг + транзакции + долги."""
    db_clear_chat_history(uid)
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('DELETE FROM transactions WHERE user_id=%s', (uid,))
            c.execute('DELETE FROM debts WHERE user_id=%s', (uid,))
            c.execute('''UPDATE users SET
                onboarding_state='lang', onboarding_done=0,
                name='', income_freq='', income_amt=0, income_currency='UZS',
                side_income=0, goal='', notify_time='21:00', notify_enabled=1,
                debt_target=0, debt_current=0, debt_temp_json='{}',
                tx_count_since_insight=0
                WHERE user_id=%s''', (uid,))
        conn.commit()

def reset_onboarding_only(uid: int):
    """Сброс только онбординга — транзакции сохраняются."""
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('''UPDATE users SET
                onboarding_state='lang', onboarding_done=0,
                name='', income_freq='', income_amt=0, income_currency='UZS',
                side_income=0, goal='', notify_time='21:00', notify_enabled=1,
                debt_target=0, debt_current=0, debt_temp_json='{}'
                WHERE user_id=%s''', (uid,))
        conn.commit()

def get_all_users_with_notify():
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                "SELECT user_id, name, language, notify_time FROM users WHERE notify_enabled=1 AND onboarding_done=1 AND notify_time != ''"
            )
            return c.fetchall()

def get_all_users_list(limit=50):
    """Получить список пользователей для admin reset."""
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                'SELECT user_id, name, language, onboarding_done FROM users ORDER BY user_id DESC LIMIT %s',
                (limit,)
            )
            return [dict(r) for r in c.fetchall()]

# ─────────────────── BUDGET PER CATEGORY ───────────────────
def set_category_budget(uid: int, category: str, monthly_limit: float) -> bool:
    """Установить месячный лимит на категорию."""
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('''
                INSERT INTO category_budgets(user_id, category, monthly_limit)
                VALUES (%s, %s, %s)
                ON CONFLICT (user_id, category) DO UPDATE SET monthly_limit = %s
            ''', (uid, category, monthly_limit, monthly_limit))
        conn.commit()
    return True

def get_category_budget(uid: int, category: str) -> float | None:
    """Получить лимит на категорию."""
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    'SELECT monthly_limit FROM category_budgets WHERE user_id=%s AND category=%s',
                    (uid, category)
                )
                row = c.fetchone()
        return float(row[0]) if row else None
    except Exception:
        return None

def get_all_category_budgets(uid: int) -> list:
    """Получить все лимиты пользователя."""
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    'SELECT category, monthly_limit FROM category_budgets WHERE user_id=%s ORDER BY category',
                    (uid,)
                )
                return list(c.fetchall())
    except Exception:
        return []

def get_month_spent_by_category(uid: int, category: str) -> float:
    """Получить сумму трат по категории за текущий месяц."""
    month = datetime.now(TZ).strftime('%Y-%m')
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('''
                    SELECT COALESCE(SUM(amount), 0) FROM transactions
                    WHERE user_id=%s AND type='exp' AND category=%s
                    AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s
                ''', (uid, category, month))
                row = c.fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0

def check_budget_alert(uid: int, category: str, amount: float, lang: str = 'ru') -> str:
    """Проверить лимит бюджета. Вернуть предупреждение или пустую строку."""
    limit = get_category_budget(uid, category)
    if not limit or limit <= 0:
        return ''
    
    spent = get_month_spent_by_category(uid, category)
    new_spent = spent + amount
    ratio = new_spent / limit
    
    if ratio >= 1.0:
        # Превышен лимит
        over_by = new_spent - limit
        if lang == 'ru':
            return (
                f"🚨 *ВНИМАНИЕ! Лимит превышен!*\n\n"
                f"📊 {category}: потрачено `{uzs(new_spent)}` из `{uzs(limit)}`\n"
                f"💸 Превышение на `{uzs(over_by)}`"
            )
        else:
            return (
                f"🚨 *DIQQAT! Limit oshildi!*\n\n"
                f"📊 {category}: sarflangan `{uzs(new_spent)}` dan `{uzs(limit)}`\n"
                f"💸 Oshirish `{uzs(over_by)}`"
            )
    elif ratio >= 0.8:
        # Осталось меньше 20%
        left = limit - new_spent
        if lang == 'ru':
            return (
                f"⚠️ *{category} — осталось всего {uzs(left)}!*\n\n"
                f"📊 Потрачено `{uzs(new_spent)}` из `{uzs(limit)}` ({int(ratio*100)}%)"
            )
        else:
            return (
                f"⚠️ *{category} — atigi {uzs(left)} qoldi!*\n\n"
                f"📊 Sarflangan `{uzs(new_spent)}` dan `{uzs(limit)}` ({int(ratio*100)}%)"
            )
    
    return ''

# ────────────────────────── CHAT HISTORY ─────────────────────────────
def db_get_chat_history(uid: int, limit: int = 20) -> list:
    """Получить последние N сообщений истории чата из БД."""
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    '''SELECT role, content FROM chat_history
                       WHERE user_id=%s ORDER BY created_at DESC LIMIT %s''',
                    (uid, limit)
                )
                rows = c.fetchall()
        return [{'role': r[0], 'content': r[1]} for r in reversed(rows)]
    except Exception as e:
        logger.error(f'db_get_chat_history error: {e}')
        return []


def db_save_chat_message(uid: int, role: str, content: str):
    """Сохранить одно сообщение в историю чата."""
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    'INSERT INTO chat_history(user_id, role, content) VALUES(%s,%s,%s)',
                    (uid, role, content)
                )
                # Удалить старые сообщения, оставить только последние 30
                c.execute(
                    '''DELETE FROM chat_history WHERE user_id=%s AND id NOT IN (
                        SELECT id FROM chat_history WHERE user_id=%s
                        ORDER BY created_at DESC LIMIT 30
                    )''',
                    (uid, uid)
                )
            conn.commit()
    except Exception as e:
        logger.error(f'db_save_chat_message error: {e}')


def db_clear_chat_history(uid: int):
    """Очистить историю чата пользователя."""
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('DELETE FROM chat_history WHERE user_id=%s', (uid,))
            conn.commit()
    except Exception as e:
        logger.error(f'db_clear_chat_history error: {e}')

# ────────────────────────── CURRENCY ──────────────────────────────
def get_rates() -> dict:
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
If text contains correction keywords (исправь/тузат/ошибся/неправильно/переписать/неверно/не так/измени/wrong/noto'g'ri/o'zgartir) return:
{"action":"fix","amount":NEW_AMOUNT_OR_NULL,"description":"NEW_DESC_OR_NULL"}
If text contains cancellation (отмени/отменить/bekor) return:
{"action":"cancel"}
If text contains "что если", "что будет если", "если я буду", "a agar", "nima bo'ladi agar", "подсчитай сколько", return:
{"action":"scenario","question":"original user question"}
If text describes the user having a CURRENT BALANCE on card/account (not a transaction), return:
{"action":"balance_info","amount":NUMBER}
Keywords for balance: у меня на карте, на счету, у меня есть, остаток, сейчас на руках, menda bor, kartamda, hisobimda, balansim, pulim bor
If text means the user has PAID OFF or CLOSED a debt (закрыл кредит, оплатил кредит, погасил долг, выплатил, kredito to'ladim, qarzni yopdim, kredit to'liq to'ladim), return:
{"action":"debt_paid","amount":NUMBER_OR_NULL}
If text is about adding/recording a debt or loan, return {"action":"add_debt"}
Keywords for debt: добавить кредит, записать кредит, у меня кредит, взял кредит, есть долг, новый кредит, мой кредит, кредит в банке, Капиталбанк, Хамкорбанк, Ипотека, kredit qo'sh, qarz qo'sh, kreditim bor, qarzim bor, qarzimni yoz, kredit oldim, qarz oldim, yangi kredit, kreditni qo'shmoqchiman, oylik to'lov, kredit yoz, kreditga oldim, bankdan oldim, ипотека
If no amount is mentioned, return {"action":"no_amount"} instead of guessing amount"""

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
    gender = user.get('gender', 'unknown')

    if lang == 'ru':
        if gender == 'male':
            gender_hint = '\n• Пользователь — мужчина. Обращайся к нему строго в мужском роде: потратил, заработал, добавил, сделал, купил.'
        elif gender == 'female':
            gender_hint = '\n• Пользователь — женщина. Обращайся к ней строго в женском роде: потратила, заработала, добавила, сделала, купила.'
        else:
            gender_hint = '\n• Пол пользователя неизвестен. Используй нейтральные формулировки без глаголов прошедшего времени.'
    else:
        gender_hint = ''  # в узбекском языке глаголы не имеют рода

    if lang == 'uz':
        goal_uz = goal if goal else "yo'q"
        mood_uz = user.get('last_mood', '') or ''
        mood_ctx_uz = ''
        if mood_uz == 'хорошо':
            mood_ctx_uz = "\n• Kayfiyat: yaxshi 😊 — quvnoq va iliq gapir"
        elif mood_uz == 'нормально':
            mood_ctx_uz = "\n• Kayfiyat: normal — tinch va qo'llab-quvvatlovchi gapir"
        elif mood_uz == 'тяжело':
            mood_ctx_uz = "\n• Kayfiyat: og'ir 😟 — ayniqsa muloyim, g'amxo'rlik bilan gapir, bosim o'tkazma"

        return (
            f"Siz Finora — {name} ning shaxsiy moliyaviy do'stisiz. "
            f"Birgalikda moliyaviy erkinlikka intilasiz.\n\n"
            f"MA'LUMOT:\n"
            f"• Daromad: {income} {cur} ({freq})" +
            (f"\n• Qo'shimcha: {side} {cur}" if side else '') +
            f"\n• Maqsad: {goal_uz}" +
            mood_ctx_uz +
            f"\n\nSIZNING ROLINGIZ:\n"
            f"Siz faqat sovetchidan emas, balki samimiy do'stsiz. "
            f"O'zbek tilida gaplashing. Qisqa, amaliy, do'stona, emotsional. "
            f"Emoji ishlating 💬. Qo'llab-quvvatlang, tushunib bering, ilhomlantiring.\n\n"
            f"MASLAHATLAR:\n"
            f"O'zbekiston uchun real moliyaviy vositalar: "
            f"depozitlar (Kapitalbank, Hamkorbank), UZSE aksiyalari, oltin, ko'chmas mulk."
        )
    else:
        mood = user.get('last_mood', '') or ''
        mood_ctx = ''
        if mood == 'хорошо':
            mood_ctx = '\n• Настроение: хорошее 😊 — говори энергично и с радостью'
        elif mood == 'нормально':
            mood_ctx = '\n• Настроение: нормальное — говори спокойно и поддерживающе'
        elif mood == 'тяжело':
            mood_ctx = '\n• Настроение: тяжёлое 😟 — говори особенно мягко, с заботой и поддержкой, без давления'

        return (
            f"Ты — Finora, личный финансовый друг {name}. "
            f"Вы вместе идёте к финансовой свободе.\n\n"
            f"ИНФОРМАЦИЯ О ПОЛЬЗОВАТЕЛЕ:\n"
            f"• Доход: {income} {cur} ({freq})" +
            (f"\n• Доп. доход: {side} {cur}" if side else '') +
            f"\n• Финансовая цель: {goal or 'не задана'}" +
            mood_ctx +
            gender_hint +
            f"\n\nТВОЯ РОЛЬ:\n"
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
    try:
        raw = await asyncio.to_thread(_chat, _NAME_EXTRACT_SYS, text, 100)
        raw = raw.replace('```json', '').replace('```', '').strip()
        data = json.loads(raw)
        name = data.get('name')
        if name and isinstance(name, str) and len(name.strip()) >= 2:
            return name.strip()[:50]  # limit length
    except Exception as e:
        logger.warning(f'Name extraction failed: {e}')
    # Fallback: find capitalized word(s) that look like a name
    words = text.strip().split()
    for word in words:
        cleaned = word.strip('.,!?«»"\'').capitalize()
        if len(cleaned) >= 2 and cleaned[0].isupper():
            return cleaned[:50]
    return words[0].capitalize()[:50] if words else None


_GENDER_SYS = """Analyze the name and determine gender.
Return ONLY valid JSON, no markdown:
{"gender": "male"} or {"gender": "female"} or {"gender": "unknown"}

Common male names in CIS: Алишер, Бахтиёр, Шерзод, Фарход, Рустам, Камиль, Руслан, Влад, Алекс, Олег, Иван, Санжар, Жасур, Ботир, Одил, Фарход, Илхом, Шухрат, Акмал, Нодир, Бекзод, Сирож, Азиз, Зафар, Али, Бобир, Файзулло, Олим, Саид, Мухаммад, Абдулла, Комил, Нурали, Сарвар, Акбар, Равшан, Зафар, Асад, Камол, Улугбек, Азизбек, Дониёр, Жавлон, Шохруз, Исо, Элдор, Самвел, Артур, Марлен, Роланд, Максим, Дмитрий, Сергей, Андрей, Павел, Игорь, Виктор, Евгений, Артём, Даниил, Кирилл, Марат, Тимур, Ильдар, Булат, Артур, Рашид, Сабир, Самир, Ариф, Рамазан, Адам, Муслим, Азamat, Рустамбек, Жасурбек, Фарходбек

Common female names in CIS: Финора, Диана, Мадина, Нигина, Шахноза, Севара, Гулнора, Мавлуда, Мукаддас, Зухра, Нилуфар, Гулсара, Хилола, Дилфуза, Наргиза, Назгул, Умеда, Дилрабо, Шоира, Гулчехра, Нилуфар, Фарида, Мастура, Тоира, Мавлюда, Ситора, Шаходат, Розия, Озода, Мадина, Сабрина, Зарина, Алина, Карина, Вика, Аня, Маша, Даша, Лилия, Виолетта, Амина, Камила, Ясмин, Алия, Гульнара, Азиза, Наргис, Малика, Самира, Тамара, Зара, Катя, Настя, Полина, Оля, Ира, Лена, Таня, Света, Юля, Галя, Надя, Люба, Валя, Зина, Rita, Sophie, Maria, Anna, Olga, Natasha, Elena, Irina, Svetlana, Tatiana, Victoria, Anastasia, Yulia, Oksana, Natalia

If name is ambiguous or not in lists, return {"gender": "unknown"}"""

async def ai_detect_gender(name: str) -> str:
    """Определяет пол по имени через AI."""
    if not name or len(name) < 2:
        return 'unknown'
    try:
        raw = await asyncio.to_thread(_chat, _GENDER_SYS, f"Name: {name}", 50)
        raw = raw.replace('```json', '').replace('```', '').strip()
        data = json.loads(raw)
        return data.get('gender', 'unknown')
    except Exception as e:
        logger.warning(f'Gender detection failed for "{name}": {e}')
        return 'unknown'


# ────────────────────────── ИЗМЕНЕНИЕ 2: Умный парсер чисел ────────
async def ai_parse_number(text: str) -> float | None:
    """
    Конвертирует человеческое описание числа в float.
    "2,5 млн" → 2500000.0
    "полтора миллиона" → 1500000.0
    "500к" → 500000.0
    "три тысячи двести" → 3200.0
    """
    # Сначала простая конвертация
    try:
        return float(text.strip().replace(' ', '').replace(',', '.'))
    except ValueError:
        pass
    # Если не вышло — спрашиваем AI
    try:
        prompt = (
            f"Convert this text to a number. Return ONLY the number, nothing else.\n"
            f"Examples: '2,5 млн'→2500000, 'полтора миллиона'→1500000, "
            f"'три тысячи'→3000, '500к'→500000, '250 тыщ'→250000, "
            f"'2.5M'→2500000, 'ярим миллион'→500000\n"
            f"Text: {text}"
        )
        raw = await asyncio.to_thread(
            _chat,
            'Return ONLY a plain number. No text, no units, no symbols. Just digits.',
            prompt, 50
        )
        return float(raw.strip().replace(',', '.').replace(' ', ''))
    except Exception as e:
        logger.warning(f'ai_parse_number failed for "{text}": {e}')
        return None


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


async def generate_goal_plan(uid: int, lang: str) -> str:
    """Генерирует конкретный план достижения цели после онбординга."""
    u      = get_user(uid)
    goal   = u.get('goal', '')
    income = u.get('income_amt', 0)
    side   = u.get('side_income', 0)
    stats  = get_stats(uid)
    bal    = stats['m_inc'] - stats['m_exp']

    if not goal or income == 0:
        return ''

    prompt = (
        f"Пользователь {u.get('name', '')} только что зарегистрировался.\n"
        f"Его цель: {goal}\n"
        f"Доход: {uzs(income)}/мес"
        + (f", доп.доход: {uzs(side)}/мес" if side else '') +
        f"\n\nСоставь конкретный персональный план:\n"
        f"1. Оцени сколько нужно денег для цели (если можно посчитать)\n"
        f"2. Сколько реально откладывать в месяц (~20% от дохода)\n"
        f"3. За сколько месяцев достигнет цели\n"
        f"4. Один конкретный первый шаг который можно сделать уже сегодня\n\n"
        f"Говори тепло, коротко, с конкретными цифрами. "
        f"Язык: {'русский' if lang == 'ru' else 'узбекский'}."
    )
    sys = (
        "Ты Финора — личный финансовый друг. "
        "Только что познакомилась с пользователем. "
        "Даёшь конкретный реалистичный план, говоришь как близкий человек. "
        "Используй эмодзи 💎"
    )
    try:
        plan = await asyncio.to_thread(_chat, sys, prompt, 500)
        return plan.replace('**', '*')
    except Exception as e:
        logger.error(f'generate_goal_plan error: {e}')
        return ''


async def ai_chat(uid: int, lang: str, text: str, context: ContextTypes.DEFAULT_TYPE = None) -> str:
    user   = get_user(uid)
    stats  = get_stats(uid)
    recent = get_recent(uid, limit=15)

    fin_ctx = (
        f"Финансы пользователя: доходы {uzs(stats['inc'])}, расходы {uzs(stats['exp'])}, "
        f"баланс {uzs(stats['inc'] - stats['exp'])}. "
        f"Этот месяц: доходы {uzs(stats['m_inc'])}, расходы {uzs(stats['m_exp'])}."
    )

    recent_str = ''
    if recent:
        recent_str = "\n\nПоследние транзакции:\n" + '\n'.join(
            f"{'➕' if r[0] == 'inc' else '➖'} {uzs(r[1])} — {r[2]} ({r[3]})"
            for r in recent[:15]
        )

    sys_prompt = build_advisor_system(user, lang) + f"\n\nТекущая финансовая ситуация: {fin_ctx}{recent_str}"

    # Загружаем историю из БД (персистентная)
    chat_history = db_get_chat_history(uid, limit=20)

    messages = [{'role': 'system', 'content': sys_prompt}]
    messages.extend(chat_history)
    messages.append({'role': 'user', 'content': text})

    try:
        response = client.chat.completions.create(
            model=OR_MODEL,
            max_tokens=600,
            messages=messages
        )
        reply = response.choices[0].message.content.strip().replace('**', '*')

        # Сохраняем в БД
        db_save_chat_message(uid, 'user', text)
        db_save_chat_message(uid, 'assistant', reply)

        return reply
    except Exception as e:
        logger.error(f'AI chat error: {e}')
        return '❌ Извини, что-то пошло не так. Попробуй ещё раз!' if lang == 'ru' else '❌ Kechirasiz, xatolik. Qayta urinib ko\'ring!'

# ────────────────────────── VOICE (Groq Whisper) ──────────────────
def _transcribe_sync(ogg_path: str, lang: str) -> str | None:
    with open(ogg_path, 'rb') as f:
        ogg_bytes = f.read()
    hint = 'ru' if lang == 'ru' else 'uz'

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
    if _random.random() < 0.10:
        return _random.choice(MOTIVATIONS.get(lang, MOTIVATIONS['ru']))
    return ''

# ════════════════════════════════════════════════════════════════
# 🎭 ЖИВЫЕ ЭМОЦИИ — главная фишка бота! (Русский + Узбекский)
# ════════════════════════════════════════════════════════════════

BIG_EXP_EMOTIONS = {
    'ru': {
        'huge': [
            "😱 Ого, {name}! Это же огромная сумма! Ты в порядке?",
            "🤯 {name}, {amount} за один раз! Серьёзно?",
            "💸 {name}, такая сумма за раз... Надеюсь всё хорошо?",
        ],
        'big': [
            "😮 {name}, прилично! На что-то важное?",
            "💰 {name}, заметная сумма. Всё ок?",
            "👀 {name}, много... Ты уверен что всё в порядке?",
        ],
        'small': [
            "😊 {name}, отличный выбор! Мелочи — тоже важно!",
            "💚 {name}, хорошо что записываешь даже мелочи!",
            "✨ {name}, из таких покупок складывается счастье!",
        ],
    },
    'uz': {
        'huge': [
            "😱 Voy, {name}! Bu katta suma! Yaxshimisiz?",
            "🤯 {name}, {amount} bir martada! Rostdan ham?",
            "💸 {name}, bu qancha pul... Hammasi yaxshimi?",
        ],
        'big': [
            "😮 {name}, sezilarli! Biror muhim narsaga-mi?",
            "💰 {name}, ko'p... Hammasi joyidami?",
            "👀 {name}, ko'p pul... Ishonchingiz komilmi?",
        ],
        'small': [
            "😊 {name}, zo'r tanlov! Maydalari ham muhim!",
            "💚 {name}, hatto maydalarini ham yozayapsiz!",
            "✨ {name}, shunday xarajatlardan baxt yig'iladi!",
        ],
    }
}

DEBT_PAY_EMOTIONS = {
    'ru': [
        "🎉 *{name}*, ты сделал это! Ещё один шаг к свободе!",
        "💪 {name}, так держать! Каждый платёж приближает к цели!",
        "⭐ {name}, серьёзный подход! Уважаю!",
        "🔥 {name}, финансовый воин! Продолжай в том же духе!",
        "💎 {name}, закрываем долги один за другим!",
    ],
    'uz': [
        "🎉 *{name}*, buni qildingiz! Erkinlikka yana bir qadam!",
        "💪 {name}, davom eting! Har bir to'lov maqsadga yaqinlashtiradi!",
        "⭐ {name}, jiddiy yondashuv! Hurmat!",
        "🔥 {name}, moliyaviy jangchi! Shunday davom eting!",
        "💎 {name}, qarzlarni birin-ketin yopmoqdamiz!",
    ]
}

DEBT_CLOSED_EMOTIONS = {
    'ru': [
        "🎉🎉🎉 *{name}*, ТЫ ЗАКРЫЛ КРЕДИТ!!! ЭТО КОСМОС!!! 💎💎💎",
        "🥳 *{name}*, с этим кредитом покончено! Ты свободнее чем вчера!",
        "🏆 *{name}*, вот это достижение! Кредит закрыт, ты молодец!",
        "💎 *{name}*, один долг меньше — свободы больше! Празднуй!",
    ],
    'uz': [
        "🎉🎉🎉 *{name}*, KREDIT YOPILDI!!! BU AJOYIB!!! 💎💎💎",
        "🥳 *{name}*, bu kredit tugadi! Siz dashingiz ozodroq!",
        "🏆 *{name}*, bu yutuq! Kredit yopildi, siz yaxshimisiz!",
        "💎 *{name}*, bir qarz kamaydi — ozodlik ortdi! Bayram qiling!",
    ]
}

SAVINGS_EMOTIONS = {
    'ru': [
        "💎 *{name}*, ты копишь! Это путь к мечте!",
        "🏦 {name}, накопления растут! Продолжай!",
        "🎯 {name}, каждый сэкономленный сум — это шаг к цели!",
    ],
    'uz': [
        "💎 *{name}*, siz tejayapsiz! Bu orzuga olib boradi!",
        "🏦 {name}, jamg'arma oshmoqda! Davom eting!",
        "🎯 {name}*, har bir tejagan so'm maqsadga qadam!",
    ]
}

def get_emotion_for_amount(uid: int, amount: float, tx_type: str, lang: str) -> str:
    """Генерирует эмоциональную реакцию на транзакцию (Русский + Узбекский)."""
    user = get_user(uid)
    name = user.get('name', 'друг' if lang == 'ru' else "do'st")
    income = user.get('income_amt', 0) or 0
    
    # Доходы — всегда позитив
    if tx_type == 'inc':
        emotions = SAVINGS_EMOTIONS.get(lang, SAVINGS_EMOTIONS['ru'])
        emotion = _random.choice(emotions)
        return emotion.format(name=name, amount=uzs(amount))
    
    # Расходы — зависит от размера относительно дохода
    if income > 0:
        ratio = amount / income
        if ratio >= 3:
            emotions = BIG_EXP_EMOTIONS.get(lang, BIG_EXP_EMOTIONS['ru'])['huge']
        elif ratio >= 1:
            emotions = BIG_EXP_EMOTIONS.get(lang, BIG_EXP_EMOTIONS['ru'])['big']
        else:
            emotions = BIG_EXP_EMOTIONS.get(lang, BIG_EXP_EMOTIONS['ru'])['small']
    else:
        # Нет данных о доходе — случайная позитивная реакция
        emotions = BIG_EXP_EMOTIONS.get(lang, BIG_EXP_EMOTIONS['ru'])['small']
    
    emotion = _random.choice(emotions)
    return emotion.format(name=name, amount=uzs(amount))

def get_debt_pay_emotion(uid: int, lang: str) -> str:
    """Эмоция при платеже по кредиту."""
    user = get_user(uid)
    name = user.get('name', 'друг' if lang == 'ru' else "do'st")
    emotions = DEBT_PAY_EMOTIONS.get(lang, DEBT_PAY_EMOTIONS['ru'])
    return _random.choice(emotions).format(name=name)

def get_debt_closed_emotion(uid: int, lang: str) -> str:
    """Эмоция при полном закрытии кредита."""
    user = get_user(uid)
    name = user.get('name', 'друг' if lang == 'ru' else "do'st")
    emotions = DEBT_CLOSED_EMOTIONS.get(lang, DEBT_CLOSED_EMOTIONS['ru'])
    return _random.choice(emotions).format(name=name)


# ────────────────────────── ИЗМЕНЕНИЕ 5: Проактивные инсайты ─────
async def maybe_send_insight(uid: int, lang: str, context: ContextTypes.DEFAULT_TYPE):
    """Отправить AI-инсайт каждые 10 транзакций."""
    try:
        u = get_user(uid)
        count = u.get('tx_count_since_insight', 0) or 0
        if count < 10:
            return
        set_user(uid, tx_count_since_insight=0)
    except Exception:
        return

    recent = get_recent(uid, limit=30)
    stats  = get_stats(uid)
    if not recent:
        return

    tx_text = '\n'.join(
        f"{'доход' if r[0]=='inc' else 'расход'} {uzs(r[1])} — {r[2]} ({r[3]})"
        for r in recent
    )
    u_insight = get_user(uid)
    gender = u_insight.get('gender', 'unknown')
    if gender == 'male':
        gender_note = ' Пользователь мужчина — обращайся в мужском роде.'
    elif gender == 'female':
        gender_note = ' Пользователь женщина — обращайся в женском роде.'
    else:
        gender_note = ''
    prompt = (
        f"Проанализируй последние транзакции и найди 1-2 конкретных паттерна.\n\n"
        f"Транзакции:\n{tx_text}\n\n"
        f"Статистика месяца: доходы {uzs(stats['m_inc'])}, расходы {uzs(stats['m_exp'])}\n\n"
        f"Напиши коротко (3-4 предложения). Конкретные цифры. "
        f"Говори как умная подруга.{gender_note} Язык: {'русский' if lang=='ru' else 'узбекский'}."
    )
    try:
        insight = await asyncio.to_thread(
            _chat,
            "Ты Финора — финансовый аналитик и личный друг. Говори тепло, конкретно, с эмодзи.",
            prompt, 300
        )
        await context.bot.send_message(uid, f"💡 {insight.replace('**','*')}", parse_mode='Markdown')
    except Exception as e:
        logger.warning(f'Insight send error for {uid}: {e}')

# ────────────────────────── ИЗМЕНЕНИЕ 6: Прогноз на конец месяца ──
def forecast_month_end(uid: int) -> dict:
    """Прогнозирует остаток к концу месяца на основе текущего темпа трат."""
    now       = datetime.now(TZ)
    day       = now.day
    if day == 0:
        return {}
    days_left    = max(30 - day, 0)
    stats        = get_stats(uid)
    daily_spend  = stats['m_exp'] / day if day > 0 else 0
    forecast_exp = stats['m_exp'] + (daily_spend * days_left)
    forecast_bal = stats['m_inc'] - forecast_exp
    return {
        'forecast_exp': forecast_exp,
        'forecast_bal': forecast_bal,
        'days_left':    days_left,
    }

# ────────────────────────── FORMATTERS ────────────────────────────
def fmt_tx_msg(parsed: dict, lang: str, rates: dict) -> str:
    cur   = parsed.get('currency', 'UZS')
    amt   = parsed['amount']
    sign  = '+' if parsed['type'] == 'inc' else '-'
    label = tx('ru', 'type_inc') if parsed['type'] == 'inc' else tx('ru', 'type_exp')
    if lang == 'uz':
        label = tx('uz', 'type_inc') if parsed['type'] == 'inc' else tx('uz', 'type_exp')

    saved_word = 'Yozib oldim' if lang == 'uz' else 'Записала'
    amt_str = fmt_amount(amt, cur, rates)
    text = (f"✅ {saved_word}!\n\n"
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

    elif state == STATE_GENDER:
        kb = [[
            InlineKeyboardButton('👩 Женщина' if lang == 'ru' else '👩 Ayol', callback_data='gender_female'),
            InlineKeyboardButton('👨 Мужчина' if lang == 'ru' else '👨 Erkak', callback_data='gender_male'),
        ], [back_btn]]
        msg = (
            f"✨ *{name}*, а ты кто?\n\n"
            f"Это поможет мне общаться с тобой теплее 😊"
            if lang == 'ru' else
            f"✨ *{name}*, siz kimsiz?\n\n"
            f"Bu menga siz bilan iliqroq muloqot qilishga yordam beradi 😊"
        )
        await context.bot.send_message(
            chat_id, msg,
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
        # ИЗМЕНЕНИЕ 3: Кнопка "Не знаю / Пропустить"
        skip_btn = InlineKeyboardButton(
            '🤷 Не знаю / Пропустить' if lang == 'ru' else "🤷 Bilmayman / O'tkazib yuborish",
            callback_data='income_skip'
        )
        kb = [[skip_btn], [back_btn]]
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

    elif state == STATE_DEBT_BANK:
        ds = get_debt_state(uid)
        progress = f" ({ds['current'] + 1}/{ds['target']})" if ds['target'] > 1 else ''
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id,
            f"🏦 *Кредит{progress}*\n\nНазвание банка или кредитора?" if lang == 'ru'
            else f"🏦 *Kredit{progress}*\n\nBank yoki kreditor nomi?",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_DEBT_AMT:
        kb = [[back_btn]]
        u_debt = get_user(uid)
        gender = u_debt.get('gender', 'unknown')
        if gender == 'female':
            debt_q_ru = '💵 Сколько ты должна? Напиши сумму долга:'
        else:
            debt_q_ru = '💵 Сколько ты должен? Напиши сумму долга:'
        await context.bot.send_message(
            chat_id,
            debt_q_ru if lang == 'ru'
            else "💵 Qancha qarzingiz bor? Qarz miqdorini yozing:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_DEBT_RATE:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id,
            '📊 Процентная ставка в год? (например: *24*):' if lang == 'ru'
            else "📊 Yillik foiz stavkasi? (masalan: *24*):",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_DEBT_MONTHLY:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id,
            '📅 Ежемесячный платёж? Напиши сумму:' if lang == 'ru'
            else "📅 Oylik to'lov? Miqdorni yozing:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_DEBT_DEADLINE:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id,
            '⏳ Срок погашения? (например: *Июнь 2026* или *12 месяцев*):' if lang == 'ru'
            else "⏳ To'lash muddati? (masalan: *Iyun 2026* yoki *12 oy*):",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

    elif state == STATE_NOTIFY_TIME:
        kb = [[back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_notify_time'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )

async def handle_bug_report(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, is_voice: bool = False):
    uid      = upd.effective_user.id
    user     = get_user(uid)
    lang     = user.get('language', 'ru')
    name     = user.get('name', 'Пользователь')
    username = upd.effective_user.username or 'no_username'

    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                'INSERT INTO bug_reports(user_id, username, description) VALUES(%s,%s,%s) RETURNING id',
                (uid, username, text)
            )
            report_id = c.fetchone()[0]
        conn.commit()

    stats = get_stats(uid)
    state = user.get('onboarding_state', 'unknown')

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
            ADMIN_ID, admin_msg,
            reply_markup=InlineKeyboardMarkup(kb_admin),
            parse_mode='Markdown'
        )
    except Exception as e:
        logger.error(f'Failed to send bug report to admin: {e}')

    set_user(uid, onboarding_state=STATE_DONE)

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

    # Если пользователь уже прошёл онбординг (добавил кредиты из /settings) — просто возвращаем в STATE_DONE
    u_check = get_user(uid)
    if u_check.get('onboarding_done'):
        set_user(uid, onboarding_state=STATE_DONE)
    else:
        set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, context)

async def cmd_bug(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = upd.effective_user.id
    user = get_user(uid)
    lang = user.get('language', 'ru')
    await upd.message.reply_text(
        '🐛 Опишите проблему подробно:' if lang == 'ru' else '🐛 Muammoni batafsil yozing:',
        parse_mode='Markdown'
    )
    set_user(uid, onboarding_state=STATE_BUG_REPORT)

async def setup_bot_ui(app: Application):
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
        BotCommand('clear', '🗑 Очистить данные'),
        BotCommand('budgets', '💰 Бюджет по категориям'),
    ]
    await app.bot.set_my_commands(commands)

# ─── CALLBACKS ───────────────────────────────────────────────────
async def on_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = upd.callback_query
    data = q.data
    uid = upd.effective_user.id
    chat_id = upd.effective_chat.id
    lang = get_lang(uid)

    # ─── ИЗМЕНЕНИЕ 1.3: Голосовое подтверждение ───
    if data == 'voice_confirm':
        pending = ctx.user_data.pop('pending_voice_tx', None)
        ctx.user_data.pop('pending_voice_text', None)
        if not pending:
            await q.answer('❌ Данные потеряны' if lang == 'ru' else "❌ Ma'lumot yo'qoldi")
            return
        rates     = await asyncio.to_thread(get_rates)
        cur       = pending.get('currency', 'UZS')
        items_lst = pending.get('items', [])
        items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
        add_tx(uid, pending['type'], pending['amount'],
               pending.get('description', ''), pending.get('category', '❓ Другое'),
               cur, items_str)
        # 🎭 ЖИВЫЕ ЭМОЦИИ для голосового подтверждения (callback)
        emotion = get_emotion_for_amount(uid, pending['amount'], pending['type'], lang)
        reply = fmt_tx_msg(pending, lang, rates) + maybe_motivate(lang) + f"\n\n{emotion}"
        await q.answer('✅')
        await q.edit_message_text(reply, parse_mode='Markdown')
        return

    if data == 'voice_fix':
        set_user(uid, onboarding_state='voice_fix_pending')
        await q.answer()
        await q.edit_message_text(
            '✏️ *Что исправить?*\n\nНапиши или скажи голосом:\n\n'
            '_Например: "сумма 250000" или "категория еда"_'
            if lang == 'ru' else
            '✏️ *Nimani tuzatish kerak?*\n\nYozing yoki ovoz bilan ayting:\n\n'
            '_Masalan: "miqdor 250000" yoki "kategoriya ovqat"_',
            parse_mode='Markdown'
        )
        return

    # ─── ИЗМЕНЕНИЕ 3: Кнопка "Не знаю" для дохода ───
    if data == 'income_skip':
        set_user(uid, income_amt=0, onboarding_state=STATE_CURRENCY)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_CURRENCY, ctx)
        return

    # ─── ИЗМЕНЕНИЕ 4: Пропустить кредиты ───
    if data == 'debt_skip_onboarding':
        set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
        return

    # ─── DEBT MANAGEMENT ───
    if data == 'debt_add':
        set_debt_state(uid, target=1, current=0, temp={})
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
        set_user(uid, onboarding_state=STATE_GENDER)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_GENDER, ctx)
        return

    if data == 'name_edit':
        set_user(uid, onboarding_state=STATE_NAME)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_NAME, ctx)
        return

    # ─── GENDER ───
    if data in ('gender_male', 'gender_female'):
        gender_val = 'male' if data == 'gender_male' else 'female'
        set_user(uid, gender=gender_val, onboarding_state=STATE_INCOME_FREQ)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_INCOME_FREQ, ctx)
        return
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
        # ИЗМЕНЕНИЕ 4: Пропустить кредиты + кнопка
        if data == 'goal_debt':
            set_user(uid, onboarding_state=STATE_DEBT_COUNT)
            skip_btn = InlineKeyboardButton(
                '⏭ Пропустить, добавлю позже' if lang == 'ru' else "⏭ O'tkazib yuborish, keyinroq qo'shaman",
                callback_data='debt_skip_onboarding'
            )
            await ctx.bot.send_message(
                chat_id,
                '💳 Сколько у тебя кредитов?\n\nНапиши число от 1 до 50:\n\n_Можешь пропустить и добавить позже через /debts_'
                if lang == 'ru' else
                "💳 Nechta kreditingiz bor?\n\nRaqam yozing (1 dan 50 gacha):\n\n_/debts orqali keyinroq qo'shishingiz mumkin_",
                reply_markup=InlineKeyboardMarkup([[skip_btn]]), parse_mode='Markdown'
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
        # Отправить персональный план цели
        await asyncio.sleep(1)
        plan = await generate_goal_plan(uid, lang)
        if plan:
            header = '🎯 *Вот твой персональный план:*\n\n' if lang == 'ru' else '🎯 *Mana sizning shaxsiy rejangiz:*\n\n'
            await ctx.bot.send_message(chat_id, header + plan, parse_mode='Markdown')
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

    # ─── ADMIN RESET CALLBACKS ───
    if data.startswith('reset_onb_'):
        if uid != ADMIN_ID:
            await q.answer('❌ Нет доступа'); return
        target_uid = int(data[len('reset_onb_'):])
        reset_onboarding_only(target_uid)
        await q.answer('✅ Онбординг сброшен')
        await q.edit_message_text(
            f"✅ *Онбординг сброшен*\n\nUser ID: `{target_uid}`\nТранзакции сохранены.",
            parse_mode='Markdown'
        )
        return

    if data.startswith('reset_full_'):
        if uid != ADMIN_ID:
            await q.answer('❌ Нет доступа'); return
        target_uid = int(data[len('reset_full_'):])
        full_reset_user(target_uid)
        await q.answer('✅ Полный сброс выполнен')
        await q.edit_message_text(
            f"✅ *Полный сброс выполнен*\n\nUser ID: `{target_uid}`\nВсё удалено.",
            parse_mode='Markdown'
        )
        return

    if data.startswith('reset_select_'):
        if uid != ADMIN_ID:
            await q.answer('❌ Нет доступа'); return
        target_uid = int(data[len('reset_select_'):])
        kb = [
            [InlineKeyboardButton('🔄 Сбросить онбординг', callback_data=f'reset_onb_{target_uid}')],
            [InlineKeyboardButton('💥 Полный сброс (всё)', callback_data=f'reset_full_{target_uid}')],
            [InlineKeyboardButton('← Отмена', callback_data='reset_cancel')],
        ]
        await q.edit_message_text(
            f"⚙️ *Сброс пользователя `{target_uid}`*\n\nВыберите тип сброса:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        return

    if data == 'reset_cancel':
        if uid != ADMIN_ID:
            await q.answer('❌ Нет доступа'); return
        await q.answer('Отменено')
        try: await q.message.delete()
        except: pass
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

    if data == 'set_income':
        set_user(uid, onboarding_state='set_income')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id,
            '💰 Напиши новый ежемесячный доход:' if lang == 'ru'
            else '💰 Yangi oylik daromadingizni yozing:',
            parse_mode='Markdown'
        )
        return

    # ─── БОЛЬШИЕ СУММЫ ───
    if data == 'confirm_big_tx':
        pending = ctx.user_data.pop('pending_tx', None)
        if pending:
            rates     = await asyncio.to_thread(get_rates)
            cur       = pending.get('currency', 'UZS')
            items_lst = pending.get('items', [])
            items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
            add_tx(uid, pending['type'], pending['amount'],
                   pending.get('description', ''), pending.get('category', '❓ Другое'),
                   cur, items_str)
            reply = fmt_tx_msg(pending, lang, rates) + maybe_motivate(lang)
            await q.answer('✅')
            await q.edit_message_text(reply, parse_mode='Markdown')
        else:
            await q.answer('❌ Данные потеряны' if lang == 'ru' else "❌ Ma'lumot yo'qoldi")
        return

    if data == 'cancel_big_tx':
        ctx.user_data.pop('pending_tx', None)
        await q.answer()
        await q.edit_message_text('❌ Отменено.' if lang == 'ru' else '❌ Bekor qilindi.')
        return

    # ─── MOOD CHECKIN ───
    if data in ('mood_good', 'mood_ok', 'mood_bad'):
        mood_map = {'mood_good': 'хорошо', 'mood_ok': 'нормально', 'mood_bad': 'тяжело'}
        mood = mood_map[data]
        set_user(uid, last_mood=mood)
        ctx.user_data['last_mood'] = mood

        prompt = (
            f"Пользователь сказал что чувствует себя финансово: {mood}.\n"
            f"Ответь коротко (2-3 предложения) — поддержи, прими, если тяжело — вдохнови. "
            f"Говори как близкий человек. Язык: {'русский' if lang == 'ru' else 'узбекский'}."
        )
        sys = "Ты Finora — финансовый друг. Говоришь тепло и искренне."
        try:
            reply = await asyncio.to_thread(_chat, sys, prompt, 200)
        except Exception:
            reply = '💙' + ('Спасибо что поделился!' if lang == 'ru' else 'Ulashganingiz uchun rahmat!')

        await q.answer()
        await q.edit_message_text(reply, parse_mode='Markdown')
        return

    # ─── BUG 6: Отмена записи через inline-кнопку ───
    if data == 'undo_last_tx':
        if delete_last_tx(uid):
            await q.answer('🗑')
            await q.edit_message_text(
                '🗑 Запись отменена.' if lang == 'ru' else "🗑 Yozuv bekor qilindi."
            )
        else:
            await q.answer('❌ Нечего отменять' if lang == 'ru' else "❌ Bekor qilish mumkin emas")
        return

    await q.answer()

# ────────────────────────── TEXT HANDLER ──────────────────────────
async def on_text(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = upd.message.text.strip()
    uid = upd.effective_user.id
    chat_id = upd.effective_chat.id
    lang = get_lang(uid)
    state = get_state(uid)
    await _process_text_input(uid, chat_id, text, lang, state, upd, ctx)

async def _process_text_input(uid: int, chat_id: int, text: str, lang: str,
                               state: str, upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Общий обработчик текстового ввода — используется и для текста, и для голоса."""

    # ─── DEBT MANAGEMENT ───
    if state == STATE_DEBT_COUNT:
        try:
            count = int(text.strip())
            if count <= 0: raise ValueError('zero')
            # ИЗМЕНЕНИЕ 4: Лимит 50 вместо 10
            if count > 50:
                await upd.message.reply_text(
                    '❌ Максимум 50 кредитов. Напиши число от 1 до 50:' if lang == 'ru'
                    else '❌ Maksimal 50 kredit. 1 dan 50 gacha raqam yozing:',
                    parse_mode='Markdown'
                )
                return
            set_user(uid, onboarding_state=STATE_DEBT_BANK)
            set_debt_state(uid, target=count, current=0, temp={})
            await upd.message.reply_text('✅ Отлично! Начинаю собирать данные...' if lang == 'ru' else "✅ Zo'r! Ma'lumotlarni to'playapman...")
            await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
        except ValueError:
            await upd.message.reply_text(
                '❌ Напиши число от 1 до 50, например: *3*' if lang == 'ru' else "❌ 1 dan 50 gacha raqam yozing, masalan: *3*",
                parse_mode='Markdown'
            )
        return

    elif state == STATE_DEBT_BANK:
        ds = get_debt_state(uid)
        temp = ds['temp']
        temp['bank'] = text
        set_debt_state(uid, temp=temp)
        set_user(uid, onboarding_state=STATE_DEBT_AMT)
        await send_onboarding_step(chat_id, uid, STATE_DEBT_AMT, ctx)
        return

    elif state == STATE_DEBT_AMT:
        # ИЗМЕНЕНИЕ 2: Умный парсер чисел для долга
        try:
            amt = await ai_parse_number(text)
            if amt is None or amt <= 0:
                raise ValueError('bad amount')
            ds = get_debt_state(uid)
            temp = ds['temp']
            temp['amount'] = amt
            set_debt_state(uid, temp=temp)
            set_user(uid, onboarding_state=STATE_DEBT_RATE)
            await send_onboarding_step(chat_id, uid, STATE_DEBT_RATE, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    elif state == STATE_DEBT_RATE:
        try:
            rate = float(text.replace('%', '').replace(' ', '').replace(',', '.'))
            ds = get_debt_state(uid)
            temp = ds['temp']
            temp['rate'] = rate
            set_debt_state(uid, temp=temp)
            set_user(uid, onboarding_state=STATE_DEBT_MONTHLY)
            await send_onboarding_step(chat_id, uid, STATE_DEBT_MONTHLY, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число, например: *24*' if lang == 'ru' else "❌ Raqam yozing, masalan: *24*", parse_mode='Markdown')
        return

    elif state == STATE_DEBT_MONTHLY:
        try:
            monthly = float(text.replace(' ', '').replace(',', '.'))
            ds = get_debt_state(uid)
            temp = ds['temp']
            temp['monthly_payment'] = monthly
            set_debt_state(uid, temp=temp)
            set_user(uid, onboarding_state=STATE_DEBT_DEADLINE)
            await send_onboarding_step(chat_id, uid, STATE_DEBT_DEADLINE, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    elif state == STATE_DEBT_DEADLINE:
        ds = get_debt_state(uid)
        temp = ds['temp']
        temp['deadline'] = text
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute(
                    'INSERT INTO debts(user_id, bank, amount, rate, monthly_payment, deadline) VALUES(%s,%s,%s,%s,%s,%s)',
                    (uid, temp.get('bank', '?'), temp.get('amount', 0), temp.get('rate', 0),
                     temp.get('monthly_payment', 0), temp.get('deadline', '?'))
                )
            conn.commit()
        current = ds['current'] + 1
        target  = ds['target']
        set_debt_state(uid, current=current, temp={})
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

    # ИЗМЕНЕНИЕ 2: STATE_INCOME_AMT с умным парсером
    elif state == STATE_INCOME_AMT:
        cleaned = text.replace("so'm", '').replace('сум', '').replace('$', '').strip()
        amount = await ai_parse_number(cleaned)
        if amount is not None and amount > 0:
            set_user(uid, income_amt=amount, onboarding_state=STATE_CURRENCY)
            await send_onboarding_step(chat_id, uid, STATE_CURRENCY, ctx)
        else:
            await upd.message.reply_text(
                '❌ Не поняла сумму 🤔\n\nНапиши цифрами, например: *500 000* или *2,5 млн*'
                if lang == 'ru' else
                '❌ Miqdorni tushunmadim 🤔\n\nRaqamda yozing, masalan: *500 000* yoki *2,5 mln*',
                parse_mode='Markdown'
            )
        return

    # ИЗМЕНЕНИЕ 2: STATE_SIDE_AMT с умным парсером
    elif state == STATE_SIDE_AMT:
        amount = await ai_parse_number(text)
        if amount is not None and amount > 0:
            set_user(uid, side_income=amount, onboarding_state=STATE_GOAL)
            await send_onboarding_step(chat_id, uid, STATE_GOAL, ctx)
        else:
            await upd.message.reply_text(
                '❌ Не поняла сумму. Напиши цифрами, например: *300 000* или *1 млн*'
                if lang == 'ru' else
                '❌ Miqdorni tushunmadim. Raqamda yozing, masalan: *300 000* yoki *1 mln*',
                parse_mode='Markdown'
            )
        return

    elif state == STATE_GOAL_CUSTOM:
        # Проверяем: не похоже ли сообщение на команду транзакции, а не на цель
        _tx_cmd_words = [
            'добавь', 'запиши', 'потратил', 'потратила', 'купил', 'купила',
            'заработал', 'заработала', 'транзакцию', 'расход', 'доход',
            'на карте', 'на счёт', 'qo\'sh', 'yoz', 'oldim', 'sotib',
        ]
        _text_lower = text.lower()
        _has_number = any(ch.isdigit() for ch in text)
        _looks_like_tx = _has_number and any(w in _text_lower for w in _tx_cmd_words)

        if _looks_like_tx:
            if lang == 'ru':
                await upd.message.reply_text(
                    '😊 Похоже, ты пытаешься добавить транзакцию — но сейчас я жду твою *финансовую цель*.\n\n'
                    'Напиши что-то вроде:\n'
                    '• _"Накопить на машину"_\n'
                    '• _"Не жить от зарплаты до зарплаты"_\n'
                    '• _"Поехать в отпуск"_\n\n'
                    'Транзакцию сможешь добавить сразу после регистрации 💎',
                    parse_mode='Markdown'
                )
            else:
                await upd.message.reply_text(
                    '😊 Tranzaksiya qo\'shmoqchi ko\'rinasiz — lekin hozir men sizning *moliyaviy maqsadingizni* kutmoqdaman.\n\n'
                    'Masalan:\n'
                    '• _"Mashina uchun yig\'ish"_\n'
                    '• _"Ta\'tilga borish"_\n\n'
                    'Tranzaksiyani ro\'yxatdan o\'tgandan keyin qo\'shishingiz mumkin 💎',
                    parse_mode='Markdown'
                )
            return

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
                # Отправить персональный план цели
                await asyncio.sleep(1)
                plan = await generate_goal_plan(uid, lang)
                if plan:
                    header = '🎯 *Вот твой персональный план:*\n\n' if lang == 'ru' else '🎯 *Mana sizning shaxsiy rejangiz:*\n\n'
                    await upd.message.reply_text(header + plan, parse_mode='Markdown')
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
        goal_text = text.strip()
        set_user(uid, goal=goal_text, onboarding_state=STATE_DONE)
        await upd.message.reply_text(
            f"✅ Цель обновлена: _{goal_text}_" if lang == 'ru' else f"✅ Maqsad yangilandi: _{goal_text}_",
            parse_mode='Markdown'
        )
        # Если цель связана с долгами — предложить добавить кредиты
        debt_keywords = ['долг', 'кредит', 'займ', 'qarz', 'kredit']
        if any(kw in goal_text.lower() for kw in debt_keywords):
            with get_conn() as conn:
                with conn.cursor() as c:
                    c.execute('SELECT COUNT(*) FROM debts WHERE user_id=%s', (uid,))
                    debt_count = c.fetchone()[0]
            if debt_count == 0:
                skip_btn = InlineKeyboardButton(
                    '⏭ Позже' if lang == 'ru' else "⏭ Keyinroq",
                    callback_data='debt_skip_onboarding'
                )
                msg = (
                    '💳 *Отличная цель!*\n\n'
                    'Чтобы я помогла составить план погашения, нужно знать твои кредиты.\n\n'
                    'Сколько у тебя кредитов? Напиши число (от 1 до 50):\n\n'
                    '_Можешь пропустить и добавить позже через /debts_'
                    if lang == 'ru' else
                    '💳 *Ajoyib maqsad!*\n\n'
                    'To\'lov rejasini tuzish uchun kreditlaringizni bilishim kerak.\n\n'
                    'Nechta kreditingiz bor? Raqam yozing (1 dan 50 gacha):\n\n'
                    '_/debts orqali keyinroq qo\'shishingiz mumkin_'
                )
                await upd.message.reply_text(
                    msg, reply_markup=InlineKeyboardMarkup([[skip_btn]]), parse_mode='Markdown'
                )
                set_user(uid, onboarding_state=STATE_DEBT_COUNT)
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

    # ИЗМЕНЕНИЕ 2: set_income с умным парсером
    elif state == 'set_income':
        try:
            cleaned = text.replace("so'm", '').replace('сум', '').replace('$', '').strip()
            new_income = await ai_parse_number(cleaned)
            if new_income is None or new_income <= 0:
                raise ValueError('bad')
            set_user(uid, income_amt=new_income, onboarding_state=STATE_DONE)
            await upd.message.reply_text(
                f"✅ Доход обновлён: `{uzs(new_income)}`" if lang == 'ru' else f"✅ Daromad yangilandi: `{uzs(new_income)}`",
                parse_mode='Markdown'
            )
        except Exception:
            await upd.message.reply_text(
                '❌ Напиши число, например: *3000000*' if lang == 'ru' else '❌ Raqam yozing, masalan: *3000000*',
                parse_mode='Markdown'
            )
        return

    # ИЗМЕНЕНИЕ 1.4: voice_fix_pending обработка
    elif state == 'voice_fix_pending':
        pending = ctx.user_data.get('pending_voice_tx', {})
        if not pending:
            # pending_voice_tx потерялся (рестарт сервера) — сбрасываем стейт
            # и обрабатываем сообщение как обычную транзакцию
            set_user(uid, onboarding_state=STATE_DONE)
            await _process_text_input(uid, chat_id, text, lang, STATE_DONE, upd, ctx)
            return

        fix_prompt = (
            f"Пользователь хочет исправить транзакцию.\n"
            f"Текущие данные: {json.dumps(pending, ensure_ascii=False)}\n"
            f"Исправление: {text}\n\n"
            f"Верни ТОЛЬКО JSON с полями которые нужно изменить. "
            f"Пример: {{\"amount\": 250000}} или {{\"category\": \"🍔 Еда\"}}. "
            f"Только изменённые поля, остальные не включай."
        )
        try:
            raw = await asyncio.to_thread(
                _chat, 'Return ONLY valid JSON, no markdown, no explanation.', fix_prompt, 200
            )
            raw = raw.replace('```json', '').replace('```', '').strip()
            fixes = json.loads(raw)
            pending.update(fixes)
            ctx.user_data['pending_voice_tx'] = pending
        except Exception as e:
            logger.warning(f'Voice fix parse error: {e}')

        rates   = await asyncio.to_thread(get_rates)
        cur     = pending.get('currency', 'UZS')
        amt_str = fmt_amount(pending['amount'], cur, rates)
        sign    = '+' if pending['type'] == 'inc' else '-'
        label   = (tx('ru', 'type_inc') if pending['type'] == 'inc' else tx('ru', 'type_exp')) if lang == 'ru' \
                  else (tx('uz', 'type_inc') if pending['type'] == 'inc' else tx('uz', 'type_exp'))

        confirm_text = (
            f"📝 *Обновила. Теперь верно?*\n\n"
            f"{label}: `{sign}{amt_str}`\n"
            f"📝 {pending.get('description', '')}\n"
            f"🏷 {pending.get('category', '')}"
            if lang == 'ru' else
            f"📝 *Yangiladim. Endi to'g'rimi?*\n\n"
            f"{label}: `{sign}{amt_str}`\n"
            f"📝 {pending.get('description', '')}\n"
            f"🏷 {pending.get('category', '')}"
        )
        kb = [[
            InlineKeyboardButton('✅ Верно'     if lang == 'ru' else "✅ To'g'ri",        callback_data='voice_confirm'),
            InlineKeyboardButton('✏️ Ещё раз'  if lang == 'ru' else '✏️ Yana bir bor',  callback_data='voice_fix'),
        ]]
        set_user(uid, onboarding_state=STATE_DONE)
        await upd.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
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
            reply = await ai_chat(uid, lang, text, ctx)
            await upd.message.reply_text(reply, parse_mode='Markdown')
            return

        action = parsed.get('action')

        if action == 'cancel':
            if delete_last_tx(uid):
                msg = '🗑 Последняя транзакция удалена.' if lang == 'ru' else "🗑 Oxirgi tranzaksiya o'chirildi."
                await upd.message.reply_text(msg, parse_mode='Markdown')
            else:
                await upd.message.reply_text(tx(lang, 'no_data'), parse_mode='Markdown')
            return

        # ── BUG 1: debt_paid — пользователь закрыл/оплатил кредит ──
        if action == 'debt_paid':
            amount_val = parsed.get('amount')
            if amount_val and float(amount_val) > 0:
                # Записать как расход с категорией Кредит
                add_tx(uid, 'exp', float(amount_val), 'Платёж по кредиту', '💳 Кредит', 'UZS', '')
                rates_w = await asyncio.to_thread(get_rates)
                parsed_tx = {
                    'type': 'exp',
                    'amount': float(amount_val),
                    'description': 'Платёж по кредиту',
                    'category': '💳 Кредит',
                    'currency': 'UZS',
                    'items': []
                }
                reply = fmt_tx_msg(parsed_tx, lang, rates_w)
                await upd.message.reply_text(
                    reply + '\n\n💳 Записала как платёж по кредиту!',
                    parse_mode='Markdown'
                )
            else:
                await upd.message.reply_text(
                    '💳 Чтобы записать платёж по кредиту — зайди в /debts и нажми "Внести платёж".'
                    if lang == 'ru' else
                    "💳 Kredit to'lovini yozish uchun /debts ga kiring va \"To'lov qilish\" tugmasini bosing.",
                    parse_mode='Markdown'
                )
            return

        # ── BUG 3: balance_info — пользователь говорит об остатке, не о транзакции ──
        if action == 'balance_info':
            reported_balance = parsed.get('amount', 0)
            if reported_balance:
                stats_b = get_stats(uid)
                actual_balance = stats_b['inc'] - stats_b['exp']
                diff = reported_balance - actual_balance
                if lang == 'ru':
                    msg = (
                        f"📊 Спасибо за информацию!\n\n"
                        f"По моим данным твой баланс: `{uzs(actual_balance)}`\n"
                        f"Ты говоришь что на карте: `{uzs(reported_balance)}`\n\n"
                    )
                    if abs(diff) > 100:
                        msg += f"💡 Разница: `{uzs(abs(diff))}` — возможно, не все транзакции записаны?"
                else:
                    msg = f"📊 Ma'lumot uchun rahmat! Balans: `{uzs(reported_balance)}`"
                await upd.message.reply_text(msg, parse_mode='Markdown')
            return

        # ── FIX 2: no_amount — короткий ответ, возможно реакция на вопрос бота ──
        if action == 'no_amount':
            # Отправляем в ai_chat: может это ответ на вопрос Финоры ("да", "работаю" и т.д.)
            reply = await ai_chat(uid, lang, text, ctx)
            await upd.message.reply_text(reply, parse_mode='Markdown')
            return

        if action == 'add_debt':
            set_debt_state(uid, target=1, current=0, temp={})
            set_user(uid, onboarding_state=STATE_DEBT_BANK)
            await upd.message.reply_text(
                '💳 Добавляем кредит!\n\nНазвание банка или кредитора?' if lang == 'ru'
                else "💳 Kredit qo'shamiz!\n\nBank yoki kreditor nomi?",
                parse_mode='Markdown'
            )
            return

        # ─── BUDGET: Установка лимита ───
        if action == 'set_budget':
            category = parsed.get('category', '')
            limit    = parsed.get('amount', 0)
            if category and limit > 0:
                set_category_budget(uid, category, limit)
                await upd.message.reply_text(
                    f"✅ *Лимит установлен!*\n\n🏷 {category}\n💰 {uzs(limit)}/месяц\n\n"
                    f"_Проверить: /budgets_" if lang == 'ru' else
                    f"✅ *Limit o'rnatildi!*\n\n🏷 {category}\n💰 {uzs(limit)}/oyiga\n\n"
                    f"_Tekshirish: /budgets_",
                    parse_mode='Markdown'
                )
            else:
                await upd.message.reply_text(
                    '❌ Не поняла. Напиши: "установи лимит на еду 500 000 в месяц"' if lang == 'ru'
                    else "❌ Tushunmadim. Yozing: \"ovqat uchun oyiga 500 000 limit qo'y\"",
                    parse_mode='Markdown'
                )
            return

        if action == 'fix':
            last = get_last_tx(uid)
            if not last:
                await upd.message.reply_text(tx(lang, 'no_data'), parse_mode='Markdown')
                return
            old_amount = last[2]
            old_desc   = last[3]
            new_amount = parsed.get('amount')
            new_desc   = parsed.get('description')
            update_tx(last[0], amount=new_amount, description=new_desc)

            lines = ['✅ *Исправила!*\n' if lang == 'ru' else '✅ *Tuzatdim!*\n']
            if new_amount is not None and new_amount != old_amount:
                lines.append(f"💰 {'Сумма' if lang == 'ru' else 'Miqdor'}: `{uzs(old_amount)}` → `{uzs(new_amount)}`")
            if new_desc is not None and new_desc != old_desc:
                lines.append(f"📝 {'Описание' if lang == 'ru' else 'Tavsif'}: _{old_desc}_ → _{new_desc}_")

            await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')
            return

        # ИЗМЕНЕНИЕ 7: Сценарии "а что если"
        if action == 'scenario':
            u_sc   = get_user(uid)
            stats  = get_stats(uid)
            income = u_sc.get('income_amt', 0)
            goal   = u_sc.get('goal', '')

            scenario_prompt = (
                f"Вопрос: {parsed.get('question', text)}\n\n"
                f"Данные пользователя:\n"
                f"• Доход: {uzs(income)}/мес\n"
                f"• Расходы этого месяца: {uzs(stats['m_exp'])}\n"
                f"• Баланс: {uzs(stats['m_inc'] - stats['m_exp'])}\n"
                f"• Цель: {goal or 'не задана'}\n\n"
                f"Посчитай конкретно с реальными цифрами. "
                f"Коротко, по-дружески. Язык: {'русский' if lang=='ru' else 'узбекский'}."
            )
            try:
                sc_reply = await asyncio.to_thread(
                    _chat,
                    "Ты Финора — финансовый советник. Считаешь точно, говоришь понятно и тепло.",
                    scenario_prompt, 400
                )
                sc_reply = sc_reply.replace('**', '*')
            except Exception:
                sc_reply = '❌ Не смогла посчитать. Попробуй ещё раз.'
            await upd.message.reply_text(sc_reply, parse_mode='Markdown')
            return

        if 'type' not in parsed or 'amount' not in parsed:
            await upd.message.reply_text(tx(lang, 'parse_error'), parse_mode='Markdown')
            return

        if parsed.get('amount', 0) <= 0:
            await upd.message.reply_text(tx(lang, 'parse_error'), parse_mode='Markdown')
            return

        # Проверка на подозрительно большую сумму
        u3_income = u3.get('income_amt', 0) or 0
        if u3_income > 0 and parsed['amount'] > u3_income * 3 and parsed['type'] == 'exp':
            if ctx is not None:
                ctx.user_data['pending_tx'] = parsed
            cur_symbol = '$' if parsed.get('currency') == 'USD' else ("₽" if parsed.get('currency') == 'RUB' else "so'm")
            confirm_msg = (
                f"⚠️ *Большая сумма!*\n\n"
                f"Ты уверен что потратил `{parsed['amount']:,.0f} {cur_symbol}`?\n\n"
                f"Это больше твоего месячного дохода в 3 раза."
                if lang == 'ru' else
                f"⚠️ *Katta miqdor!*\n\n"
                f"`{parsed['amount']:,.0f} {cur_symbol}` sarflaganingizga ishonchingiz komilmi?\n\n"
                f"Bu oylik daromadingizdan 3 baravar ko'p."
            )
            kb = [[
                InlineKeyboardButton('✅ Да, записать' if lang == 'ru' else "✅ Ha, yozish", callback_data='confirm_big_tx'),
                InlineKeyboardButton('❌ Отмена' if lang == 'ru' else '❌ Bekor', callback_data='cancel_big_tx'),
            ]]
            await upd.message.reply_text(confirm_msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            return

        rates     = await asyncio.to_thread(get_rates)
        cur       = parsed.get('currency', 'UZS')
        items_lst = parsed.get('items', [])
        items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
        add_tx(uid, parsed['type'], parsed['amount'],
               parsed.get('description', ''), parsed.get('category', '❓ Другое'),
               cur, items_str)

        # ─── BUDGET ALERT: проверка лимитов ───
        budget_alert = ''
        if parsed['type'] == 'exp' and parsed.get('category'):
            budget_alert = check_budget_alert(uid, parsed['category'], parsed['amount'], lang)

        # 🎭 ЖИВЫЕ ЭМОЦИИ — реагируем на каждую транзакцию!
        emotion = get_emotion_for_amount(uid, parsed['amount'], parsed['type'], lang)
        reply = fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang) + f"\n\n{emotion}"
        if budget_alert:
            reply += f"\n\n{budget_alert}"

        # ── FIX 3: кнопка отмены объединена с основным сообщением ──
        undo_kb = [[InlineKeyboardButton(
            '🗑 Отменить запись' if lang == 'ru' else "🗑 Yozuvni bekor qilish",
            callback_data='undo_last_tx'
        )]]
        await upd.message.reply_text(reply, reply_markup=InlineKeyboardMarkup(undo_kb), parse_mode='Markdown')

        # Проверка и отправка инсайта
        await maybe_send_insight(uid, lang, ctx)

        # ── BUG 5: Используем regex для точного поиска целых слов ──
        import re as _re_qmark
        _QUESTION_MARKERS_RE = _re_qmark.compile(
            r'\b(почему|как именно|сколько|много ли|мало|нормально ли|стоит ли|советуешь|'
            r'nima|qanday|ko\'p mi|oz mi|normal mi|maslahat)\b|[?][?]?|[?]'
        )
        if _QUESTION_MARKERS_RE.search(text.lower()):
            ai_reply = await ai_chat(uid, lang, text, ctx)
            await upd.message.reply_text(ai_reply, parse_mode='Markdown')


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

    # ИЗМЕНЕНИЕ 6: Прогноз на конец месяца
    fc = forecast_month_end(uid)
    if fc and fc.get('days_left', 0) > 3 and s['m_exp'] > 0:
        fc_bal = fc['forecast_bal']
        fc_str = f"+{uzs(fc_bal)}" if fc_bal >= 0 else f"-{uzs(abs(fc_bal))}"
        msg += (
            f"\n\n🔮 *{'Прогноз на конец месяца' if lang=='ru' else 'Oy oxiriga prognoz'}:*\n"
            f"_{'Если тратить в том же темпе' if lang=='ru' else 'Hozirgi tezlikda sarflasangiz'}:_\n"
            f"{'📉 Итого расходов' if lang=='ru' else '📉 Jami xarajat'}: `{uzs(fc['forecast_exp'])}`\n"
            f"{'💰 Остаток' if lang=='ru' else '💰 Qoldiq'}: `{fc_str}`"
        )

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
        [InlineKeyboardButton(tx(lang, 'set_income'),     callback_data='set_income')],
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


async def cmd_budgets(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать все бюджеты по категориям."""
    uid  = upd.effective_user.id
    lang = get_lang(uid)

    budgets = get_all_category_budgets(uid)

    if not budgets:
        msg = (
            '💰 *Твои лимиты расходов*\n\n'
            'Пока нет лимитов 📭\n\n'
            '_Напиши мне: "установи лимит на еду 500 000 в месяц" — и я создам!_'
            if lang == 'ru' else
            '💰 *Xarajat limitlaringiz*\n\n'
            "Hali limit yo'q 📭\n\n"
            "_Menga yozing: \"ovqat uchun oyiga 500 000 limit qo'y\" — men yarataman!_"
        )
        await upd.message.reply_text(msg, parse_mode='Markdown')
        return

    lines = ['💰 *Твои лимиты расходов:*\n' if lang == 'ru' else "💰 *Xarajat limitlaringiz:*\n"]
    for cat, limit in budgets:
        spent = get_month_spent_by_category(uid, cat)
        ratio = spent / limit if limit > 0 else 0
        pct = min(int(ratio * 100), 100)

        bar = '█' * (pct // 10) + '░' * (10 - pct // 10)
        spent_str = uzs(spent)
        limit_str = uzs(limit)
        left = limit - spent
        left_str = uzs(left)

        if ratio >= 1.0:
            status = '🚨' if lang == 'ru' else '🚨'
            status_text = '⚠️ ПРЕВЫШЕН!' if lang == 'ru' else "⚠️ OSHILDI!"
        elif ratio >= 0.8:
            status = '⚠️'
            status_text = f'Осталось {left_str}' if lang == 'ru' else f'Qoldi {left_str}'
        else:
            status = '✅'
            status_text = f'Осталось {left_str}' if lang == 'ru' else f'Qoldi {left_str}'

        lines.append(
            f"{cat}\n"
            f"  [{bar}] {pct}%\n"
            f"  📊 {spent_str} / {limit_str}\n"
            f"  {status} {status_text}\n"
        )

    lines.append('\n_Изменить лимит: "установи лимит на категорию 300 000"_')
    await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')


async def cmd_reset(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin: сбросить данные пользователя. Только для ADMIN_ID."""
    uid = upd.effective_user.id
    if uid != ADMIN_ID:
        await upd.message.reply_text('❌ Нет доступа')
        return

    args = ctx.args
    
    if args and args[0].isdigit():
        target_uid = int(args[0])
        kb = [
            [InlineKeyboardButton('🔄 Сбросить онбординг', callback_data=f'reset_onb_{target_uid}')],
            [InlineKeyboardButton('💥 Полный сброс (всё)', callback_data=f'reset_full_{target_uid}')],
            [InlineKeyboardButton('← Отмена', callback_data='reset_cancel')],
        ]
        await upd.message.reply_text(
            f"⚙️ *Сброс пользователя `{target_uid}`*\n\nВыберите тип сброса:",
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        return

    users = get_all_users_list(limit=30)
    if not users:
        await upd.message.reply_text('📭 Нет пользователей')
        return

    lines = ['👥 *Список пользователей:*\n']
    kb = []
    for u in users:
        u_id   = u['user_id']
        u_name = u.get('name', '?') or '?'
        u_done = '✅' if u.get('onboarding_done') else '⏳'
        u_lang = u.get('language', '?')
        lines.append(f"{u_done} `{u_id}` — *{u_name}* ({u_lang})")
        kb.append([InlineKeyboardButton(
            f"{u_done} {u_name} ({u_id})",
            callback_data=f'reset_select_{u_id}'
        )])

    kb.append([InlineKeyboardButton('← Закрыть', callback_data='reset_cancel')])
    await upd.message.reply_text(
        '\n'.join(lines),
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

    # ─── BUDGET ALERT: проверка лимитов ───
    budget_alert = ''
    if parsed['type'] == 'exp' and parsed.get('category'):
        budget_alert = check_budget_alert(uid, parsed['category'], parsed['amount'], lang)

    # 🎭 ЖИВЫЕ ЭМОЦИИ для фото чеков
    emotion = get_emotion_for_amount(uid, parsed['amount'], parsed['type'], lang)
    reply = fmt_tx_msg(parsed, lang, rates) + maybe_motivate(lang) + f"\n\n{emotion}"
    if budget_alert:
        reply += f"\n\n{budget_alert}"
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

    # ─── Если есть pending голосовая транзакция — проверить подтверждение ───
    # FIX 5: если pending_voice_tx некорректный (рестарт сервера) — сбрасываем и идём дальше
    if ctx.user_data.get('pending_voice_tx'):
        pending_check = ctx.user_data['pending_voice_tx']
        if not isinstance(pending_check, dict) or 'amount' not in pending_check:
            ctx.user_data.pop('pending_voice_tx', None)
            ctx.user_data.pop('pending_voice_text', None)
        else:
            text_lower = text_result.lower().strip()

            # Слова подтверждения
            confirm_words = [
                'да', 'верно', 'правильно', 'ок', 'окей', 'хорошо', 'всё верно',
                'подтверждаю', 'записывай', 'записать', 'так', 'точно',
                "ha", "to'g'ri", "tasdiqlash", "yozib ol", "yozish"
            ]
            # Слова отказа / исправления
            fix_words = [
                'нет', 'неверно', 'не так', 'не то', 'неправильно', 'исправь',
                'исправить', 'ошибка', 'ошибся', 'переписать', 'измени',
                "yo'q", "noto'g'ri", "tuzat", "xato", "o'zgartir"
            ]

            if any(w in text_lower for w in confirm_words):
                # Подтвердить транзакцию
                pending = ctx.user_data.pop('pending_voice_tx', None)
                ctx.user_data.pop('pending_voice_text', None)
                if pending:
                    rates     = await asyncio.to_thread(get_rates)
                    cur       = pending.get('currency', 'UZS')
                    items_lst = pending.get('items', [])
                    items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
                    add_tx(uid, pending['type'], pending['amount'],
                           pending.get('description', ''), pending.get('category', '❓ Другое'),
                           cur, items_str)
                    # 🎭 ЖИВЫЕ ЭМОЦИИ для голоса
                    emotion = get_emotion_for_amount(uid, pending['amount'], pending['type'], lang)
                    reply = fmt_tx_msg(pending, lang, rates) + maybe_motivate(lang) + f"\n\n{emotion}"
                    await upd.message.reply_text(reply, parse_mode='Markdown')
                    await maybe_send_insight(uid, lang, ctx)
                return

            if any(w in text_lower for w in fix_words):
                # Перейти в режим исправления
                set_user(uid, onboarding_state='voice_fix_pending')
                await upd.message.reply_text(
                    f'🎤 _{text_result}_\n\n'
                    '✏️ *Что исправить?* Скажи или напиши:\n\n'
                    '_Например: "сумма 250000" или "категория еда"_'
                    if lang == 'ru' else
                    f'🎤 _{text_result}_\n\n'
                    '✏️ *Nimani tuzatish kerak?* Ayting yoki yozing:\n\n'
                    '_Masalan: "miqdor 250000" yoki "kategoriya ovqat"_',
                    parse_mode='Markdown'
                )
                return

    if state == STATE_BUG_REPORT:
        await handle_bug_report(upd, ctx, text_result, is_voice=True)
        return

    if state in ONBOARDING_TEXT_STATES:
        await upd.message.reply_text(f'🎤 _{text_result}_', parse_mode='Markdown')
        await _process_text_input(uid, upd.effective_chat.id, text_result, lang, state, upd, ctx)
        return

    # ─── Regular transaction parsing with confirmation ───
    wait2  = await upd.message.reply_text(f'🎤 _{text_result}_\n\n{tx(lang, "processing")}', parse_mode='Markdown')
    parsed = await ai_parse(text_result)
    try: await wait2.delete()
    except: pass

    if not parsed or 'type' not in parsed or 'amount' not in parsed:
        # Не транзакция — в AI-чат
        reply = await ai_chat(uid, lang, text_result, ctx)
        await upd.message.reply_text(reply, parse_mode='Markdown')
        return

    # Сохранить и показать подтверждение
    ctx.user_data['pending_voice_tx']   = parsed
    ctx.user_data['pending_voice_text'] = text_result

    rates   = await asyncio.to_thread(get_rates)
    cur     = parsed.get('currency', 'UZS')
    amt_str = fmt_amount(parsed['amount'], cur, rates)
    sign    = '+' if parsed['type'] == 'inc' else '-'
    label   = (tx('ru', 'type_inc') if parsed['type'] == 'inc' else tx('ru', 'type_exp')) if lang == 'ru' \
              else (tx('uz', 'type_inc') if parsed['type'] == 'inc' else tx('uz', 'type_exp'))

    confirm_text = (
        f"🎤 *Я поняла:*\n\n"
        f"{label}: `{sign}{amt_str}`\n"
        f"📝 {parsed.get('description', '')}\n"
        f"🏷 {parsed.get('category', '')}\n\n"
        f"*Всё верно?*"
        if lang == 'ru' else
        f"🎤 *Men tushundim:*\n\n"
        f"{label}: `{sign}{amt_str}`\n"
        f"📝 {parsed.get('description', '')}\n"
        f"🏷 {parsed.get('category', '')}\n\n"
        f"*Hammasi to'g'rimi?*"
    )
    kb = [[
        InlineKeyboardButton('✅ Верно'       if lang == 'ru' else "✅ To'g'ri",   callback_data='voice_confirm'),
        InlineKeyboardButton('✏️ Исправить'  if lang == 'ru' else '✏️ Tuzatish', callback_data='voice_fix'),
    ]]
    await upd.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')


# ────────────────────────── NOTIFICATION SCHEDULER ────────────────
async def send_daily_notifications(context: ContextTypes.DEFAULT_TYPE):
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


async def send_weekly_summary(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет еженедельный отчёт по воскресеньям в 20:00."""
    now = datetime.now(TZ)
    if now.weekday() != 6 or now.hour != 20 or now.minute != 0:
        return
    try:
        users = get_all_users_with_notify()
    except Exception as e:
        logger.error(f'Weekly summary fetch error: {e}')
        return

    for uid, name, lang, _ in users:
        try:
            stats = get_stats(uid)
            if stats['count'] == 0:
                continue
            bal = stats['m_inc'] - stats['m_exp']
            bal_str = f"+{uzs(bal)}" if bal >= 0 else f"-{uzs(abs(bal))}"

            top_cats = ''
            if stats['cats']:
                top_cats = ('\n\n📊 *Топ расходов месяца:*\n' if lang == 'ru' else '\n\n📊 *Bu oy top xarajatlar:*\n')
                for i, (cat, amt) in enumerate(stats['cats'][:3], 1):
                    top_cats += f"  {i}. {cat}: `{uzs(amt)}`\n"

            msg = (
                f"📊 *{name}, вот твой недельный итог!*\n\n"
                f"Этот месяц:\n"
                f"📈 Доходы: `{uzs(stats['m_inc'])}`\n"
                f"📉 Расходы: `{uzs(stats['m_exp'])}`\n"
                f"💰 Баланс: `{bal_str}`"
                f"{top_cats}\n"
                f"_Продолжай записывать — ты движешься к цели!_ 🚀"
                if lang == 'ru' else
                f"📊 *{name}, haftalik natijangiz!*\n\n"
                f"Bu oy:\n"
                f"📈 Daromad: `{uzs(stats['m_inc'])}`\n"
                f"📉 Xarajat: `{uzs(stats['m_exp'])}`\n"
                f"💰 Balans: `{bal_str}`"
                f"{top_cats}\n"
                f"_Yozishda davom eting — maqsadga yaqinlashyapsiz!_ 🚀"
            )

            await context.bot.send_message(uid, msg, parse_mode='Markdown')
            logger.info(f'Sent weekly summary to {uid}')
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning(f'Failed to send weekly summary to {uid}: {e}')


async def generate_morning_greeting(uid: int, lang: str) -> str:
    """Генерирует персональное утреннее пожелание через AI."""
    user  = get_user(uid)
    stats = get_stats(uid)
    name  = user.get('name', 'друг' if lang == 'ru' else "do'st")
    goal  = user.get('goal', '')
    bal   = stats['m_inc'] - stats['m_exp']
    gender = user.get('gender', 'unknown')

    if lang == 'ru':
        if gender == 'male':
            gender_line = '- Пользователь мужчина, обращайся к нему в мужском роде\n'
        elif gender == 'female':
            gender_line = '- Пользователь женщина, обращайся к ней в женском роде\n'
        else:
            gender_line = '- Пол неизвестен, избегай глаголов прошедшего времени\n'
    else:
        gender_line = ''

    fin_hint = ''
    if stats['m_exp'] > 0:
        fin_hint = f"В этом месяце потратил(а) {uzs(stats['m_exp'])}, баланс {uzs(bal)}. " if lang == 'ru' else f"Bu oy {uzs(stats['m_exp'])} sarfladi, balans {uzs(bal)}. "

    prompt = (
        f"Напиши короткое утреннее пожелание для {name}. "
        f"{'Цель: ' + goal + '. ' if goal else ''}"
        f"{fin_hint}"
        f"Требования:\n"
        f"- Ты Финора — молодая девушка, личный финансовый друг, говоришь тепло и искренне\n"
        f"- Как будто пишет живой человек, НЕ робот\n"
        f"- Короткое (2-4 предложения)\n"
        f"- Каждый раз разное настроение: иногда вдохновляющее, иногда нежное, иногда с лёгкой иронией\n"
        f"- Упомяни имя {name}\n"
        f"- Можно намекнуть на цель или финансы, но не навязчиво\n"
        f"- Используй 1-2 эмодзи максимум\n"
        f"- Язык: {'русский' if lang == 'ru' else 'узбекский'}\n"
        f"- НЕ начинай со слова 'Доброе утро' каждый раз — варьируй приветствие\n"
        f"{gender_line}"
    )
    sys = "Ты Финора — молодая девушка, личный финансовый друг пользователя. Пишешь тепло, по-человечески, как близкий человек. Никакого официоза, никаких шаблонов. Каждое сообщение уникальное."

    try:
        return await asyncio.to_thread(_chat, sys, prompt, 200)
    except Exception as e:
        logger.error(f'Morning greeting generation error: {e}')
        return f"Доброе утро, {name} ☀️" if lang == 'ru' else f"Xayrli tong, {name} ☀️"


async def send_morning_greetings(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет утренние пожелания от Финоры каждый день в 8:00."""
    now = datetime.now(TZ)
    if now.hour != 8 or now.minute != 0:
        return

    try:
        users = get_all_users_with_notify()
    except Exception as e:
        logger.error(f'Morning greeting fetch error: {e}')
        return

    for uid, name, lang, _ in users:
        try:
            greeting = await generate_morning_greeting(uid, lang)
            await context.bot.send_message(uid, greeting, parse_mode='Markdown')
            logger.info(f'Sent morning greeting to {uid}')
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning(f'Failed to send morning greeting to {uid}: {e}')


async def send_weekly_education(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет персональный образовательный совет каждую среду в 12:00."""
    now = datetime.now(TZ)
    if now.weekday() != 2 or now.hour != 12 or now.minute != 0:
        return

    try:
        users = get_all_users_with_notify()
    except Exception as e:
        logger.error(f'Education fetch error: {e}')
        return

    for uid, name, lang, _ in users:
        try:
            u      = get_user(uid)
            stats  = get_stats(uid)
            income = u.get('income_amt', 0)
            goal   = u.get('goal', '')
            gender = u.get('gender', 'unknown')
            if gender == 'male':
                gender_edu = ' Пользователь мужчина — обращайся в мужском роде.'
            elif gender == 'female':
                gender_edu = ' Пользователь женщина — обращайся в женском роде.'
            else:
                gender_edu = ''

            prompt = (
                f"Напиши один короткий (3-4 предложения) финансовый совет или факт для {name}.\n"
                f"Их ситуация: доход {uzs(income)}/мес, расходы этого месяца {uzs(stats['m_exp'])}, цель: {goal or 'не задана'}.\n\n"
                f"Требования:\n"
                f"- Привязан к их реальной ситуации, не абстрактный\n"
                f"- Конкретный инструмент для Узбекистана (депозиты, UZSE, золото и т.д.)\n"
                f"- Говоришь как умная подруга, не как учебник\n"
                f"- Язык: {'русский' if lang == 'ru' else 'узбекский'}\n"
                f"- {gender_edu}"
            )
            sys = "Ты Финора — финансовый советник. Учишь ненавязчиво, конкретно, по-дружески."

            tip = await asyncio.to_thread(_chat, sys, prompt, 250)
            await context.bot.send_message(uid, f"📚 {tip}", parse_mode='Markdown')
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning(f'Education send error for {uid}: {e}')


async def send_emotional_checkin(context: ContextTypes.DEFAULT_TYPE):
    """Раз в 14 дней спрашивает про эмоциональное состояние (1-го и 15-го в 19:00)."""
    now = datetime.now(TZ)
    if now.day not in (1, 15) or now.hour != 19 or now.minute != 0:
        return

    try:
        users = get_all_users_with_notify()
    except Exception:
        return

    for uid, name, lang, _ in users:
        try:
            msg = (
                f"Привет, {name} 💙\n\n"
                f"Как ты себя чувствуешь финансово в этом месяце?\n\n"
                f"Это важно — я хочу понимать не только цифры, но и как ты на самом деле."
                if lang == 'ru' else
                f"Salom, {name} 💙\n\n"
                f"Bu oy moliyaviy jihatdan o'zingizni qanday his qilyapsiz?\n\n"
                f"Bu muhim — men faqat raqamlarni emas, siz haqiqatan qanday ekanligingizni bilmoqchiman."
            )
            kb = [[
                InlineKeyboardButton('😊 Хорошо' if lang == 'ru' else '😊 Yaxshi',     callback_data='mood_good'),
                InlineKeyboardButton('😐 Нормально' if lang == 'ru' else '😐 Normal',  callback_data='mood_ok'),
                InlineKeyboardButton('😟 Тяжело' if lang == 'ru' else '😟 Qiyin',      callback_data='mood_bad'),
            ]]
            await context.bot.send_message(uid, msg, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning(f'Checkin send error for {uid}: {e}')


# ────────────────────────── ИЗМЕНЕНИЕ 4: Напоминание про кредиты ──
async def send_debt_reminders(context: ContextTypes.DEFAULT_TYPE):
    """Напоминать пользователям с целью 'долги' добавить кредиты если не добавили."""
    try:
        with get_conn() as conn:
            with conn.cursor() as c:
                c.execute('''
                    SELECT u.user_id, u.name, u.language
                    FROM users u
                    LEFT JOIN debts d ON d.user_id = u.user_id
                    WHERE u.onboarding_done = 1
                    AND (
                        u.goal ILIKE '%долг%' OR u.goal ILIKE '%кредит%'
                        OR u.goal ILIKE '%qarz%' OR u.goal ILIKE '%kredit%'
                    )
                    AND d.id IS NULL
                    GROUP BY u.user_id, u.name, u.language
                ''')
                users = c.fetchall()
    except Exception as e:
        logger.error(f'Debt reminder fetch error: {e}')
        return

    for uid, name, lang in users:
        try:
            u_obj = get_user(uid)
            gender = u_obj.get('gender', 'unknown')
            if gender == 'female':
                v1, v2 = 'добавила', 'должна'
            else:
                v1, v2 = 'добавил', 'должен'
            msg = (
                f"💳 *{name}*, ты ещё не {v1} свои кредиты!\n\n"
                f"Твоя цель — закрыть долги, но я не знаю сколько ты {v2}. "
                f"Без этого не смогу помочь составить план 🙏\n\n"
                f"Зайди в /debts — займёт буквально 2 минуты."
                if lang == 'ru' else
                f"💳 *{name}*, siz hali kreditlaringizni qo'shmadingiz!\n\n"
                f"Maqsadingiz qarzlarni to'lash, lekin qancha qarzingiz borligini bilmayman. "
                f"Bunsiz reja tuzib bera olmayman 🙏\n\n"
                f"/debts ga kiring — atigi 2 daqiqa vaqt oladi."
            )
            await context.bot.send_message(uid, msg, parse_mode='Markdown')
            await asyncio.sleep(0.1)
        except Exception as e:
            logger.warning(f'Debt reminder send error for {uid}: {e}')


# ────────────────────────── FLASK DASHBOARD ───────────────────────
flask_app = Flask(__name__, template_folder='templates')
flask_app.secret_key = os.getenv('FLASK_SECRET_KEY', 'finora-dashboard-secret-change-me')

def _verify_telegram_webapp(init_data: str) -> int | None:
    """Проверка tg.initData от Telegram WebApp. Возвращает user_id или None."""
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        check_hash = parsed.pop('hash', None)
        if not check_hash:
            return None

        data_check_arr = [f'{k}={v}' for k, v in sorted(parsed.items())]
        data_check_string = '\n'.join(data_check_arr)

        secret_key = hmac.new(
            BOT_TOKEN.encode(),
            b'WebAppData',
            hashlib.sha256
        ).digest()
        computed = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(computed, check_hash):
            return None

        auth_date = int(parsed.get('auth_date', 0))
        if datetime.now().timestamp() - auth_date > 300:
            return None

        user_data = json.loads(parsed.get('user', '{}'))
        return user_data.get('id')
    except Exception as e:
        logger.warning(f'WebApp auth error: {e}')
        return None

def _get_user_stats_for_dashboard(user_id: int) -> dict:
    month = datetime.now(TZ).strftime('%Y-%m')
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as c:
                c.execute('SELECT type, SUM(amount) as total FROM transactions WHERE user_id=%s GROUP BY type', (user_id,))
                totals = {row['type']: float(row['total']) for row in c.fetchall()}

                c.execute('''SELECT type, SUM(amount) as total FROM transactions
                    WHERE user_id=%s AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s
                    GROUP BY type''', (user_id, month))
                month_totals = {row['type']: float(row['total']) for row in c.fetchall()}

                c.execute('''SELECT category, SUM(amount) as total
                    FROM transactions WHERE user_id=%s AND type='exp'
                    AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s
                    GROUP BY category ORDER BY total DESC LIMIT 10''', (user_id, month))
                categories = [dict(row) for row in c.fetchall()]

                c.execute('''SELECT id, type, amount, description, category, currency, created_at
                    FROM transactions WHERE user_id=%s ORDER BY created_at DESC LIMIT 50''', (user_id,))
                transactions = [dict(row) for row in c.fetchall()]

                c.execute('SELECT * FROM users WHERE user_id=%s', (user_id,))
                row = c.fetchone()
                user_info = dict(row) if row else {}
    except Exception as e:
        logger.error(f'Dashboard stats error: {e}')
        return {}

    return {
        'total_income':   totals.get('inc', 0),
        'total_expense':  totals.get('exp', 0),
        'balance':        totals.get('inc', 0) - totals.get('exp', 0),
        'month_income':   month_totals.get('inc', 0),
        'month_expense':  month_totals.get('exp', 0),
        'month_balance':  month_totals.get('inc', 0) - month_totals.get('exp', 0),
        'categories':     categories,
        'transactions':   transactions,
        'user_info':      user_info,
    }

@flask_app.route('/')
def dashboard_index():
    return render_template('dashboard.html')

@flask_app.route('/api/stats')
def dashboard_api_stats():
    """API endpoint — валидирует tg.initData и возвращает статистику."""
    init_data = request.args.get('initData', '')
    if not init_data:
        return jsonify({'error': 'No initData'}), 401

    user_id = _verify_telegram_webapp(init_data)
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401

    stats = _get_user_stats_for_dashboard(user_id)

    for t in stats.get('transactions', []):
        if t.get('created_at'):
            try:
                t['created_at'] = t['created_at'].astimezone(TZ).strftime('%d.%m %H:%M')
            except Exception:
                t['created_at'] = str(t['created_at'])[:16]

    return jsonify(stats)

def _run_flask():
    """Запуск Flask в отдельном потоке."""
    port = int(os.getenv('PORT', 5000))
    logger.info(f'Starting Flask dashboard on port {port}')
    flask_app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)

# ────────────────────────── MAIN ──────────────────────────────────
def main():
    init_db()

    flask_thread = threading.Thread(target=_run_flask, daemon=True)
    flask_thread.start()

    async def post_init(app: Application):
        await setup_bot_ui(app)
        # Ежедневные напоминания
        app.job_queue.run_repeating(send_daily_notifications, interval=60, first=10)
        # Еженедельный отчёт (воскресенье 20:00)
        app.job_queue.run_repeating(send_weekly_summary, interval=60, first=30)
        # Утренние пожелания (каждый день в 8:00)
        app.job_queue.run_repeating(send_morning_greetings, interval=60, first=15)
        # Образовательный совет (среда 12:00)
        app.job_queue.run_repeating(send_weekly_education, interval=60, first=20)
        # Психологический чекин (1 и 15 числа в 19:00)
        app.job_queue.run_repeating(send_emotional_checkin, interval=60, first=25)
        # ИЗМЕНЕНИЕ 4: Напоминание про кредиты (раз в 3 дня)
        app.job_queue.run_repeating(send_debt_reminders, interval=259200, first=300)

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler('start',    cmd_start))
    app.add_handler(CommandHandler('stats',    cmd_stats))
    app.add_handler(CommandHandler('history',  cmd_history))
    app.add_handler(CommandHandler('advice',   cmd_advice))
    app.add_handler(CommandHandler('rate',     cmd_rate))
    app.add_handler(CommandHandler('settings', cmd_settings))
    app.add_handler(CommandHandler('bug',      cmd_bug))
    app.add_handler(CommandHandler('help',     cmd_help))
    app.add_handler(CommandHandler('debts',    cmd_debts))
    app.add_handler(CommandHandler('clear',    cmd_clear))
    app.add_handler(CommandHandler('reset',    cmd_reset))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(CallbackQueryHandler(on_callback))

    app.run_polling(drop_pending_updates=True)


if __name__ == '__main__':
    main()
