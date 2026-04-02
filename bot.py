#!/usr/bin/env python3
"""
💎 Finora — Твой личный финансовый друг
Telegram bot: учёт финансов + AI советы + умный онбординг + уведомления + Flask dashboard
"""

import os, json, logging, tempfile, base64, asyncio, threading, hashlib, hmac, math, secrets, time
from datetime import datetime
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import parse_qsl

import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, constants, MenuButtonWebApp, BotCommand, WebAppInfo
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)
from flask import Flask, render_template, request, jsonify

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
ADMIN_ID       = int(os.getenv('ADMIN_USER_ID', '1326256223'))

# ─── SECURITY CONSTANTS ───────────────────────────────────────────
# Allowed AI models for OpenRouter (must be before OR_MODEL validation)
_ALLOWED_MODELS = frozenset({
    'anthropic/claude-sonnet-4-5',
    'anthropic/claude-3-5-sonnet-20241022',
    'openai/gpt-4o',
    'google/gemini-pro-1.5',
    'meta-llama/llama-3-8b-instruct',
})

# Validate OR_MODEL — fallback to default if invalid
_OR_MODEL = os.getenv('OR_MODEL', 'anthropic/claude-sonnet-4-5')
if _OR_MODEL not in _ALLOWED_MODELS:
    logger.warning(f'OR_MODEL "{_OR_MODEL}" not in allowed list, using default')
    OR_MODEL = 'anthropic/claude-sonnet-4-5'
else:
    OR_MODEL = _OR_MODEL

client = OpenAI(
    api_key=OPENROUTER_KEY,
    base_url='https://openrouter.ai/api/v1',
    default_headers={
        'HTTP-Referer': 'https://finora.app',
        'X-Title': 'Finora Finance Bot',
    }
)

# Whitelist of allowed columns for set_user() — prevents SQL injection
_ALLOWED_USER_COLUMNS = frozenset({
    'language', 'name', 'income_freq', 'income_amt', 'income_currency',
    'side_income', 'goal', 'goal_amount', 'goal_saved',
    'notify_time', 'notify_enabled', 'onboarding_state', 'onboarding_done',
    'debt_target', 'debt_current', 'debt_temp_json',
})

# Rate limiting for AI requests (per user per minute)
_RATE_LIMIT_WINDOW = 60  # seconds
_RATE_LIMIT_MAX = 30     # max requests per window
_ai_rate_limit: dict[int, list[float]] = defaultdict(list)

# Input length limits
_MAX_LEN_NAME = 64
_MAX_LEN_GOAL = 256
_MAX_LEN_DESC = 500
_MAX_LEN_BANK = 128
_MAX_LEN_DEADLINE = 64

# TTL for pending transactions (10 minutes)
_PENDING_TX_TTL = 600  # seconds

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
STATE_GOAL_AMOUNT = 'goal_amount'
STATE_NOTIFY_WHY  = 'notify_why'
STATE_NOTIFY_TIME = 'notify_time'
STATE_DONE        = 'done'
STATE_BUG_REPORT  = 'bug_report'

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
    STATE_GOAL_AMOUNT,
    STATE_NOTIFY_TIME, STATE_DEBT_COUNT, STATE_DEBT_BANK, STATE_DEBT_AMT,
    STATE_DEBT_RATE, STATE_DEBT_MONTHLY, STATE_DEBT_DEADLINE,
    'set_name', 'set_goal', 'set_notify_time', 'debt_payment',
    'goal_add_amount',
    'goal_set_amount',
    'budget_set_amount',
}

