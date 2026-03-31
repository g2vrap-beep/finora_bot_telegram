#!/usr/bin/env python3
"""
💎 Moliya Bot — Shaxsiy moliyaviy yordamchi / Личный финансовый помощник
Telegram bot: голос + фото чека + текст → учёт финансов + AI советы
"""

import os, json, sqlite3, logging, tempfile, base64
from datetime import datetime
from pathlib import Path

import requests
from openai import OpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes
)

try:
    import speech_recognition as sr
    from pydub import AudioSegment
    VOICE_OK = True
except Exception:
    VOICE_OK = False

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN       = os.getenv('BOT_TOKEN', '')
OPENROUTER_KEY  = os.getenv('OPENROUTER_KEY', '')
DB_PATH         = os.getenv('DB_PATH', '/data/finance.db')
# Модель — можно менять на любую из openrouter.ai/models
OR_MODEL        = os.getenv('OR_MODEL', 'anthropic/claude-sonnet-4-5')

client = OpenAI(
    api_key=OPENROUTER_KEY,
    base_url='https://openrouter.ai/api/v1',
    default_headers={
        'HTTP-Referer': 'https://moliya-bot.app',
        'X-Title': 'Moliya Finance Bot',
    }
)

# ────────────────────────── TRANSLATIONS ──────────────────────────
T = {
    'ru': {
        'choose_lang'    : '👋 Выбери язык / Tilni tanlang:',
        'welcome'        : ('💎 *Молия* — твой финансовый помощник!\n\n'
                           '📝 Напиши: _«Потратил 50 000 сум на обед»_\n'
                           '🎤 Или скажи голосом\n'
                           '📷 Или отправь фото чека\n\n'
                           'Я запишу всё, покажу статистику и дам совет куда вложить деньги 💡\n\n'
                           '/help — все команды'),
        'processing'     : '⏳ Обрабатываю...',
        'added'          : '✅ Записано!',
        'type_inc'       : '📈 Доход',
        'type_exp'       : '📉 Расход',
        'no_data'        : '📭 Записей пока нет. Начни с любой траты!',
        'voice_error'    : '❌ Не удалось распознать голос. Попробуй ещё раз или напиши текстом.',
        'photo_error'    : '❌ Не удалось прочитать чек. Попробуй более чёткое фото.',
        'parse_error'    : '🤔 Не понял. Попробуй: _«Потратил 50000 сум на такси»_',
        'advice_wait'    : '🤔 Анализирую твои финансы...',
        'confirm_clear'  : '⚠️ Удалить *все* данные?',
        'cleared'        : '🗑 Данные удалены.',
        'cancelled'      : '❌ Отменено.',
        'rate_err'       : '❌ Не удалось получить курс.',
        'help_text'      : ('📌 *Как пользоваться Молия:*\n\n'
                           '*Добавить запись:*\n'
                           '• Напиши текст: _«Купил продукты на 35 000»_\n'
                           '• 🎤 Голосом то же самое\n'
                           '• 📷 Фото чека/квитанции\n\n'
                           '*Команды:*\n'
                           '/stats — 📊 Статистика\n'
                           '/history — 📋 Последние записи\n'
                           '/advice — 🤖 AI-совет\n'
                           '/rate — 💱 Курс валют (ЦБ РУз)\n'
                           '/clear — 🗑 Очистить данные\n'
                           '/lang — 🌐 Сменить язык'),
        'yes_del'        : '🗑 Да, удалить',
        'no_cancel'      : '← Отмена',
        'stats_hdr'      : '📊 *Финансовый отчёт*',
        'all_time'       : 'Всё время',
        'this_month'     : 'Этот месяц',
        'income_lbl'     : '📈 Доходы',
        'expense_lbl'    : '📉 Расходы',
        'balance_lbl'    : 'Баланс',
        'records_lbl'    : 'Записей всего',
        'hist_hdr'       : '📋 *Последние 10 записей*',
        'rate_hdr'       : '💱 *Курс ЦБ Узбекистана*',
        'advice_hdr'     : '🤖 *Молия советует:*',
        'updated'        : 'Обновлено',
    },
    'uz': {
        'choose_lang'    : '👋 Выбери язык / Tilni tanlang:',
        'welcome'        : ('💎 *Moliya* — sizning moliyaviy yordamchingiz!\n\n'
                           '📝 Yozing: _«50 000 so\'m ovqatga sarfladim»_\n'
                           '🎤 Yoki ovoz bilan ayting\n'
                           '📷 Yoki chek rasmini yuboring\n\n'
                           'Men hammasini yozaman, statistika ko\'rsataman va pul qo\'yish bo\'yicha maslahat beraman 💡\n\n'
                           '/help — barcha buyruqlar'),
        'processing'     : '⏳ Ishlamoqda...',
        'added'          : '✅ Yozib olindi!',
        'type_inc'       : '📈 Daromad',
        'type_exp'       : '📉 Xarajat',
        'no_data'        : '📭 Hali yozuv yo\'q. Birinchi xarajatingizni kiriting!',
        'voice_error'    : '❌ Ovozni tanib bo\'lmadi. Qayta urinib ko\'ring yoki matn yuboring.',
        'photo_error'    : '❌ Chekni o\'qib bo\'lmadi. Aniqroq rasm yuborib ko\'ring.',
        'parse_error'    : '🤔 Tushunmadim. Masalan: _«50000 so\'m taksi uchun sarfladim»_',
        'advice_wait'    : '🤔 Moliyangizni tahlil qilyapman...',
        'confirm_clear'  : '⚠️ *Barcha* ma\'lumotlarni o\'chirasizmi?',
        'cleared'        : '🗑 Ma\'lumotlar o\'chirildi.',
        'cancelled'      : '❌ Bekor qilindi.',
        'rate_err'       : '❌ Kursni olishning iloji bo\'lmadi.',
        'help_text'      : ('📌 *Moliyadan qanday foydalanish:*\n\n'
                           '*Yozuv qo\'shish:*\n'
                           '• Matn: _«35 000 ga oziq-ovqat sotib oldim»_\n'
                           '• 🎤 Ovoz bilan\n'
                           '• 📷 Chek rasmi\n\n'
                           '*Buyruqlar:*\n'
                           '/stats — 📊 Statistika\n'
                           '/history — 📋 Oxirgi yozuvlar\n'
                           '/advice — 🤖 AI maslahat\n'
                           '/rate — 💱 Valyuta kursi (O\'MBi)\n'
                           '/clear — 🗑 Ma\'lumotlarni o\'chirish\n'
                           '/lang — 🌐 Tilni o\'zgartirish'),
        'yes_del'        : '🗑 Ha, o\'chir',
        'no_cancel'      : '← Bekor qilish',
        'stats_hdr'      : '📊 *Moliyaviy hisobot*',
        'all_time'       : 'Jami',
        'this_month'     : 'Bu oy',
        'income_lbl'     : '📈 Daromad',
        'expense_lbl'    : '📉 Xarajat',
        'balance_lbl'    : 'Balans',
        'records_lbl'    : 'Jami yozuvlar',
        'hist_hdr'       : '📋 *Oxirgi 10 ta yozuv*',
        'rate_hdr'       : '💱 *O\'zbekiston MBi kursi*',
        'advice_hdr'     : '🤖 *Moliya maslahat beradi:*',
        'updated'        : 'Yangilandi',
    }
}