# Карта навигации: текущий state → предыдущий state
ONBOARDING_BACK_MAP = {
    'name_confirm':  STATE_NAME,
    STATE_INCOME_FREQ: 'name_confirm',
    STATE_INCOME_AMT:  STATE_INCOME_FREQ,
    STATE_CURRENCY:    STATE_INCOME_AMT,
    STATE_SIDE_HUSTLE: STATE_CURRENCY,
    STATE_SIDE_AMT:    STATE_SIDE_HUSTLE,
    STATE_GOAL_CUSTOM: STATE_GOAL,
    STATE_GOAL_AMOUNT: STATE_GOAL_CUSTOM,
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
        'ask_goal_amount'  : '💰 На сколько хочешь накопить? Напиши сумму (или пропусти):',
        'goal_amount_skip' : '⏭ Пропустить',
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
                              '/goal — 🎯 Цель и прогресс\n'
                              '/budget — 💰 Бюджеты\n'
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
                              '/goal — 🎯 Цель и прогресс\n'
                              '/budget — 💰 Бюджеты\n'
                              '/settings — ⚙️ Настройки\n'
                              '/clear — 🗑 Очистить данные'),
        'settings_hdr'     : '⚙️ *Настройки*\n\nЧто хочешь изменить?',
        'set_notify'       : '🔔 Время уведомлений',
        'set_goal'         : '🎯 Финансовая цель',
        'set_name'         : '👤 Своё имя',
        'cancel_notify'    : '🔕 Отключить уведомления',
        'notify_disabled'  : '🔕 Уведомления отключены.',
        'confirm_hdr'      : '📝 Проверь правильно ли я понял:',
        'confirm_correct'  : '✅ Верно',
        'confirm_edit'     : '✏️ Исправить',
        'confirm_cancel'   : '❌ Отмена',
        'goal_hdr'         : '🎯 *Твоя цель*',
        'goal_add'         : '➕ Пополнить копилку',
        'goal_edit'        : '✏️ Изменить цель',
        'goal_edit_amount' : '✏️ Изменить сумму',
        'goal_add_amount'  : '💰 Сколько добавить?',
        'goal_added'       : '✅ Добавлено к цели!',
        'goal_progress'   : '📊 Прогресс: {bar} {pct}%\n💰 Накоплено: {saved} из {total}\n📅 При текущем темпе: ~{months} мес.',
        'goal_no_amount'   : '💰 Сумма не установлена. Напиши сумму:',
        'goal_amount_set'  : '✅ Сумма цели установлена: {amount}',
        'budget_hdr'       : '💰 *Бюджеты*',
        'budget_add'       : '➕ Установить лимит',
        'budget_delete'    : '🗑 Удалить лимит',
        'budget_no'        : '📭 Бюджеты не установлены',
        'budget_item'      : '📊 {cat}: {spent}/{budget} ({pct}%)',
        'budget_80'        : '⚠️ Лимит на {cat} использован на {pct}%',
        'budget_100'       : '🚨 Лимит на {cat} превышен!',
        'budget_choose_cat': '🏷 Выбери категорию:',
        'budget_enter_amt' : '💰 Напиши сумму лимита:',
        'budget_set'       : '✅ Лимит {cat}: {amount}',
        'budget_deleted'   : '🗑 Лимит удалён',
        'advice_40'        : '\n\n⚠️ *Замечаю тенденцию:* на {cat} в этом месяце уже {amount} — это {pct:.0f}% от дохода. Возможно стоит пересмотреть?',
        'advice_60'        : '\n\n🚨 *Внимание!* {cat} съедает {pct:.0f}% твоего дохода в этом месяце! Это {amount}. Хочешь разберём как сократить? 👉 /advice',
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
        'ask_goal_amount'  : '💰 Qancha to\'plashni xohlaysiz? Summani yozing (yoki o\'tkazib yuboring):',
        'goal_amount_skip' : '⏭ O\'tkazib yuborish',
        'no_goal_speech'   : ('Hmm, *{name}*, sizni tushunaman 😊\n\n'
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
                              '/goal — 🎯 Maqsad va progress\n'
                              '/budget — 💰 Byudjetlar\n'
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
        'parse_error'      : '🤔 Tushunmadim. Batafsilroq yozing, masalan: *Non sotib oldim 3 000 so\'m*',
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
                              '• Yozing: _"Non sotib oldim 3 000"\n'
                              '• 🎤 Ovoz bilan\n'
                              '• 📷 Chek rasmi\n\n'
                              '*Tuzatish:* _"tuzat"_ yoki _"bekor qil"_ yozing\n\n'
                              '*Buyruqlar:*\n'
                              '/stats — 📊 Statistika\n'
                              '/history — 📋 Tarix\n'
                              '/advice — 🤖 AI maslahat\n'
                              '/rate — 💱 Valyuta kursi\n'
                              '/goal — 🎯 Maqsad va progress\n'
                              '/budget — 💰 Byudjetlar\n'
                              '/settings — ⚙️ Sozlamalar\n'
                              '/clear — 🗑 Ma\'lumotlarni o\'chirish'),
        'settings_hdr'     : '⚙️ *Sozlamalar*\n\nNimani o\'zgartiroqsiz?',
        'set_notify'       : '🔔 Eslatma vaqti',
        'set_goal'         : '🎯 Moliyaviy maqsad',
        'set_name'         : '👤 Ismingiz',
        'cancel_notify'    : '🔕 Eslatmalarni o\'chirish',
        'notify_disabled'  : '🔕 Eslatmalar o\'chirildi.',
        'confirm_hdr'      : '📝 Tekshir — to\'g\'ri tushundimmi:',
        'confirm_correct'  : '✅ To\'g\'ri',
        'confirm_edit'     : '✏️ Tuzatish',
        'confirm_cancel'   : '❌ Bekor qilish',
        'goal_hdr'         : '🎯 *Sening maqsading*',
        'goal_add'         : '➕ Koptokka qo\'shish',
        'goal_edit'        : '✏️ Maqsadni o\'zgartirish',
        'goal_edit_amount' : '✏️ Summani o\'zgartirish',
        'goal_add_amount'  : '💰 Qancha qo\'shish kerak?',
        'goal_added'       : '✅ Maqsadga qo\'shildi!',
        'goal_progress'   : '📊 Progress: {bar} {pct}%\n💰 Jamg\'arilgan: {saved} / {total}\n📅 Hozirgi tezlikda: ~{months} oy.',
        'goal_no_amount'   : '💰 Summa o\'rnatilmagan. Summani yozing:',
        'goal_amount_set'  : '✅ Maqsad summasi: {amount}',
        'budget_hdr'       : '💰 *Byudjetlar*',
        'budget_add'       : '➕ Limit o\'rnatish',
        'budget_delete'    : '🗑 Limitni o\'chirish',
        'budget_no'        : '📭 Byudjetlar o\'rnatilmagan',
        'budget_item'      : '📊 {cat}: {spent}/{budget} ({pct}%)',
        'budget_80'        : '⚠️ {cat} limiti {pct}% ishlatildi',
        'budget_100'       : '🚨 {cat} limiti oshdi!',
        'budget_choose_cat': '🏷 Kategoriyani tanlang:',
        'budget_enter_amt' : '💰 Limit summasini yozing:',
        'budget_set'       : '✅ {cat} limiti: {amount}',
        'budget_deleted'   : '🗑 Limit o\'chirildi',
        'advice_40'        : '\n\n⚠️ *Tendencia:* bu oy {cat} uchun {amount} — bu daromadning {pct:.0f}% ini tashkil qiladi. Qayta ko\'rib chiqishga arzigoy?',
        'advice_60'        : '\n\n🚨 *E\'tibor!* {cat} bu oy daromadingizning {pct:.0f}% ini yeb qo\'ydi! Bu {amount}. Qisqartirishni ko\'rib chiqamizmi? 👉 /advice',
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
                goal_amount FLOAT DEFAULT 0,
                goal_saved FLOAT DEFAULT 0,
                notify_time TEXT DEFAULT '21:00',
                notify_enabled INTEGER DEFAULT 1,
                onboarding_state TEXT DEFAULT 'lang',
                onboarding_done INTEGER DEFAULT 0,
                debt_target INTEGER DEFAULT 0,
                debt_current INTEGER DEFAULT 0,
                debt_temp_json TEXT DEFAULT '{}'
            )''')
            for col, definition in [
                ('goal_amount',   'FLOAT DEFAULT 0'),
                ('goal_saved',   'FLOAT DEFAULT 0'),
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
            c.execute('''CREATE TABLE IF NOT EXISTS budgets(
                id SERIAL PRIMARY KEY,
                user_id BIGINT,
                category TEXT,
                amount FLOAT,
                created_at TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(user_id, category)
            )''')
        conn.commit()

def get_user(uid: int) -> dict:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute('INSERT INTO users(user_id) VALUES(%s) ON CONFLICT DO NOTHING', (uid,))
            conn.commit()
            c.execute('SELECT * FROM users WHERE user_id=%s', (uid,))
            row = c.fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()

def set_user(uid: int, **kwargs):
    if not kwargs: return
    filtered = {k: v for k, v in kwargs.items() if k in _ALLOWED_USER_COLUMNS}
    if not filtered:
        return
    fields = ', '.join(f'{k}=%s' for k in filtered)
    vals   = list(filtered.values()) + [uid]
    conn = get_conn()
    try:
        with conn.cursor() as c:
            c.execute(f'UPDATE users SET {fields} WHERE user_id=%s', vals)
        conn.commit()
    finally:
        conn.close()

def get_lang(uid: int) -> str:
    return get_user(uid).get('language', 'ru')

def get_state(uid: int) -> str:
    return get_user(uid).get('onboarding_state', STATE_LANG)

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

def full_reset_user(uid: int):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('DELETE FROM transactions WHERE user_id=%s', (uid,))
            c.execute('DELETE FROM debts WHERE user_id=%s', (uid,))
            c.execute('DELETE FROM budgets WHERE user_id=%s', (uid,))
            c.execute('''UPDATE users SET
                onboarding_state='lang', onboarding_done=0,
                name='', income_freq='', income_amt=0, income_currency='UZS',
                side_income=0, goal='', goal_amount=0, goal_saved=0,
                notify_time='21:00', notify_enabled=1,
                debt_target=0, debt_current=0, debt_temp_json='{}'
                WHERE user_id=%s''', (uid,))
        conn.commit()

def reset_onboarding_only(uid: int):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('''UPDATE users SET
                onboarding_state='lang', onboarding_done=0,
                name='', income_freq='', income_amt=0, income_currency='UZS',
                side_income=0, goal='', goal_amount=0, goal_saved=0,
                notify_time='21:00', notify_enabled=1,
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
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as c:
            c.execute(
                'SELECT user_id, name, language, onboarding_done FROM users ORDER BY user_id DESC LIMIT %s',
                (limit,)
            )
            return [dict(r) for r in c.fetchall()]