def tx(uid_or_lang: str | int, key: str) -> str:
    lang = uid_or_lang if uid_or_lang in ('ru', 'uz') else get_lang(uid_or_lang)
    return T.get(lang, T['ru']).get(key, key)

# ────────────────────────── DATABASE ──────────────────────────────
def init_db():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS transactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, type TEXT, amount REAL,
            description TEXT, category TEXT,
            currency TEXT DEFAULT 'UZS',
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS users(
            user_id INTEGER PRIMARY KEY,
            language TEXT DEFAULT 'ru'
        )''')

def get_lang(uid: int) -> str:
    with sqlite3.connect(DB_PATH) as c:
        c.execute('INSERT OR IGNORE INTO users(user_id,language) VALUES(?,?)', (uid,'ru'))
        r = c.execute('SELECT language FROM users WHERE user_id=?', (uid,)).fetchone()
    return r[0] if r else 'ru'

def set_lang(uid: int, lang: str):
    with sqlite3.connect(DB_PATH) as c:
        c.execute('INSERT OR REPLACE INTO users(user_id,language) VALUES(?,?)', (uid,lang))

def add_tx(uid: int, type_: str, amount: float, desc: str, cat: str, cur: str = 'UZS'):
    with sqlite3.connect(DB_PATH) as c:
        c.execute('INSERT INTO transactions(user_id,type,amount,description,category,currency) VALUES(?,?,?,?,?,?)',
                  (uid, type_, amount, desc, cat, cur))

def get_stats(uid: int) -> dict:
    with sqlite3.connect(DB_PATH) as c:
        rows = c.execute('SELECT type,SUM(amount),COUNT(*) FROM transactions WHERE user_id=? GROUP BY type',(uid,)).fetchall()
        month = datetime.now().strftime('%Y-%m')
        mrows = c.execute("SELECT type,SUM(amount) FROM transactions WHERE user_id=? AND strftime('%Y-%m',created_at)=? GROUP BY type",(uid,month)).fetchall()
        cnt = c.execute('SELECT COUNT(*) FROM transactions WHERE user_id=?',(uid,)).fetchone()[0]
    s = {'inc':0,'exp':0,'count':cnt,'m_inc':0,'m_exp':0}
    for r in rows:
        s['inc' if r[0]=='inc' else 'exp'] = r[1] or 0
    for r in mrows:
        s['m_inc' if r[0]=='inc' else 'm_exp'] = r[1] or 0
    return s

def get_history(uid: int, limit=10):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute('SELECT type,amount,description,category,currency,created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?',(uid,limit)).fetchall()

def get_recent(uid: int, limit=30):
    with sqlite3.connect(DB_PATH) as c:
        return c.execute('SELECT type,amount,description,category,created_at FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?',(uid,limit)).fetchall()

def clear_data(uid: int):
    with sqlite3.connect(DB_PATH) as c:
        c.execute('DELETE FROM transactions WHERE user_id=?',(uid,))

# ────────────────────────── CURRENCY ──────────────────────────────
def get_rates() -> dict:
    try:
        data = requests.get('https://cbu.uz/oz/arkhiv-kursov-valyut/json/', timeout=8).json()
        return {d['Ccy']: {'rate': float(d['Rate']), 'diff': float(d.get('Diff',0))}
                for d in data if d.get('Ccy') in ('USD','EUR','RUB')}
    except:
        return {}

def uzs(n: float) -> str:
    return f"{n:,.0f} so'm".replace(',', ' ')

def usd_equiv(n_uzs: float, rates: dict) -> str:
    r = rates.get('USD', {}).get('rate', 0)
    return f' ≈ ${n_uzs/r:.2f}' if r else ''

# ────────────────────────── AI ────────────────────────────────────
_PARSE_SYS = """Parse a financial transaction from Russian or Uzbek text.
Return ONLY valid JSON, no markdown fences:
{"type":"exp","amount":50000,"description":"short name","category":"🍔 Еда","currency":"UZS"}
type: "inc" or "exp". currency: "USD" if dollars mentioned, else "UZS".
Expense categories: 🍔 Еда, 🚗 Транспорт, 🏠 Жильё, 💊 Здоровье, 👗 Одежда, 🎮 Развлечения, 📱 Связь, 🛒 Магазин, 💡 Коммуналка, 📚 Образование, ❓ Другое
Income categories: 💰 Зарплата, 🤝 Фриланс, 📈 Инвестиции, 💼 Бизнес, 🎁 Подарок, ❓ Другое"""

_PHOTO_SYS = """Read this receipt or financial document image.
Return ONLY valid JSON, no markdown:
{"type":"exp","amount":50000,"description":"Store name","category":"🛒 Магазин","currency":"UZS","items":["item - price"]}
currency: "USD" if dollar amounts, else "UZS"."""

_ADVICE_SYS = {
    'ru': ('Ты — Молия, личный финансовый советник для жителя Узбекистана. '
           'Говоришь по-русски. Отвечаешь кратко, практично, с конкретными числами. '
           'Советуешь реальные инструменты доступные в Узбекистане: '
           'депозиты в сумах/долларах (Kapitalbank, Hamkorbank), '
           'Uzbek Stock Exchange (UZSE), золото, недвижимость, бизнес-идеи. '
           'Используй **жирный** и эмодзи для читаемости.'),
    'uz': ('Siz — Moliya, O\'zbekistondagi shaxs uchun shaxsiy moliyaviy maslahatchi. '
           'O\'zbekcha gapirasiz. Qisqa, amaliy, aniq raqamlar bilan javob bering. '
           'O\'zbekistonda mavjud real vositalarni maslahat bering: '
           'so\'m/dollar depozitlar (Kapitalbank, Hamkorbank), '
           'O\'zbekiston Fond Birjasi (UZSE), oltin, ko\'chmas mulk, biznes. '
           'O\'qilishi uchun **qalin** va emoji ishlating.'),
}

def _chat(system: str, user_content, max_tokens=300) -> str:
    """Universal OpenRouter call — works for text and vision."""
    r = client.chat.completions.create(
        model=OR_MODEL,
        max_tokens=max_tokens,
        messages=[
            {'role': 'system', 'content': system},
            {'role': 'user',   'content': user_content},
        ]
    )
    return r.choices[0].message.content.strip()

async def ai_parse(text: str) -> dict | None:
    try:
        raw = _chat(_PARSE_SYS, text, 200)
        raw = raw.replace('```json','').replace('```','').strip()
        return json.loads(raw)
    except:
        return None

async def ai_parse_photo(img_bytes: bytes, mime: str) -> dict | None:
    try:
        b64 = base64.b64encode(img_bytes).decode()
        content = [
            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{b64}'}},
            {'type': 'text', 'text': 'Read this receipt'},
        ]
        raw = _chat(_PHOTO_SYS, content, 300)
        raw = raw.replace('```json','').replace('```','').strip()
        return json.loads(raw)
    except:
        return None

async def ai_advice(uid: int, lang: str) -> str:
    rows  = get_recent(uid)
    stats = get_stats(uid)
    rates = get_rates()
    usd_r = rates.get('USD',{}).get('rate',0)

    prompt = (f"Данные:\n"
              f"Доходы всего: {uzs(stats['inc'])}{usd_equiv(stats['inc'],rates)}\n"
              f"Расходы всего: {uzs(stats['exp'])}{usd_equiv(stats['exp'],rates)}\n"
              f"Баланс: {uzs(stats['inc']-stats['exp'])}{usd_equiv(stats['inc']-stats['exp'],rates)}\n"
              f"Этот месяц — доходы: {uzs(stats['m_inc'])}, расходы: {uzs(stats['m_exp'])}\n"
              f"Курс USD: {uzs(usd_r) if usd_r else 'н/д'}\n\n"
              f"Последние транзакции:\n" +
              '\n'.join(f"{'➕' if r[0]=='inc' else '➖'} {uzs(r[1])} — {r[2]} ({r[3]})" for r in rows[:20]))
    try:
        return _chat(_ADVICE_SYS[lang], prompt, 700)
    except:
        return '❌ Ошибка.' if lang=='ru' else '❌ Xatolik.'

# ────────────────────────── VOICE ─────────────────────────────────
async def transcribe(ogg_path: str, lang: str) -> str | None:
    if not VOICE_OK:
        return None
    try:
        wav = ogg_path.replace('.ogg','.wav')
        AudioSegment.from_ogg(ogg_path).export(wav, format='wav')
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav) as src:
            audio = recognizer.record(src)
        Path(wav).unlink(missing_ok=True)
        lang_code = 'ru-RU' if lang=='ru' else 'uz-UZ'
        return recognizer.recognize_google(audio, language=lang_code)
    except:
        return None

# ────────────────────────── FORMATTERS ────────────────────────────
def fmt_tx_msg(parsed: dict, lang: str, rates: dict) -> str:
    cur  = parsed.get('currency','UZS')
    amt  = parsed['amount']
    if cur == 'USD':
        r = rates.get('USD',{}).get('rate',0)
        amt_str = f"${amt:,.2f}" + (f" ({uzs(amt*r)})" if r else '')
    else:
        amt_str = uzs(amt) + usd_equiv(amt, rates)

    sign  = '+' if parsed['type']=='inc' else '-'
    label = tx(lang,'type_inc') if parsed['type']=='inc' else tx(lang,'type_exp')
    return (f"{tx(lang,'added')}\n\n"
            f"{label}: `{sign}{amt_str}`\n"
            f"📝 {parsed.get('description','')}\n"
            f"🏷 {parsed.get('category','')}")

# ────────────────────────── HANDLERS ──────────────────────────────
async def cmd_start(upd: Update, _):
    kb = [[InlineKeyboardButton('🇷🇺 Русский',callback_data='lang_ru'),
           InlineKeyboardButton('🇺🇿 O\'zbek', callback_data='lang_uz')]]
    await upd.message.reply_text(T['ru']['choose_lang'], reply_markup=InlineKeyboardMarkup(kb))

async def cmd_help(upd: Update, _):
    uid = upd.effective_user.id
    await upd.message.reply_text(tx(uid,'help_text'), parse_mode='Markdown')

async def cmd_lang(upd: Update, _):
    kb = [[InlineKeyboardButton('🇷🇺 Русский',callback_data='lang_ru'),
           InlineKeyboardButton('🇺🇿 O\'zbek', callback_data='lang_uz')]]
    await upd.message.reply_text(T['ru']['choose_lang'], reply_markup=InlineKeyboardMarkup(kb))

async def cmd_stats(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    s    = get_stats(uid)
    if s['count'] == 0:
        await upd.message.reply_text(tx(lang,'no_data')); return

    rates = get_rates()
    def line(n): return f'`{uzs(n)}{usd_equiv(n,rates)}`'
    bal   = s['inc'] - s['exp']
    icon  = '✅' if bal >= 0 else '⚠️'

    msg = (f"{tx(lang,'stats_hdr')}\n\n"
           f"*{tx(lang,'all_time')}*\n"
           f"{tx(lang,'income_lbl')}: {line(s['inc'])}\n"
           f"{tx(lang,'expense_lbl')}: {line(s['exp'])}\n"
           f"{icon} {tx(lang,'balance_lbl')}: {line(bal)}\n\n"
           f"*{tx(lang,'this_month')}*\n"
           f"{tx(lang,'income_lbl')}: {line(s['m_inc'])}\n"
           f"{tx(lang,'expense_lbl')}: {line(s['m_exp'])}\n\n"
           f"📋 {tx(lang,'records_lbl')}: {s['count']}")
    await upd.message.reply_text(msg, parse_mode='Markdown')

async def cmd_history(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    rows = get_history(uid)
    if not rows:
        await upd.message.reply_text(tx(lang,'no_data')); return

    lines = [f"{tx(lang,'hist_hdr')}\n"]
    for type_,amount,desc,cat,cur,dt in rows:
        sign  = '➕' if type_=='inc' else '➖'
        cur   = cur or 'UZS'
        amt_s = f"${amount:,.2f}" if cur=='USD' else uzs(amount)
        date  = (dt or '')[:10]
        lines.append(f"{sign} `{amt_s}` — {desc or cat}\n   _{cat}_ · {date}\n")
    await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')

async def cmd_rate(upd: Update, _):
    uid   = upd.effective_user.id
    lang  = get_lang(uid)
    rates = get_rates()
    if not rates:
        await upd.message.reply_text(tx(lang,'rate_err')); return

    lines = [f"{tx(lang,'rate_hdr')}\n"]
    for ccy, d in rates.items():
        arrow = '🔺' if d['diff']>0 else ('🔻' if d['diff']<0 else '➡️')
        lines.append(f"{arrow} *{ccy}* = `{uzs(d['rate'])}` ({d['diff']:+.2f})")
    lines.append(f"\n_{tx(lang,'updated')}: {datetime.now().strftime('%d.%m %H:%M')}_")
    await upd.message.reply_text('\n'.join(lines), parse_mode='Markdown')

async def cmd_advice(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    msg  = await upd.message.reply_text(tx(lang,'advice_wait'))
    text = await ai_advice(uid, lang)
    text_md = text.replace('**','*')
    await msg.edit_text(f"{tx(lang,'advice_hdr')}\n\n{text_md}", parse_mode='Markdown')

async def cmd_clear(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    kb   = [[InlineKeyboardButton(tx(lang,'yes_del'),  callback_data='clear_yes'),
             InlineKeyboardButton(tx(lang,'no_cancel'), callback_data='clear_no')]]
    await upd.message.reply_text(tx(lang,'confirm_clear'),
                                  reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def on_text(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    text = upd.message.text.strip()
    if text.startswith('/'): return

    msg    = await upd.message.reply_text(tx(lang,'processing'))
    parsed = await ai_parse(text)
    if not parsed or 'amount' not in parsed:
        await msg.edit_text(tx(lang,'parse_error'), parse_mode='Markdown'); return

    rates = get_rates()
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description',''), parsed.get('category','❓'), parsed.get('currency','UZS'))
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')

async def on_photo(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    msg  = await upd.message.reply_text(tx(lang,'processing'))

    photo = upd.message.photo[-1]
    file  = await photo.get_file()
    data  = bytes(await file.download_as_bytearray())

    parsed = await ai_parse_photo(data, 'image/jpeg')
    if not parsed or 'amount' not in parsed:
        await msg.edit_text(tx(lang,'photo_error')); return

    rates = get_rates()
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description',''), parsed.get('category','🛒'), parsed.get('currency','UZS'))

    extra = ''
    if parsed.get('items'):
        extra = '\n\n📄 ' + '\n'.join(parsed['items'][:5])
    await msg.edit_text(fmt_tx_msg(parsed, lang, rates) + extra, parse_mode='Markdown')

async def on_voice(upd: Update, _):
    uid  = upd.effective_user.id
    lang = get_lang(uid)
    msg  = await upd.message.reply_text(tx(lang,'processing'))

    vfile = await upd.message.voice.get_file()
    with tempfile.NamedTemporaryFile(suffix='.ogg', delete=False) as f:
        await vfile.download_to_drive(f.name)
        ogg = f.name

    transcript = await transcribe(ogg, lang)
    Path(ogg).unlink(missing_ok=True)

    if not transcript:
        await msg.edit_text(tx(lang,'voice_error')); return

    parsed = await ai_parse(transcript)
    if not parsed or 'amount' not in parsed:
        await msg.edit_text(f'🎤 _{transcript}_\n\n{tx(lang,"parse_error")}', parse_mode='Markdown'); return

    rates = get_rates()
    add_tx(uid, parsed['type'], parsed['amount'],
           parsed.get('description',''), parsed.get('category','❓'), parsed.get('currency','UZS'))
    await msg.edit_text(f'🎤 _{transcript}_\n\n' + fmt_tx_msg(parsed, lang, rates), parse_mode='Markdown')

async def on_callback(upd: Update, _):
    q    = upd.callback_query
    uid  = q.from_user.id
    data = q.data
    await q.answer()

    if data.startswith('lang_'):
        lang = data[5:]
        set_lang(uid, lang)
        await q.edit_message_text(T[lang]['welcome'], parse_mode='Markdown')

    elif data == 'clear_yes':
        lang = get_lang(uid)
        clear_data(uid)
        await q.edit_message_text(tx(lang,'cleared'))

    elif data == 'clear_no':
        lang = get_lang(uid)
        await q.edit_message_text(tx(lang,'cancelled'))

# ────────────────────────── MAIN ──────────────────────────────────
def main():
    init_db()
    if not BOT_TOKEN:      raise ValueError('BOT_TOKEN not set')
    if not OPENROUTER_KEY: raise ValueError('OPENROUTER_KEY not set')

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler('start',   cmd_start))
    app.add_handler(CommandHandler('help',    cmd_help))
    app.add_handler(CommandHandler('lang',    cmd_lang))
    app.add_handler(CommandHandler('stats',   cmd_stats))
    app.add_handler(CommandHandler('history', cmd_history))
    app.add_handler(CommandHandler('rate',    cmd_rate))
    app.add_handler(CommandHandler('advice',  cmd_advice))
    app.add_handler(CommandHandler('clear',   cmd_clear))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.PHOTO,  on_photo))
    app.add_handler(MessageHandler(filters.VOICE,  on_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    logger.info('🚀 Moliya Bot is running!')
    app.run_polling(drop_pending_updates=True)

if __name__ == '__main__':
    main()