def get_budgets(uid: int) -> dict:
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('SELECT category, amount FROM budgets WHERE user_id=%s', (uid,))
            return {row[0]: row[1] for row in c.fetchall()}

def set_budget(uid: int, category: str, amount: float):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('''INSERT INTO budgets(user_id, category, amount) VALUES(%s,%s,%s)
                         ON CONFLICT (user_id, category) DO UPDATE SET amount=%s''',
                      (uid, category, amount, amount))
        conn.commit()

def delete_budget(uid: int, category: str):
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute('DELETE FROM budgets WHERE user_id=%s AND category=%s', (uid, category))
        conn.commit()

def get_month_expenses_by_category(uid: int) -> dict:
    month = datetime.now(TZ).strftime('%Y-%m')
    with get_conn() as conn:
        with conn.cursor() as c:
            c.execute(
                """SELECT category, SUM(amount) FROM transactions
                   WHERE user_id=%s AND type='exp'
                   AND TO_CHAR(created_at AT TIME ZONE 'Asia/Tashkent', 'YYYY-MM')=%s
                   GROUP BY category""",
                (uid, month)
            )
            return {row[0]: float(row[1]) for row in c.fetchall()}

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
If text contains correction keywords (исправь/тузат/ошибся/неправильно/нет/не то/не так/имею в виду/хотел сказать/точнее/вернее/имею ввиду/это не/поправка/yo'q/emas/ya'ni/to'g'rilik) return:
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

If unclear or no name found, return: {"name": null}"""

async def ai_extract_name(text: str) -> str | None:
    try:
        raw = await asyncio.to_thread(_chat, _NAME_EXTRACT_SYS, text, 100)
        raw = raw.replace('```json', '').replace('```', '').strip()
        data = json.loads(raw)
        return data.get('name')
    except Exception as e:
        logger.warning(f'Name extraction failed: {e}')
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

# ────────────────────────── FORMATTERS ────────────────────────────
def fmt_tx_msg(parsed: dict, lang: str, rates: dict) -> str:
    cur   = parsed.get('currency', 'UZS')
    amt   = parsed['amount']
    sign  = '+' if parsed['type'] == 'inc' else '-'
    label = tx(lang, 'type_exp') if parsed['type'] == 'exp' else tx(lang, 'type_inc')

    amt_str = fmt_amount(amt, cur, rates)
    text = (f"✅ {'Yozib oldim' if lang == 'uz' else 'Записала'}!\n\n"
            f"{label}: `{sign}{amt_str}`\n"
            f"📝 {parsed.get('description', '')}\n"
            f"🏷 {parsed.get('category', '')}")

    items = parsed.get('items', [])
    if items:
        text += '\n\n📄 ' + '\n'.join(f"• {i}" for i in items[:8])
    return text

def fmt_confirm_card(parsed: dict, lang: str, rates: dict) -> str:
    cur   = parsed.get('currency', 'UZS')
    amt   = parsed['amount']
    label = tx(lang, 'type_exp') if parsed['type'] == 'exp' else tx(lang, 'type_inc')
    amt_str = fmt_amount(amt, cur, rates)
    
    sign = '-' if parsed['type'] == 'exp' else '+'
    return (f"📝 {tx(lang, 'confirm_hdr')}\n\n"
            f"{label}: {sign}{amt_str}\n"
            f"📝 {parsed.get('description', '')}\n"
            f"🏷 {parsed.get('category', '')}")

def build_progress_bar(pct: float) -> str:
    filled = min(int(pct / 10), 10)
    empty = 10 - filled
    return '█' * filled + '░' * empty

def calc_payoff_months(amount: float, rate: float, monthly: float) -> int | None:
    if monthly <= 0:
        return None
    monthly_rate = rate / 100 / 12
    if monthly_rate == 0:
        return int(amount / monthly) if amount > 0 else None
    interest = amount * monthly_rate
    if monthly <= interest:
        return None
    n = -math.log(1 - (monthly_rate * amount / monthly)) / math.log(1 + monthly_rate)
    return math.ceil(n)

async def check_proactive_advice(uid: int, lang: str, category: str, amount: float) -> str:
    user = get_user(uid)
    income = user.get('income_amt', 0) + user.get('side_income', 0)
    
    if income <= 0:
        return ''
    
    cat_expenses = get_month_expenses_by_category(uid)
    cat_total = cat_expenses.get(category, 0)
    
    pct = (cat_total / income) * 100 if income > 0 else 0
    
    if pct >= 60:
        return tx(lang, 'advice_60', cat=category, amount=uzs(cat_total), pct=pct)
    elif pct >= 40:
        return tx(lang, 'advice_40', cat=category, amount=uzs(cat_total), pct=pct)
    
    return ''

async def check_budget_warning(uid: int, lang: str, category: str) -> str:
    budgets = get_budgets(uid)
    if category not in budgets:
        return ''
    
    budget = budgets[category]
    expenses = get_month_expenses_by_category(uid)
    spent = expenses.get(category, 0)
    
    if budget <= 0:
        return ''
    
    pct = (spent / budget) * 100
    
    if pct >= 100:
        return tx(lang, 'budget_100', cat=category, spent=uzs(spent), budget=uzs(budget))
    elif pct >= 80:
        return tx(lang, 'budget_80', cat=category, pct=int(pct))
    
    return ''

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

    elif state == STATE_GOAL_AMOUNT:
        kb = [[
            InlineKeyboardButton(tx(lang, 'goal_amount_skip'), callback_data='goal_amount_skip'),
        ], [back_btn]]
        await context.bot.send_message(
            chat_id, tx(lang, 'ask_goal_amount'),
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
        await context.bot.send_message(
            chat_id,
            '💵 Сколько ты должен? Напиши сумму долга:' if lang == 'ru'
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

    confirm = (
        f"✅ *Спасибо за репорт!*\n\n"
        f"Твоё сообщение отправлено разработчику.\n"
        f"Мы исправим это как можно быстрее! 🚀\n\n"
        f"ID репорта: *#{report_id}*\n\n"
        f"💬 Обычно отвечаем в течение 24 часов."
        if lang == 'ru' else
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
        BotCommand('goal', '🎯 Цель и прогресс'),
        BotCommand('budget', '💰 Бюджеты'),
        BotCommand('bug', '🐛 Сообщить об ошибке'),
        BotCommand('help', '❓ Помощь'),
        BotCommand('debts', '💳 Управление кредитами'),
        BotCommand('clear', '🗑 Очистить данные')
    ]
    await app.bot.set_my_commands(commands)

    # WebApp кнопка в меню
    webapp_url = os.getenv('WEBAPP_URL', '')
    if webapp_url:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text='📊 Дашборд',
                web_app=WebAppInfo(url=webapp_url)
            )
        )

# ─── CALLBACKS ───────────────────────────────────────────────────
async def on_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = upd.callback_query
    data = q.data
    uid = upd.effective_user.id
    chat_id = upd.effective_chat.id
    lang = get_lang(uid)

    # FIX: Check pending_tx TTL before using it
    if not _check_pending_tx_ttl(ctx.user_data):
        await q.answer('⏰ Данные устарели. Отправьте транзакцию заново.', show_alert=True)
        return

    # PENDING TX CALLBACKS
    if data == 'tx_confirm':
        pending = ctx.user_data.get('pending_tx')
        if pending:
            cur = pending.get('currency', 'UZS')
            items_lst = pending.get('items', [])
            items_str = json.dumps(items_lst, ensure_ascii=False) if items_lst else ''
            add_tx(uid, pending['type'], pending['amount'],
                   pending.get('description', ''), pending.get('category', '❓ Другое'),
                   cur, items_str)
            
            ctx.user_data.pop('pending_tx', None)
            
            rates = await asyncio.to_thread(get_rates)
            reply = fmt_tx_msg(pending, lang, rates) + maybe_motivate(lang)
            
            if pending['type'] == 'exp':
                advice = await check_proactive_advice(uid, lang, pending.get('category', ''), pending['amount'])
                if advice:
                    reply += advice
                budget_warning = await check_budget_warning(uid, lang, pending.get('category', ''))
                if budget_warning:
                    reply += '\n' + budget_warning
            
            await q.answer()
            try: await q.message.edit_text(reply, parse_mode='Markdown', reply_markup=None)
            except: await q.message.reply_text(reply, parse_mode='Markdown')
        else:
            await q.answer('❌ Данные устарели')
        return

    if data == 'tx_edit':
        pending = ctx.user_data.get('pending_tx')
        if pending:
            ctx.user_data['pending_tx_edit'] = True
            await q.answer()
            try: await q.message.delete()
            except: pass
            await upd.effective_message.reply_text(
                tx(lang, 'fix_prompt'), parse_mode='Markdown'
            )
        else:
            await q.answer('❌ Данные устарели')
        return

    if data == 'tx_cancel':
        ctx.user_data.pop('pending_tx', None)
        ctx.user_data.pop('pending_tx_edit', None)
        await q.answer()
        try: await q.message.delete()
        except: pass
        return

    if data == 'goal_amount_skip':
        set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
        return

    if data == 'goal_add':
        set_user(uid, onboarding_state='goal_add_amount')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id, tx(lang, 'goal_add_amount'), parse_mode='Markdown'
        )
        return

    if data == 'goal_edit':
        set_user(uid, onboarding_state='set_goal')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id,
            '🎯 Напиши новую финансовую цель:' if lang == 'ru'
            else '🎯 Yangi moliyaviy maqsadingizni yozing:',
            parse_mode='Markdown'
        )
        return

    if data == 'goal_edit_amount':
        set_user(uid, onboarding_state='goal_set_amount')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id, tx(lang, 'goal_no_amount'), parse_mode='Markdown'
        )
        return

    if data == 'budget_add':
        categories = ['🍔 Еда', '🚗 Транспорт', '🏠 Жильё', '💊 Здоровье', '👗 Одежда',
                      '🎮 Развлечения', '📱 Связь', '🛒 Магазин', '💡 Коммуналка',
                      '📚 Образование', '⛽ Бензин', '💼 Бизнес', '🎁 Подарок', '❓ Другое']
        kb = [[InlineKeyboardButton(cat, callback_data=f'budget_cat_{cat}')] for cat in categories]
        kb.append([InlineKeyboardButton('← Назад' if lang == 'ru' else '← Orqaga', callback_data='cmd_budget')])
        await q.answer()
        try: await q.message.edit_text(
            tx(lang, 'budget_choose_cat'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        except: pass
        return

    if data.startswith('budget_cat_'):
        category = data[11:]
        ctx.user_data['budget_category'] = category
        set_user(uid, onboarding_state='budget_set_amount')
        await q.answer()
        try: await q.message.delete()
        except: pass
        await ctx.bot.send_message(
            chat_id, tx(lang, 'budget_enter_amt'), parse_mode='Markdown'
        )
        return

    if data == 'budget_delete':
        budgets = get_budgets(uid)
        if not budgets:
            await q.answer('📭 Нет бюджетов')
            return
        kb = [[InlineKeyboardButton(cat, callback_data=f'budget_del_{cat}')] for cat in budgets.keys()]
        kb.append([InlineKeyboardButton('← Назад' if lang == 'ru' else '← Orqaga', callback_data='cmd_budget')])
        await q.answer()
        try: await q.message.edit_text(
            tx(lang, 'budget_delete'),
            reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown'
        )
        except: pass
        return

    if data.startswith('budget_del_'):
        category = data[11:]
        delete_budget(uid, category)
        await q.answer(tx(lang, 'budget_deleted'))
        try: await q.message.edit_text(tx(lang, 'budget_deleted'), parse_mode='Markdown')
        except: pass
        return

    if data == 'cmd_budget':
        await q.answer()
        try: await q.message.delete()
        except: pass
        await cmd_budget(upd, ctx)
        return

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
                c.execute('SELECT bank, amount FROM debts WHERE id=%s AND user_id=%s', (debt_id, uid))
                result = c.fetchone()
                if result:
                    bank, amount = result
                    c.execute('DELETE FROM debts WHERE id=%s AND user_id=%s', (debt_id, uid))
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

    if data in ('lang_ru', 'lang_uz'):
        chosen = 'ru' if data == 'lang_ru' else 'uz'
        set_user(uid, language=chosen, onboarding_state=STATE_NAME)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_NAME, ctx)
        return

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

    if data.startswith('cur_'):
        set_user(uid, income_currency=data[4:], onboarding_state=STATE_SIDE_HUSTLE)
        await q.answer()
        try: await q.message.delete()
        except: pass
        await send_onboarding_step(chat_id, uid, STATE_SIDE_HUSTLE, ctx)
        return

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
                '💳 Сколько у тебя кредитов?\n\nНапиши число (от 1 до 10):' if lang == 'ru'
                else "💳 Nechta kreditingiz bor?\n\nRaqam yozing (1 dan 10 gacha):",
                parse_mode='Markdown'
            )
        else:
            set_user(uid, onboarding_state=STATE_GOAL_AMOUNT)
            await send_onboarding_step(chat_id, uid, STATE_GOAL_AMOUNT, ctx)
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

    if data == 'confirm_clear':
        clear_data(uid)
        await q.answer()
        await q.edit_message_text(tx(lang, 'cleared'), parse_mode='Markdown')
        return

    if data == 'cancel_clear':
        await q.answer()
        await q.edit_message_text(tx(lang, 'cancelled'), parse_mode='Markdown')
        return

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

# ────────────────────────── HELPER: Rate Limiting ──────────────────
def _check_rate_limit(uid: int) -> bool:
    """Check if user exceeded rate limit. Returns True if allowed, False if blocked."""
    now = time.time()
    window = _ai_rate_limit[uid]
    # Remove old entries outside the window
    window[:] = [t for t in window if now - t < _RATE_LIMIT_WINDOW]
    if len(window) >= _RATE_LIMIT_MAX:
        return False
    window.append(now)
    return True

# ────────────────────────── HELPER: TTL Check for Pending TX ──────
def _check_pending_tx_ttl(user_data: dict) -> bool:
    """Check if pending_tx is still valid (not expired). Returns True if valid."""
    pending = user_data.get('pending_tx')
    if not pending:
        return True
    created_at = pending.get('_created_at', 0)
    if time.time() - created_at > _PENDING_TX_TTL:
        user_data.pop('pending_tx', None)
        user_data.pop('pending_tx_edit', None)
        return False
    return True

# ────────────────────────── HELPER: Validate Input Length ──────────
def _truncate(text: str, max_len: int) -> str:
    """Truncate text to max length."""
    return text[:max_len] if len(text) > max_len else text

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

    if ctx.user_data.get('pending_tx_edit') and ctx.user_data.get('pending_tx'):
        ctx.user_data['pending_tx_edit'] = False
        parsed_correction = await ai_parse(text)
        if parsed_correction and 'type' in parsed_correction:
            pending = ctx.user_data['pending_tx']
            if 'amount' in parsed_correction and parsed_correction['amount']:
                pending['amount'] = parsed_correction['amount']
            if 'description' in parsed_correction and parsed_correction['description']:
                pending['description'] = parsed_correction['description']
            if 'category' in parsed_correction and parsed_correction['category']:
                pending['category'] = parsed_correction['category']
            ctx.user_data['pending_tx'] = pending
            rates = await asyncio.to_thread(get_rates)
            card_text = fmt_confirm_card(pending, lang, rates)
            kb = [[
                InlineKeyboardButton(tx(lang, 'confirm_correct'), callback_data='tx_confirm'),
                InlineKeyboardButton(tx(lang, 'confirm_edit'), callback_data='tx_edit'),
                InlineKeyboardButton(tx(lang, 'confirm_cancel'), callback_data='tx_cancel'),
            ]]
            await upd.message.reply_text(card_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return

    if state == STATE_DEBT_COUNT:
        try:
            count = int(text.strip())
            if count <= 0: raise ValueError('zero')
            if count > 10:
                await upd.message.reply_text(
                    '❌ Максимум 10 кредитов. Напиши число от 1 до 10:' if lang == 'ru'
                    else '❌ Maksimal 10 kredit. 1 dan 10 gacha raqam yozing:',
                    parse_mode='Markdown'
                )
                return
            set_user(uid, onboarding_state=STATE_DEBT_BANK)
            set_debt_state(uid, target=count, current=0, temp={})
            await upd.message.reply_text('✅ Отлично! Начинаю собирать данные...' if lang == 'ru' else "✅ Zo'r! Ma'lumotlarni to'playapman...")
            await send_onboarding_step(chat_id, uid, STATE_DEBT_BANK, ctx)
        except ValueError:
            await upd.message.reply_text(
                '❌ Напиши число от 1 до 10, например: *3*' if lang == 'ru' else "❌ 1 dan 10 gacha raqam yozing, masalan: *3*",
                parse_mode='Markdown'
            )
        return

    elif state == STATE_DEBT_BANK:
        ds = get_debt_state(uid)
        temp = ds['temp']
        temp['bank'] = _truncate(text, _MAX_LEN_BANK)
        set_debt_state(uid, temp=temp)
        set_user(uid, onboarding_state=STATE_DEBT_AMT)
        await send_onboarding_step(chat_id, uid, STATE_DEBT_AMT, ctx)
        return

    elif state == STATE_DEBT_AMT:
        try:
            amt = float(text.replace(' ', '').replace(',', '.'))
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
        temp['deadline'] = _truncate(text, _MAX_LEN_DEADLINE)
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
                    c.execute('SELECT amount, bank FROM debts WHERE id=%s AND user_id=%s', (debt_id, uid))
                    row = c.fetchone()
                    if row:
                        old_amt, bank = row
                        new_amt = old_amt - payment
                        if new_amt <= 0:
                            c.execute('DELETE FROM debts WHERE id=%s AND user_id=%s', (debt_id, uid))
                            reply = (f"🎉🎉🎉 *ПОЗДРАВЛЯЮ!!!*\n\nТы полностью закрыл кредит *{bank}*!\n\nЭто огромный шаг к финансовой свободе! 💪\n\n_/debts для оставшихся кредитов_"
                                     if lang == 'ru' else
                                     f"🎉🎉🎉 *TABRIKLAYMAN!!!*\n\n*{bank}* kreditini to'liq yopdingiz!\n\nBu moliyaviy erkinlikka katta qadam! 💪\n\n_/debts — qolgan kreditlar_")
                        else:
                            c.execute('UPDATE debts SET amount=%s WHERE id=%s AND user_id=%s', (new_amt, debt_id, uid))
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

    elif state == STATE_NAME:
        name_val = await ai_extract_name(text)
        if not name_val:
            name_val = text.strip().split()[0].capitalize() if text.strip() else 'Друг'
        name_val = _truncate(name_val, _MAX_LEN_NAME)
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
        goal_val = _truncate(text.strip(), _MAX_LEN_GOAL)
        set_user(uid, goal=goal_val, onboarding_state=STATE_GOAL_AMOUNT)
        await send_onboarding_step(chat_id, uid, STATE_GOAL_AMOUNT, ctx)
        return

    elif state == STATE_GOAL_AMOUNT:
        try:
            amount = float(text.replace(' ', '').replace(',', '.'))
            set_user(uid, goal_amount=amount, onboarding_state=STATE_NOTIFY_WHY)
            await upd.message.reply_text(
                tx(lang, 'goal_amount_set', amount=uzs(amount)), parse_mode='Markdown'
            )
            await send_onboarding_step(chat_id, uid, STATE_NOTIFY_WHY, ctx)
        except:
            set_user(uid, onboarding_state=STATE_NOTIFY_WHY)
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
        goal_val = _truncate(text.strip(), _MAX_LEN_GOAL)
        set_user(uid, goal=goal_val, onboarding_state=STATE_DONE)
        await upd.message.reply_text(
            f"✅ Цель обновлена: _{goal_val}_" if lang == 'ru' else f"✅ Maqsad yangilandi: _{goal_val}_",
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

    elif state == 'goal_add_amount':
        try:
            amount = float(text.replace(' ', '').replace(',', '.'))
            u = get_user(uid)
            current_saved = u.get('goal_saved', 0)
            set_user(uid, goal_saved=current_saved + amount, onboarding_state=STATE_DONE)
            await upd.message.reply_text(tx(lang, 'goal_added'), parse_mode='Markdown')
            await cmd_goal(upd, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    elif state == 'goal_set_amount':
        try:
            amount = float(text.replace(' ', '').replace(',', '.'))
            set_user(uid, goal_amount=amount, onboarding_state=STATE_DONE)
            await upd.message.reply_text(
                tx(lang, 'goal_amount_set', amount=uzs(amount)), parse_mode='Markdown'
            )
            await cmd_goal(upd, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    elif state == 'budget_set_amount':
        category = ctx.user_data.pop('budget_category', None)
        if not category:
            await upd.message.reply_text('❌ Ошибка. Попробуй ещё раз.' if lang == 'ru' else "❌ Xatolik. Qayta urinib ko'ring")
            return
        try:
            amount = float(text.replace(' ', '').replace(',', '.'))
            set_budget(uid, category, amount)
            set_user(uid, onboarding_state=STATE_DONE)
            await upd.message.reply_text(
                tx(lang, 'budget_set', cat=category, amount=uzs(amount)), parse_mode='Markdown'
            )
            await cmd_budget(upd, ctx)
        except:
            await upd.message.reply_text('❌ Напиши число' if lang == 'ru' else '❌ Raqam yozing', parse_mode='Markdown')
        return

    else:
        u3 = get_user(uid)
        if not u3.get('onboarding_done'):
            await upd.message.reply_text(
                '❓ Напиши /start чтобы начать' if lang == 'ru' else '❓ /start yozing'
            )
            return

        # FIX: Rate limit check before AI call
        if not _check_rate_limit(uid):
            await upd.message.reply_text(
                '⏳ Слишком много запросов. Подожди минуту.' if lang == 'ru'
                else "⏳ Ko'p so'rovlar. Bir daqiqa kuting.",
                parse_mode='Markdown'
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

        rates = await asyncio.to_thread(get_rates)
        card_text = fmt_confirm_card(parsed, lang, rates)
        kb = [[
            InlineKeyboardButton(tx(lang, 'confirm_correct'), callback_data='tx_confirm'),
            InlineKeyboardButton(tx(lang, 'confirm_edit'), callback_data='tx_edit'),
            InlineKeyboardButton(tx(lang, 'confirm_cancel'), callback_data='tx_cancel'),
        ]]
        
        ctx.user_data['pending_tx'] = parsed
        
        await upd.message.reply_text(card_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

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

    if u.get('goal') and u.get('goal_amount', 0) > 0:
        goal_amount = u.get('goal_amount', 0)
        goal_saved = u.get('goal_saved', 0)
        pct = min((goal_saved / goal_amount) * 100, 100) if goal_amount > 0 else 0
        bar = build_progress_bar(pct)
        
        avg_monthly = s['m_inc'] - s['m_exp']
        if avg_monthly > 0:
            remaining = goal_amount - goal_saved
            months = int(remaining / avg_monthly) if remaining > 0 else 0
        else:
            months = None
        
        msg += (f"\n\n🎯 *{u.get('goal')}*\n"
                f"{tx(lang, 'goal_progress', bar=bar, pct=int(pct), saved=uzs(goal_saved), total=uzs(goal_amount), months=months if months else '?')}")

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

async def cmd_goal(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    u    = get_user(uid)
    
    goal = u.get('goal', '')
    goal_amount = u.get('goal_amount', 0)
    goal_saved = u.get('goal_saved', 0)
    s = get_stats(uid)
    
    if not goal and goal_amount == 0:
        body = tx(lang, 'goal_hdr') + '\n\n📭 ' + ('Цель не установлена. Используй /settings' if lang == 'ru' else 'Maqsad o\'rnatilmagan. /settings dan foydalaning')
    else:
        lines = [tx(lang, 'goal_hdr')]
        if goal:
            lines.append(f"\n🎯 *{goal}*")
        
        if goal_amount > 0:
            pct = min((goal_saved / goal_amount) * 100, 100) if goal_amount > 0 else 0
            bar = build_progress_bar(pct)
            
            avg_monthly = s['m_inc'] - s['m_exp']
            if avg_monthly > 0:
                remaining = goal_amount - goal_saved
                months = int(remaining / avg_monthly) if remaining > 0 else 0
            else:
                months = None
            
            lines.append(tx(lang, 'goal_progress', bar=bar, pct=int(pct), saved=uzs(goal_saved), total=uzs(goal_amount), months=months if months else '?'))
        else:
            lines.append(f"\n💰 " + ('Накоплено: ' if lang == 'ru' else 'Jamg\'arilgan: ') + uzs(goal_saved))
        
        body = '\n'.join(lines)
    
    kb = [
        [InlineKeyboardButton(tx(lang, 'goal_add'), callback_data='goal_add')],
        [InlineKeyboardButton(tx(lang, 'goal_edit'), callback_data='goal_edit')],
        [InlineKeyboardButton(tx(lang, 'goal_edit_amount'), callback_data='goal_edit_amount')],
    ]
    await upd.message.reply_text(body, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def cmd_budget(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    
    budgets = get_budgets(uid)
    expenses = get_month_expenses_by_category(uid)
    
    if not budgets:
        body = tx(lang, 'budget_hdr') + '\n\n' + tx(lang, 'budget_no')
    else:
        lines = [tx(lang, 'budget_hdr') + '\n']
        for cat, budget_amt in budgets.items():
            spent = expenses.get(cat, 0)
            pct = int((spent / budget_amt) * 100) if budget_amt > 0 else 0
            lines.append(tx(lang, 'budget_item', cat=cat, spent=uzs(spent), budget=uzs(budget_amt), pct=pct))
            if pct >= 100:
                lines.append(tx(lang, 'budget_100', cat=cat, spent=uzs(spent), budget=uzs(budget_amt)))
            elif pct >= 80:
                lines.append(tx(lang, 'budget_80', cat=cat, pct=pct))
        body = '\n'.join(lines)
    
    kb = [
        [InlineKeyboardButton(tx(lang, 'budget_add'), callback_data='budget_add')],
        [InlineKeyboardButton(tx(lang, 'budget_delete'), callback_data='budget_delete')],
    ]
    await upd.message.reply_text(body, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

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
                f"  {'Срок' if lang == 'ru' else 'Muddat'}: {deadline}"
            )
            payoff_months = calc_payoff_months(amount, rate, monthly)
            if payoff_months is not None:
                payoff_date = datetime.now(TZ).replace(day=1)
                total_months = payoff_date.month - 1 + payoff_months
                payoff_date = payoff_date.replace(
                    year=payoff_date.year + total_months // 12,
                    month=total_months % 12 + 1
                )
                month_names_ru = ['', 'Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь']
                month_names_uz = ['', 'Yanvar', 'Fevral', 'Mart', 'Aprel', 'May', 'Iyun', 'Iyul', 'Avgust', 'Sentabr', 'Oktabr', 'Noyabr', 'Dekabr']
                months_names = month_names_ru if lang == 'ru' else month_names_uz
                payoff_str = f"{months_names[payoff_date.month]} {payoff_date.year}"
                lines.append(f"  ⏱ {'Закроешь через' if lang == 'ru' else 'Yopishga'}: ~{payoff_months} мес. ({payoff_str})")
            else:
                lines.append(f"  ⚠️ {'Платёж не покрывает проценты!' if lang == 'ru' else 'To\'lov foizlarni qoplay olmaydi!'}")
            lines.append('')
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

async def cmd_reset(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
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

# Max photo size: 10MB
_MAX_PHOTO_SIZE = 10 * 1024 * 1024

# ────────────────────────── PHOTO HANDLER ─────────────────────────
async def on_photo(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = upd.effective_user.id
    lang = get_lang(uid)

    wait = await upd.message.reply_text(tx(lang, 'processing'), parse_mode='Markdown')
    try:
        photo     = upd.message.photo[-1]
        # FIX #8: Check photo file size before downloading
        if photo.file_size > _MAX_PHOTO_SIZE:
            await upd.message.reply_text(
                '❌ Фото слишком большое (макс. 10MB). Попробуй меньшее фото.' if lang == 'ru'
                else "❌ Rasm juda katta (maks. 10MB). Kichikroq rasm yuboring.",
                parse_mode='Markdown'
            )
            return
        # FIX: Rate limit check before AI call
        if not _check_rate_limit(uid):
            await upd.message.reply_text(
                '⏳ Слишком много запросов. Подожди минуту.' if lang == 'ru'
                else "⏳ Ko'p so'rovlar. Bir daqiqa kuting.",
                parse_mode='Markdown'
            )
            return

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

    rates = await asyncio.to_thread(get_rates)
    card_text = fmt_confirm_card(parsed, lang, rates)
    kb = [[
        InlineKeyboardButton(tx(lang, 'confirm_correct'), callback_data='tx_confirm'),
        InlineKeyboardButton(tx(lang, 'confirm_edit'), callback_data='tx_edit'),
        InlineKeyboardButton(tx(lang, 'confirm_cancel'), callback_data='tx_cancel'),
    ]]
    
    # FIX #5: Add creation timestamp for TTL check
    parsed['_created_at'] = time.time()
    ctx.user_data['pending_tx'] = parsed
    await upd.message.reply_text(card_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

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

    if state == STATE_BUG_REPORT:
        await handle_bug_report(upd, ctx, text_result, is_voice=True)
        return

    if state in ONBOARDING_TEXT_STATES:
        await upd.message.reply_text(f'🎤 _{text_result}_', parse_mode='Markdown')
        await _process_text_input(uid, upd.effective_chat.id, text_result, lang, state, upd, ctx)
        return

    # FIX: Rate limit check before AI call
    if not _check_rate_limit(uid):
        await upd.message.reply_text(
            '⏳ Слишком много запросов. Подожди минуту.' if lang == 'ru'
            else "⏳ Ko'p so'rovlar. Bir daqiqa kuting.",
            parse_mode='Markdown'
        )
        return

    wait2  = await upd.message.reply_text(f'🎤 _{text_result}_\n\n{tx(lang, "processing")}', parse_mode='Markdown')
    parsed = await ai_parse(text_result)
    try: await wait2.delete()
    except: pass

    if not parsed or 'type' not in parsed or 'amount' not in parsed:
        await upd.message.reply_text(tx(lang, 'parse_error'), parse_mode='Markdown')
        return

    rates = await asyncio.to_thread(get_rates)
    card_text = fmt_confirm_card(parsed, lang, rates)
    kb = [[
        InlineKeyboardButton(tx(lang, 'confirm_correct'), callback_data='tx_confirm'),
        InlineKeyboardButton(tx(lang, 'confirm_edit'), callback_data='tx_edit'),
        InlineKeyboardButton(tx(lang, 'confirm_cancel'), callback_data='tx_cancel'),
    ]]
    
    ctx.user_data['pending_tx'] = parsed
    await upd.message.reply_text(card_text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

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

# ────────────────────────── FLASK DASHBOARD ───────────────────────
flask_app = Flask(__name__, template_folder='templates')
_flask_secret = os.getenv('FLASK_SECRET_KEY', '')
if not _flask_secret:
    import secrets as _secrets
    _flask_secret = _secrets.token_hex(32)
    logger.warning('FLASK_SECRET_KEY not set — generated random key, sessions reset on restart!')
flask_app.secret_key = _flask_secret

def _verify_telegram_webapp(init_data: str) -> int | None:
    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))
        check_hash = parsed.pop('hash', None)
        if not check_hash:
            return None

        data_check_arr = [f'{k}={v}' for k, v in sorted(parsed.items())]
        data_check_string = '\n'.join(data_check_arr)

        secret_key = hmac.new(
            b'WebAppData',
            BOT_TOKEN.encode(),
            digestmod=hashlib.sha256
        ).digest()
        computed = hmac.new(secret_key, data_check_string.encode(), digestmod=hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed, check_hash):
            return None

        auth_date = int(parsed.get('auth_date', 0))
        if datetime.now().timestamp() - auth_date > 3600:
            return None

        user_data = json.loads(parsed.get('user', '{}'))
        uid = user_data.get('id')
        if not isinstance(uid, int):
            return None
        return uid
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
        app.job_queue.run_repeating(send_daily_notifications, interval=60, first=10)

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
    app.add_handler(CommandHandler('goal',    cmd_goal))
    app.add_handler(CommandHandler('budget',   cmd_budget))
    app.add_handler(CommandHandler('bug',      cmd_bug))
    app.add_handler(CommandHandler('help',     cmd_help))
    app.add_handler(CommandHandler('debts',   cmd_debts))
    app.add_handler(CommandHandler('clear',    cmd_clear))
    app.add_handler(CommandHandler('reset',    cmd_reset))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    app.add_handler(MessageHandler(filters.PHOTO, on_photo))
    app.add_handler(MessageHandler(filters.VOICE, on_voice))
    app.add_handler(CallbackQueryHandler(on_callback))

    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
